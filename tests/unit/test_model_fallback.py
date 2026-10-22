import json
import subprocess
from types import SimpleNamespace

import pytest

from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.adapters.model_probe import RelayModelProbe
from ontology_proj.adapters.agent_provider import RelayAgentProvider
from ontology_proj.adapters.answer_generator import RelayAnswerGenerator
from ontology_proj.settings import Settings


def fake_relay(monkeypatch, replies):
    calls = []
    def run(command, **kwargs):
        line = next(line for line in kwargs['input'].splitlines() if line.startswith('data = '))
        calls.append(json.loads(json.loads(line[7:])))
        response = replies[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        code, body, returncode = response
        return SimpleNamespace(stdout=body + '\n' + str(code), stderr='', returncode=returncode)
    monkeypatch.setattr('ontology_proj.adapters.fact_extractor.subprocess.run', run)
    return calls


def success(content='{}', finish='stop'):
    return (200, json.dumps({'model': 'glm-5.3-flash', 'choices': [
        {'finish_reason': finish, 'message': {'content': content}}]}), 0)


@pytest.mark.parametrize('failure', [
    (404, 'missing', 0), (408, 'timeout', 0), (429, 'rate limited', 0),
    (500, 'failure', 0), (503, 'PRIVATE-KEY unavailable', 0),
    (0, '', 7), (0, '', 28), subprocess.TimeoutExpired('curl', 185),
])
def test_availability_fallback_preserves_payload_and_attempts(monkeypatch, failure):
    calls = fake_relay(monkeypatch, [failure, success()])
    provider = RelayFactExtractor('http://localhost/v1', 'deepseek', 'PRIVATE-KEY', ['glm-5.3-flash'])
    request = {'model': 'deepseek', 'messages': [{'role': 'user', 'content': 'test'}], 'temperature': 0}
    result = provider.extract(request)
    assert [c['model'] for c in calls] == ['deepseek', 'glm-5.3-flash']
    assert calls[0] == request and calls[1] == {**request, 'model': 'glm-5.3-flash'}
    assert request['model'] == 'deepseek'
    assert result['candidate'] == {}
    assert len(result['response']['relay_attempts']) == 2
    assert result['response']['relay_requested_model'] == 'glm-5.3-flash'
    assert 'PRIVATE-KEY' not in json.dumps(result)


@pytest.mark.parametrize('response', [
    (400, 'bad params', 0), (401, 'auth', 0), (403, 'forbidden', 0),
    (422, 'invalid params', 0), (200, 'not json', 0), (200, '[]', 0), (200, 'null', 0),
    success('invalid candidate'), success('{}', 'length'), success(),
    (0, '', 60),
])
def test_non_availability_errors_and_success_do_not_fallback(monkeypatch, response):
    calls = fake_relay(monkeypatch, [response])
    provider = RelayFactExtractor('http://localhost/v1', 'primary', 'key', ['backup'])
    provider.extract({})
    assert len(calls) == 1


def test_exhaustion_deduplication_and_disabled_fallback(monkeypatch):
    calls = fake_relay(monkeypatch, [(503, 'unavailable', 0)] * 2)
    provider = RelayFactExtractor('http://localhost/v1', 'primary', 'key', ['primary', 'backup', 'backup'])
    result = provider.extract({})
    assert len(calls) == 2 and result['error_code'] == 'MODEL_TRANSPORT_FAILED'
    assert len(result['response']['relay_attempts']) == 2
    calls = fake_relay(monkeypatch, [(503, 'unavailable', 0)])
    result = RelayFactExtractor('http://localhost/v1', 'primary', 'key').extract({})
    assert len(calls) == 1 and 'relay_attempts' not in result['response']


def test_readiness_accepts_advertised_backup(monkeypatch):
    monkeypatch.setattr('ontology_proj.adapters.model_probe.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout='{"data":[{"id":"backup"}]}'))
    assert RelayModelProbe('http://localhost/v1', 'primary', 'key', ['backup']).check().state.value == 'ready'
    assert RelayModelProbe('http://localhost/v1', 'primary', 'key').check().state.value == 'unavailable'


def test_answer_and_agent_use_shared_fallback(monkeypatch):
    for factory in [RelayAnswerGenerator, RelayAgentProvider]:
        calls = fake_relay(monkeypatch, [(503, 'unavailable', 0), success()])
        provider = factory('http://localhost/v1', 'primary', 'key', ['backup'])
        if isinstance(provider, RelayAgentProvider):
            response = provider.complete(provider.request([], []))
        else:
            response = provider.generate(provider.request('question', {}))['response']
        assert len(calls) == 2 and response['relay_requested_model'] == 'backup'


def test_settings_and_bootstrap(tmp_path):
    from ontology_proj.bootstrap import build_extractor
    settings = Settings(data_root=tmp_path, llm_base_url='http://localhost/v1',
                        llm_model='primary', llm_api_key='key')
    assert settings.llm_fallback_models == ['glm-5.3-flash']
    extractor = build_extractor(settings)
    assert extractor.models == ('primary', 'glm-5.3-flash')
    assert extractor.configuration()['fallback_models'] == ['glm-5.3-flash']
    settings.llm_fallback_models = []
    assert build_extractor(settings).models == ('primary',)
    with pytest.raises(ValueError):
        Settings(llm_fallback_models=[' '])


def test_agent_failure_keeps_attempts(monkeypatch):
    from ontology_proj.application.files import FileStoreError
    fake_relay(monkeypatch, [(503, 'unavailable', 0)] * 2)
    provider = RelayAgentProvider('http://localhost/v1', 'primary', 'key', ['backup'])
    with pytest.raises(FileStoreError) as exc:
        provider.complete(provider.request([], []))
    assert len(exc.value.provider_response['relay_attempts']) == 2
