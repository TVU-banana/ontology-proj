"""Configured OpenAI-compatible relay; ordered fallback on model availability failures."""
import copy
import hashlib
from importlib.resources import files
import json
import re
import subprocess
from urllib.parse import urlsplit

from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.business_rules import load_rules


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


class RelayFactExtractor:
    def __init__(self, base_url, model, api_key, fallback_models=()):
        url = urlsplit(base_url)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password or url.query or url.fragment or not model or not api_key:
            raise FileStoreError('MODEL_NOT_CONFIGURED', 'Explicit relay configuration is invalid or missing')
        self.base, self.model, self._key = base_url.rstrip('/'), model, api_key
        self.models = tuple(dict.fromkeys([model, *fallback_models]))
        resources = files('ontology_proj.resources')
        self.prompt = resources.joinpath('extraction-prompt.txt').read_text()
        self.schema = json.loads(resources.joinpath('business.schema.json').read_text())
        self.rules = load_rules()
        self.prompt = self.prompt.replace('schema_version 固定 0.1.0-draft','schema_version 固定 0.3.0-draft')
        self.prompt += '\n规则配置（仅解释已支持表达；原文/来源不能改写）：'+canonical(self.rules.data)
        self.prompt += '\n新版每个Observation.data含area_expression。直接面积保留area_original_value/unit，area_expression=null；原文只有矩形明确长宽/单位或明确面积乘积时，area_expression完整复制包含依据的连续原句，area_original_value/unit=null。不要计算规范面积，不把乘积任一因子或结果当原始面积。缺单位、不规则、最大范围、否定/疑似/汇总保留issues/statements，不猜。area_expression和近似限定分别给同名原文source。identity_mode复制trusted_context。'

    def configuration(self):
        return {'configuration_version': '1.2', 'semantic_revision': '1.3', 'schema_version': '0.3.0-draft', 'identity_mode':'explicit', 'rules_version':self.rules.version, 'rules_sha256':self.rules.sha256, 'base_url': self.base, 'model': self.model, 'temperature': 0, 'max_tokens': 18000, 'thinking': {'type': 'disabled'}, 'stream': False, 'response_format': {'type': 'json_object'}, 'automatic_retries': 0, **({'fallback_models': list(self.models[1:]), 'fallback_policy': 'availability-v1'} if len(self.models) > 1 else {}), 'prompt_sha256': hashlib.sha256(self.prompt.encode()).hexdigest(), 'schema_sha256': hashlib.sha256(canonical(self.schema).encode()).hexdigest()}

    def request(self, document):
        needed, pending = {'CandidateBundle'}, ['CandidateBundle']
        while pending:
            for ref in re.findall(r'"\$ref":\s*"#/\$defs/([^"/]+)"', json.dumps(self.schema['$defs'][pending.pop()])):
                if ref not in needed: needed.add(ref); pending.append(ref)
        schema = {'$schema': self.schema['$schema'], '$ref': '#/$defs/CandidateBundle', '$defs': {k:copy.deepcopy(self.schema['$defs'][k]) for k in sorted(needed)}}
        config = self.configuration()
        schema['$defs']['CandidateBundle']['properties']['schema_version']={'const':config['schema_version']}
        schema['$defs']['CandidateBundle']['properties']['identity_mode']={'const':config['identity_mode']}
        # Express existing owner-specific semantic restrictions to the model.
        # This narrows the request schema; the primary contract is unchanged.
        for kind in ('Bridge', 'Component', 'Defect', 'Observation'):
            allowed = list(schema['$defs'][kind+'CandidateData']['required'])
            if kind=='Observation':allowed.append('area_expression')
            schema['$defs'][kind+'Candidate']['properties']['sources']['items'] = {'allOf': [{'$ref':'#/$defs/SourceProposal'}, {'properties': {'target_field': {'enum': allowed}}}]}
        for name, allowed in [('Statement', ['description','kind','location_text','component_ref']), ('RelationCandidate', ['bridge_id','component_id','defect_id','observation_id','bridge_code','component_code','defect_code','observation_code','name','location_text','description'])]:
            schema['$defs'][name]['properties']['sources']['items'] = {'allOf': [{'$ref':'#/$defs/SourceProposal'}, {'properties': {'target_field': {'enum': allowed}}}]}
        blocks = []
        for block in document['blocks']:
            item = {k:v for k,v in block.items() if k not in ('locations', 'location_status', 'span_basis')}
            item['page_no'] = block['locations'][0]['page_no'] if block['locations'] else None
            blocks.append(item)
        payload = {'trusted_context': {**{k: document[k] for k in ['document_id', 'source_kind']},'identity_mode':config['identity_mode']}, 'candidate_schema': schema, 'blocks_in_document_order': blocks, 'tables': document['tables'], 'parser_warnings': document['warnings']}
        config = self.configuration()
        request = {k: config[k] for k in ['model', 'temperature', 'max_tokens', 'thinking', 'stream', 'response_format']}
        request['messages'] = [{'role':'system', 'content':self.prompt}, {'role':'user', 'content':canonical(payload)}]
        return request

    def _scrub(self, value):
        if isinstance(value, str): return value.replace(self._key, '[REDACTED]')
        if isinstance(value, list): return [self._scrub(v) for v in value]
        if isinstance(value, dict): return {k:self._scrub(v) for k,v in value.items() if k.lower() not in ('api_key', 'authorization')}
        return value

    def extract(self, request):
        attempts = []
        for model in self.models:
            actual = {**request, 'model': model}
            result = self._extract_once(actual)
            retry = result.pop('_fallback_allowed', False)
            attempts.append({'requested_model': model, 'response': copy.deepcopy(result['response']),
                             'error_code': result.get('error_code')})
            if not retry:
                break
        if len(self.models) > 1:
            result['response'] = {**result['response'], 'relay_attempts': attempts,
                                  'relay_requested_model': model}
        return result

    def _extract_once(self, request):
        def quoted(text):
            return '"' + text.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n').replace('\r','\\r') + '"'
        config = '\n'.join(['url = '+quoted(self.base+'/chat/completions'), 'header = '+quoted('Authorization: Bearer '+self._key), 'header = "Content-Type: application/json"', 'data = '+quoted(json.dumps(request, ensure_ascii=False))])+'\n'
        response = {}
        fallback_allowed = False
        try:
            result = subprocess.run(['curl','--config','-','--silent','--show-error','--noproxy','*','--connect-timeout','5','--max-time','180','--request','POST','--write-out','\n%{http_code}'], input=config, text=True, capture_output=True, timeout=185)
            response = self._scrub({'raw_body_with_status': result.stdout, 'stderr': result.stderr, 'curl_returncode': result.returncode})
            if result.returncode:
                fallback_allowed = result.returncode in (5, 6, 7, 18, 28, 52, 55, 56)
                raise ValueError
            body, code = result.stdout.rsplit('\n',1)
            response = self._scrub({'raw_body': body, 'http_status': code})
            if not 200 <= int(code) < 300:
                fallback_allowed = int(code) in (404, 408, 429) or 500 <= int(code) < 600
                raise ValueError
            decoded = strict_json(body)
            if not isinstance(decoded, dict): raise ValueError
            response = self._scrub(decoded)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            fallback_allowed = fallback_allowed or isinstance(exc, subprocess.TimeoutExpired)
            return {'_fallback_allowed': fallback_allowed, 'response': response, 'candidate': None, 'error_code': 'MODEL_TRANSPORT_FAILED', 'diagnostics': [{'code':'MODEL_TRANSPORT_FAILED', 'severity':'reject', 'related_local_ids':[], 'message':'Relay request or response decoding failed'}]}
        candidate, diagnostics = None, []
        try:
            choice = response['choices'][0]
            if choice['finish_reason'] != 'stop': raise ValueError
            text = choice['message']['content']
            match = re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*', text, re.DOTALL)
            candidate = strict_json(match[1] if match else text)
        except (KeyError, IndexError, TypeError, ValueError):
            diagnostics.append({'code':'MODEL_OUTPUT_INVALID', 'severity':'reject', 'related_local_ids':[], 'message':'Response is truncated or not a single JSON candidate'})
        return {'response': response, 'candidate':candidate, 'diagnostics':diagnostics}
