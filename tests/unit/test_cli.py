import json
from pathlib import Path

import pytest

from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.adapters.local_processing_store import LocalProcessingStore
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.application.parsing import ParseOutcome, ParseStatus
from ontology_proj.local_cli import main

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / 'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf'


class SnapshotParser:
    """Explicit fixture, not a CLI-selectable parser or actual conversion."""
    calls = 0
    def configuration(self): return {'fixture': 't05'}
    def parse(self, path, document_id):
        self.calls += 1
        native = json.loads((ROOT / 'experiments/docling-parse-001/results/docling.json').read_text())
        return ParseOutcome(document_id, ParseStatus.SUCCESS, native, (), {'fixture': 'saved'}, 0)


def test_shared_cli_flow(tmp_path, capsys):
    parser = SnapshotParser()
    service = LocalParsingService(LocalProcessingStore(tmp_path), parser, DoclingNormalizer())
    def factory(settings):
        service.recover()
        return service
    def call(*args):
        code = main(['--data-root', str(tmp_path), *args], service_factory=factory)
        captured = capsys.readouterr()
        return code, json.loads(captured.out or captured.err)
    prepare = ['prepare', '--pdf', str(PDF), '--kind', 'inspection_report', '--source-kind', 'synthetic']
    code, result = call(*prepare)
    assert code == 0 and result['source']['context']['bridge_id'] is None
    docid = result['source']['document_id']
    assert call(*prepare)[1]['duplicate'] and parser.calls == 0
    assert call('read', '--document-id', docid)[1]['error']['code'] == 'ARTIFACT_NOT_GENERATED'
    assert call('parse', '--document-id', docid)[0] == 0
    assert call('parse', '--document-id', docid)[1]['reused']
    assert parser.calls == 1
    for artifact in ['manifest', 'normalized', 'docling']:
        assert call('read', '--document-id', docid, '--artifact', artifact)[0] == 0
    (tmp_path / 'documents' / docid / 'normalized.json').write_text('{}')
    code, result = call('read', '--document-id', docid)
    assert code == 1 and result['error']['code'] == 'ARTIFACT_MISMATCH'


@pytest.mark.parametrize('args', [
    [], ['prepare'], ['prepare', '--pdf', 'secret-token', '--kind', 'SECRET', '--source-kind', 'real'],
    ['read', '--document-id', 'doc_x', '--artifact', 'SECRET'], ['parse', '--document-id', 'x', '--unknown', 'SECRET'],
])
def test_argument_errors_do_not_echo_input(args, capsys):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out and 'SECRET' not in captured.err and 'secret-token' not in captured.err
    assert json.loads(captured.err)['error']['code'] == 'INVALID_ARGUMENT'


def test_settings_errors_do_not_echo_secrets(tmp_path, capsys):
    env = tmp_path / 'secret.env'
    env.write_text('APP_PORT=SECRET\nLLM_API_KEY=TOP-SECRET\n')
    assert main(['--env-file', str(env), 'read', '--document-id', 'x']) == 2
    assert 'SECRET' not in capsys.readouterr().err


def test_empty_bridge_rejected_before_store(tmp_path, capsys):
    assert main(['--data-root', str(tmp_path/'data'), 'prepare', '--pdf', str(PDF), '--kind', 'other', '--source-kind', 'real', '--bridge-id', '  ']) == 2
    assert not (tmp_path/'data').exists()
    assert json.loads(capsys.readouterr().err)['error']['code'] == 'INVALID_ARGUMENT'


def test_missing_pdf_sanitized(tmp_path, capsys):
    assert main(['prepare', '--pdf', str(tmp_path/'SECRET.pdf'), '--kind', 'other', '--source-kind', 'real']) == 1
    output = capsys.readouterr().err
    assert 'SECRET' not in output and json.loads(output)['error']['code'] == 'INPUT_UNAVAILABLE'
