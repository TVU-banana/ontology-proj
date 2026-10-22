"""Actual localhost HTTP -> Docling -> relay -> Neo4j; golden read afterwards."""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time

import httpx
from ontology_proj.bootstrap import build_graph
from ontology_proj.settings import Settings
from ontology_proj.adapters.ingestion_store import IngestionStore
from business_stability_compare import compare

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--env-file', type=Path, required=True)
    cli.add_argument('--output-dir', type=Path, required=True)
    cli.add_argument('--case',choices=['baseline','missing-area'],default='baseline')
    args = cli.parse_args()
    out = args.output_dir.resolve()
    if out.exists():
        cli.error('Use a new output directory; retain all first results')
    out.mkdir(parents=True)
    settings = Settings(_env_file=args.env_file)
    source_hashes={}
    for path in [*sorted((ROOT/'src/ontology_proj').rglob('*.py')), *sorted((ROOT/'src/ontology_proj/resources').glob('*.json')), ROOT/'src/ontology_proj/resources/extraction-prompt.txt', ROOT/'pyproject.toml', ROOT/'uv.lock']:
        relative=path.relative_to(ROOT);target=out/'frozen-source'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        source_hashes[str(relative)]=hashlib.sha256(path.read_bytes()).hexdigest()
    write(out/'source-freeze.json',source_hashes)
    graph = build_graph(settings)
    url = f'http://127.0.0.1:{settings.app_port}'
    checks, calls = {}, []
    log = (out/'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'ontology_proj', '--env-file', str(args.env_file.resolve())], cwd=ROOT, stdout=log, stderr=log, env={**os.environ, 'HF_HUB_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1'})
    def flush():
        write(out/'runtime.json', dict(checks=checks, calls=calls, status='in_progress'))
    def record(label, response):
        payload = response.json()
        calls.append(dict(case=label, status=response.status_code, payload=payload))
        flush()
        return payload
    pdf_path=ROOT/('samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf' if args.case=='baseline' else 'samples/synthetic/bridge-variants-001/input/missing-area.pdf')
    pdf = pdf_path.read_bytes()
    meta = dict(kind='inspection_report', source_kind='synthetic', bridge_id='B-DEMO-001')
    try:
        with httpx.Client(base_url=url, timeout=20, trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Server terminated; inspect retained log')
                try:
                    if client.get('/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            ready = record('ready-before', client.get('/ready'))
            checks['base_ready'] = ready['ready']
            def upload(key, body=pdf, metadata=meta):
                return client.post('/api/documents', headers={'Idempotency-Key':args.case+'-'+key}, files={'file':(pdf_path.name, body, 'application/pdf')}, data={'meta':json.dumps(metadata)})
            response = upload('s23-upload-001')
            saved = record('upload', response)
            assert response.status_code == 201, 'Fresh end-to-end source must not already be registered'
            docid = saved['document_id']
            checks['source_hash'] = docid == 'doc_'+hashlib.sha256(pdf).hexdigest()
            response = client.post(f'/api/documents/{docid}/process', headers={'Idempotency-Key':args.case+'-s23-process-001'})
            requested = record('process', response)
            assert response.status_code == 202
            operation_id = requested['operation_id']
            states = []
            begin = time.perf_counter()
            while time.perf_counter()-begin < 240:
                operation = client.get(f'/api/operations/{operation_id}').json()
                if not states or (states[-1]['status'],states[-1]['stage']) != (operation['status'],operation['stage']):
                    states.append(operation); write(out/'operation-states.json', states)
                if operation['status'] not in ('queued','running'):
                    break
                time.sleep(.3)
            final = record('document-final', client.get(f'/api/documents/{docid}'))
            assert operation['status'] in ('succeeded','succeeded_with_warnings'), operation
            checks['graph_committed'] = final['processing_status'] in ('graph_ready','graph_ready_with_warnings') and final['index_status']=='not_started'
            normalized = client.get(f'/api/documents/{docid}/parsed', params={'artifact':'normalized'}).json()['artifact']
            native = client.get(f'/api/documents/{docid}/parsed', params={'artifact':'docling'}).json()['artifact']
            candidate = client.get(f'/api/documents/{docid}/parsed', params={'artifact':'candidates'}).json()['artifact']
            write(out/'normalized.json', normalized); write(out/'docling.json', native); write(out/'candidate.json', candidate)
            facts = graph.lookup_document(docid,accepted_only=False)
            write(out/'graph-facts.json', facts)
            # Independent expectations are consumed only AFTER the complete run.
            expectations = json.loads((ROOT/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1.4/expectations-1.1.json').read_text())
            write(out/'independent-expectations.json', expectations)
            store = IngestionStore(settings.data_root)
            records = store.read_business_artifact(docid, 'records')
            write(out/'records.json', records)
            expected = next(c['expected'] for c in expectations['cases'] if c['case_id']==args.case)
            evaluation = compare(records, normalized, expected, expectations['common'])
            write(out/'independent-evaluation.json',evaluation)
            checks['independent_facts_semantics_evidence'] = evaluation['failed']==0
            checks['graph_record_scalar_roundtrip'] = all(any(all(node.get(k)==v for k,v in r.items() if k!='evidence_ids' and not (plural in ('bridges','components') and k=='source_document_id')) for node in facts[plural]) for plural in ('bridges','components','defects','observations') for r in records[plural])
            checks['business_counts'] = [len(facts[k]) for k in ('bridges','components','defects','observations')] == [1,2,3,3]
            checks['areas_decimal_strings'] = sorted(o['area_value'] for o in facts['observations'] if o.get('area_value') is not None) == (['0.04','0.9','2.6'] if args.case=='baseline' else ['0.04','0.9']) and all(o.get('area_value') is None or isinstance(o['area_value'],str) for o in facts['observations'])
            checks['pending_counts'] = final['pending_count']==(0 if args.case=='baseline' else 1)
            if args.case=='missing-area':
                target=next(o for o in facts['observations'] if o['observation_code']=='O-002')
                checks['missing_area_null_and_filtered'] = target.get('area_value') is None and target.get('area_is_approximate') is None and target['acceptance_status']=='pending_review' and len(graph.lookup_document(docid)['observations'])==2
            checks['unknown_dates'] = all(o.get('observed_at') is None for o in facts['observations'])
            checks['source_synthetic'] = all(o['source_kind']=='synthetic' for o in facts['observations'])
            checks['negative_not_defect'] = len(facts['defects'])==3 and any('未见' in b['text'] for b in normalized['blocks']) and any(s['kind']=='negative' for s in candidate['statements'])
            checks['evidence_quote_span_page'] = all(e['document_id']==docid and e['evidence']['document_id']==docid and e['text'][slice(*e['evidence']['charspan'])]==e['evidence']['quote'] and e['evidence']['page_no'] in (1,2) for e in facts['evidence'])
            checks['pdf_roundtrip'] = client.get(f'/api/documents/{docid}/file').content == pdf
            parse_count = len(list(settings.data_root.rglob('manifest.json')))
            model_count = len(list((settings.data_root/'documents'/docid).rglob('response.json')))
            repeated = record('upload-same-key',upload('s23-upload-001'))
            duplicate_response = upload('s23-upload-002')
            duplicate = record('upload-same-hash',duplicate_response)
            checks['upload_idempotent'] = repeated['document_id']==docid and duplicate_response.status_code==200 and duplicate['duplicate']
            conflict = record('upload-key-conflict',upload('s23-upload-001', metadata={**meta,'bridge_id':'OTHER'}))
            checks['payload_conflict'] = conflict['error']['code']=='IDEMPOTENCY_CONFLICT'
            for key in ('s23-process-001','s23-process-002'):
                repeat = record('process-repeat-'+key,client.post(f'/api/documents/{docid}/process',headers={'Idempotency-Key':args.case+'-'+key}))
                assert repeat['operation_id']==operation_id
            checks['no_duplicate_calls'] = model_count==1 and len(list((settings.data_root/'documents'/docid).rglob('response.json')))==model_count and len(list(settings.data_root.rglob('manifest.json')))==parse_count
            checks['parser_ready_after_run'] = next(c for c in client.get('/ready').json()['capabilities'] if c['name']=='parser')['status']['state']=='ready'
            with graph._session() as session:
                checks['success_audit_atomic'] = session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='success:'+operation_id).single()['n']==1
            record('operation-final',client.get(f'/api/operations/{operation_id}'))
            assert all(checks.values()),checks
            write(out/'runtime.json',dict(checks=checks,calls=calls,status='passed',model_generation_calls=model_count,elapsed_seconds=round(time.perf_counter()-begin,3)))
    except Exception as error:
        write(out/'runtime.json',dict(checks=checks,calls=calls,status='failed',error_type=type(error).__name__))
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
        log.close();graph.close()
    print(json.dumps(dict(status='passed',checks=len(checks),model_generation_calls=1)))


if __name__=='__main__':
    main()
