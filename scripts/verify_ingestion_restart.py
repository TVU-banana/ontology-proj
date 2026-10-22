"""Current application's TCP restart/reuse checks; never starts new extraction."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from ontology_proj.bootstrap import build_graph
from ontology_proj.settings import Settings

ROOT=Path(__file__).resolve().parents[1]


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('--env-file',required=True)
    cli.add_argument('--output-dir',type=Path,required=True)
    args=cli.parse_args();out=args.output_dir.resolve()
    if out.exists():cli.error('Preserve previous results; use a new directory')
    out.mkdir(parents=True)
    s=Settings(_env_file=args.env_file);graph=build_graph(s)
    hashes=lambda:{str(p.relative_to(s.data_root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (s.data_root/'documents').rglob('*') if p.is_file()}
    before=hashes();checks={};states=[]
    log=(out/'server.log').open('w')
    process=subprocess.Popen([sys.executable,'-m','ontology_proj','--env-file',str(Path(args.env_file).resolve())],cwd=ROOT,stdout=log,stderr=log,env={**os.environ,'HF_HUB_OFFLINE':'1'})
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{s.app_port}',trust_env=False,timeout=20) as client:
            for _ in range(100):
                try:
                    if client.get('/health').status_code==200:break
                except httpx.HTTPError:pass
                if process.poll() is not None:raise RuntimeError('Server startup failed')
                time.sleep(.1)
            checks['ready_after_container_and_app_restart']=client.get('/ready').status_code==200
            for case,path,accepted,pending in [('baseline','samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf',9,0),('missing-area','samples/synthetic/bridge-variants-001/input/missing-area.pdf',8,1)]:
                pdf=(ROOT/path).read_bytes();docid='doc_'+hashlib.sha256(pdf).hexdigest()
                state=client.get('/api/documents/'+docid).json();states.append(state)
                checks[case+'_committed_state_preserved']=state['processing_status']=='graph_ready_with_warnings' and state['accepted_count']==accepted and state['pending_count']==pending
                checks[case+'_source_bytes_contract']=state['bytes']==len(pdf)
                response=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':'final-restart-'+case})
                checks[case+'_process_reused']=response.status_code==202 and response.json()['operation_id']==state['operation_id'] and response.json()['reused']
                checks[case+'_pdf_read']=client.get('/api/documents/'+docid+'/file').content==pdf
                checks[case+'_candidate_read']=client.get('/api/documents/'+docid+'/parsed?artifact=candidates').status_code==200
                facts=graph.lookup_document(docid,accepted_only=False)
                checks[case+'_per_document_graph_counts']=[len(facts[k]) for k in ('bridges','components','defects','observations')]==[1,2,3,3]
            checks['no_new_parser_model_or_artifact_write']=hashes()==before
            checks['default_query_excludes_pending']=len(graph.lookup_document(states[1]['document_id'])['observations'])==2
            assert all(checks.values()),checks
            snapshots={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src/ontology_proj').rglob('*') if p.is_file() and '__pycache__' not in str(p)}
            (out/'runtime.json').write_text(json.dumps(dict(status='passed',checks=checks,states=states,source_hashes=snapshots,provider_calls=0,document_artifacts_unchanged=True),ensure_ascii=False,indent=2)+'\n')
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        log.close();graph.close()
    print(json.dumps(dict(status='passed',checks=len(checks),provider_calls=0)))


if __name__=='__main__':main()
