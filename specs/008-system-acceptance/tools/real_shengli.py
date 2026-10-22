"""One-shot real PDF acceptance; no golden access on request/extraction paths."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from ontology_proj.settings import Settings
from ontology_proj.adapters.fact_extractor import RelayFactExtractor, canonical
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.application.business_extraction import ExtractBusiness
from ontology_proj.application.dto import ImportContext
from ontology_proj.domain.source import SourceKind, DocumentKind

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'specs/008-system-acceptance/results/real-shengli-20261012-01'
DATA = ROOT / 'data/s52-real-shengli-20261012-01'
SOURCE = ROOT / 'data/s52-local-shengli-20261012-01'
BAD_REF = '#/tables/13/data/table_cells/277'
API = 'http://relay.example.invalid:7863/v1'
MODEL = 'cn:deepseek-v4.1-flash'


def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('x') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def now():
    return datetime.now(timezone.utc).isoformat()


class CompactExtractor(RelayFactExtractor):
    def __init__(self, settings):
        super().__init__(settings.llm_base_url, settings.llm_model, settings.llm_api_key.get_secret_value())
        self.aliases = {}

    def configuration(self):
        return {**super().configuration(), 'acceptance_request_encoding': 'full-blocks-once-alias-1',
                'known_unusable_source_ref': BAD_REF, 'default_product_encoding': False}

    def request(self, document):
        base = super().request(document)
        original = json.loads(base['messages'][1]['content'])
        blocks = []
        self.aliases = {}
        for index, b in enumerate(original['blocks_in_document_order'], 1):
            alias = 'b' + str(index)
            self.aliases[alias] = b['block_id']
            item = {k: v for k, v in b.items() if k in (
                'text', 'original_text', 'kind', 'page_no', 'table_id', 'row', 'column',
                'row_span', 'column_span', 'column_header', 'row_header')}
            item['block_id'] = alias
            if b['source_ref'] == BAD_REF:
                item['evidence_use'] = 'unusable_do_not_cite'
            blocks.append(item)
        bad_alias = next(b['block_id'] for b in blocks if b.get('evidence_use'))
        tables = [{k: t[k] for k in ('table_id', 'page_no', 'num_rows', 'num_cols', 'header_semantics')}
                  for t in document['tables']]
        boundaries = [
            {'code': 'UNUSABLE_NUMERIC_SOURCE', 'block_id': bad_alias,
             'details': 'One numeric cell overlaps two PDF rows but declares one; previous row lacks this cell. Do not quote or use it for any entity, relation or statement. No replacement value supplied.'},
            {'code': 'UNTRUSTED_HEADERS_AND_CONTINUATION', 'pages': [34, 35],
             'tables': ['#/tables/12', '#/tables/13'],
             'details': 'Native column headers absent on34; first data row misflagged on35;8 vs7 columns. No verified cross-page column correspondence. Raw flags are not trusted.'},
            {'code': 'CONTINUATION_UNRESOLVED', 'pages': [18, 19], 'table_id': '#/tables/9',
             'details': 'Following page has detached paragraph. No verified row association; do not invent inherited component identity.'},
        ]
        payload = {'trusted_context': original['trusted_context'], 'candidate_schema': original['candidate_schema'],
                   'blocks_in_document_order': blocks, 'tables': tables,
                   'parser_warnings': original['parser_warnings'], 'quality_boundaries': boundaries,
                   'encoding': 'block_id is an opaque short alias. Copy it exactly in sources. All original block text included; table cells occur once in blocks. No PDF images/OCR or new trusted table joins.'}
        base['messages'][1]['content'] = canonical(payload)
        base['messages'][0]['content'] += '\n本次来源ID为短别名，逐字照抄。quality_boundaries优先于不可信原生表头/行列标记；evidence_use=unusable_do_not_cite的块不得作为任何实体、关系或statement来源，不输出其派生事实。未验证跨页关系不推断。保留所有其他原文的业务范围，不把请求当作仅三个观测的子集。'
        return base

    def extract(self, request):
        frozen = json.loads((OUT / 'freeze.json').read_text())
        assert hashlib.sha256(canonical(request).encode()).hexdigest() == frozen['request_canonical_sha256']
        for name, value in frozen['files'].items():
            assert sha(ROOT / name) == value
        # Claim before any network write. Never repeat a claimed extraction.
        write(DATA / 'extraction-call-claim.json', {'started_at': now(), 'maximum_calls': 1})
        started = time.monotonic()
        result = super().extract(request)
        write(DATA / 'raw-extraction-result.json', result)
        candidate = result.get('candidate')
        if candidate is not None:
            mapped = copy.deepcopy(candidate)
            invalid = []
            bad_ids = {alias for alias, bid in self.aliases.items()
                       if bid == json.loads((OUT / 'preparation.json').read_text())['unusable_block_id']}
            def visit(value):
                if isinstance(value, list):
                    for item in value: visit(item)
                elif isinstance(value, dict):
                    if 'block_id' in value:
                        alias = value['block_id']
                        if alias not in self.aliases or alias in bad_ids:
                            invalid.append(alias)
                        else:
                            value['block_id'] = self.aliases[alias]
                    for key, item in value.items():
                        if key != 'block_id': visit(item)
            visit(mapped)
            if invalid:
                # Keep first raw proposal intact; reject, never selectively fix facts.
                result.update(candidate=None, diagnostics=[{'code': 'SOURCE_ALIAS_OR_TRUST_INVALID',
                              'severity': 'reject', 'related_local_ids': [],
                              'message': 'Candidate cites unknown or unusable source'}])
            else:
                result['candidate'] = mapped
            write(DATA / 'source-id-mapping-result.json', {'changed_fields': 'block_id only',
                  'invalid_source_aliases': invalid, 'alias_count': len(self.aliases),
                  'business_values_modified': False})
        write(OUT / 'provider-result.json', {'elapsed_seconds': round(time.monotonic()-started, 3),
              'response_model': result.get('response', {}).get('model'),
              'usage': result.get('response', {}).get('usage'),
              'error_code': result.get('error_code'), 'diagnostics': result.get('diagnostics'),
              'finish_reason': (result.get('response', {}).get('choices') or [{}])[0].get('finish_reason'),
              'candidate_produced': result.get('candidate') is not None})
        return result


def prepare():
    assert not (OUT / 'preparation.json').exists()
    s = Settings(_env_file=ROOT / 'data/s23-runtime/.env.s32')
    assert s.llm_base_url.rstrip('/') == API and s.llm_model == MODEL
    assert s.embedding_base_url == 'http://127.0.0.1:11434'
    tracked = [ROOT / p for p in subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0') if p]
    local = []
    for name in ('s52-local-shengli-20261012-01', 's52-outbound-plan-20261012-01',
                 's52-proofread-20261012-01', 's52-table-trust-20261012-01', 's23-runtime', 's52-system-20261012-01'):
        local.extend(p for p in (ROOT / 'data' / name).rglob('*') if p.is_file() and not p.is_symlink()
                     and '__pycache__' not in p.parts)
    write(DATA / 'protected-files.json', {str(p.relative_to(ROOT)): sha(p) for p in tracked+local})
    document = json.loads((SOURCE / 'parsed/normalized.json').read_text())
    store = IngestionStore(DATA / 'runtime')
    context = ImportContext(kind=DocumentKind(document['import_context']['kind']), source_kind=SourceKind.REAL,
                            bridge_id=document['import_context']['bridge_id'])
    source = store.save_source((SOURCE / 'input/shengli-original.pdf').read_bytes(), 'shengli-original.pdf', context).source
    assert source.document_id == document['document_id']
    config = json.loads((ROOT / 'specs/008-system-acceptance/results/local-shengli-20261012-01/parser-result.json').read_text())
    op = {'operation_id': str(uuid4()), 'document_id': source.document_id, 'scope': 'local_parse',
          'status': 'running', 'stage': 'publishing', 'error_code': None, 'created_at': now(), 'updated_at': now(),
          'configuration': config['configuration'], 'artifact_keys': []}
    store.save_operation(op)
    store.save_artifact(source.document_id, 'docling', json.loads((SOURCE / 'parsed/docling.json').read_text()))
    store.save_artifact(source.document_id, 'normalized', document)
    store.publish(source.document_id, op['operation_id'], config['configuration'], config['versions'])
    op.update(status='succeeded_with_warnings', stage='published');store.save_operation(op)
    extractor = CompactExtractor(s)
    request = extractor.request(document)
    write(DATA / 'extraction-request.json', request)
    write(DATA / 'source-id-aliases.json', extractor.aliases)
    payload = json.loads(request['messages'][1]['content'])
    blocks = payload['blocks_in_document_order']
    assert len(blocks) == len(document['blocks']) == len(set(extractor.aliases.values()))
    assert len(payload['tables']) == len(document['tables'])
    for old, new in zip(document['blocks'], blocks):
        assert old['text'] == new['text']
        assert old.get('original_text') == new.get('original_text')
        assert extractor.aliases[new['block_id']] == old['block_id']
    assert len([b for b in blocks if b.get('evidence_use')]) == 1
    bad = next(b for b in document['blocks'] if b['source_ref'] == BAD_REF)
    assert bad['text'] == '1.23 1.40'
    raw = json.dumps(request, ensure_ascii=False)
    assert s.llm_api_key.get_secret_value() not in raw
    info = {'status': 'prepared_for_approved_api_no_call_yet', 'document_id': source.document_id,
            'destination': API + '/chat/completions', 'model': MODEL, 'upstream_revision': 'not_verified',
            'blocks': len(blocks), 'tables': len(payload['tables']), 'all_original_text_preserved': True,
            'request_canonical_bytes': len(canonical(request).encode()),
            'unusable_block_id': bad['block_id'], 'known_table_defects_repaired': False,
            'source_snapshot_reused_not_reparsed': True, 'generation_budget': 6, 'extraction_budget': 1,
            'embedding_batches_budget': 32, 'embedding_queries_budget': 5, 'automatic_retries': 0,
            'professional_review': 'not_run', 'baseline_head': subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()}
    write(OUT / 'preparation.json', info)
    frozen_files = [Path(__file__), OUT / 'execution.md', SOURCE / 'parsed/normalized.json',
                    SOURCE / 'parsed/docling.json', SOURCE / 'input/shengli-original.pdf', DATA / 'source-id-aliases.json']
    frozen_files += [p for p in (ROOT / 'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    write(OUT / 'freeze.json', {'files': {str(p.relative_to(ROOT)): sha(p) for p in frozen_files},
          'request_canonical_sha256': hashlib.sha256(canonical(request).encode()).hexdigest()})
    print(json.dumps(info, ensure_ascii=False))


def extract():
    assert not (OUT / 'extraction-result.json').exists()
    s = Settings(_env_file=ROOT / 'data/s23-runtime/.env.s32')
    assert s.llm_base_url.rstrip('/') == API and s.llm_model == MODEL
    store = IngestionStore(DATA / 'runtime');lease = ProcessLease(store);lease.acquire()
    try:
        service = ExtractBusiness(store, CompactExtractor(s), BusinessValidator())
        result = service.execute(json.loads((OUT / 'preparation.json').read_text())['document_id'])
        write(OUT / 'extraction-result.json', result)
        if result['status'] != 'succeeded':
            write(OUT / 'downstream-status.json', {'graph_ingestion': 'not_run', 'index': 'not_run',
                  'answers': 'not_run', 'report': 'not_run', 'reason': result.get('error_code'),
                  'automatic_retry': False, 'professional_review': 'not_run', 'full_s52_complete': False})
        print(json.dumps(result, ensure_ascii=False))
    finally:
        lease.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser();parser.add_argument('mode', choices=['prepare', 'extract'])
    args = parser.parse_args()
    prepare() if args.mode == 'prepare' else extract()
