"""Actual HTTP/graph/process-death recovery with explicit parser fault fixture."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parents[1]


def child(config, data_root, port, mode):
    import uvicorn
    from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
    from ontology_proj.adapters.document_normalizer import DoclingNormalizer
    from ontology_proj.adapters.business_validation import BusinessValidator
    from ontology_proj.application.local_parsing import LocalParsingService
    from ontology_proj.application.business_extraction import ExtractBusiness
    from ontology_proj.application.ingestion import IngestionService
    from ontology_proj.application.parsing import ParseOutcome, ParseStatus, ParserDiagnostic
    from ontology_proj.bootstrap import build_graph, build_readiness
    from ontology_proj.interfaces.http.app import create_http_app
    from ontology_proj.settings import Settings
    settings=Settings(_env_file=config,data_root=data_root,app_port=port)
    store=IngestionStore(settings.data_root);graph=build_graph(settings)
    marker=settings.data_root/'parser-entered'
    class Parser:
        def configuration(self):return {'fixture':'process-death-before-parser-result'}
        def parse(self,path,docid):
            marker.write_text('fixture parser entered')
            if mode=='wait':
                while True:time.sleep(.2)
            return ParseOutcome(docid,ParseStatus.FAILED,None,(ParserDiagnostic('INJECTED_PARSE_FAILURE','Explicit fault fixture'),),{'fixture':'not-real-docling'},0)
    class Extractor:
        def configuration(self):return {'fixture':'must-never-be-called'}
        def request(self,*args):
            (settings.data_root/'unexpected-model-call').write_text('Unexpected fixture extraction')
            raise RuntimeError('Extraction must not be called')
    service=IngestionService(store,LocalParsingService(store,Parser(),DoclingNormalizer()),ExtractBusiness(store,Extractor(),BusinessValidator()),graph,lease=ProcessLease(store))
    uvicorn.run(create_http_app(build_readiness(settings,graph),service),host='127.0.0.1',port=port,workers=1)


def main():
    cli=argparse.ArgumentParser()
    cli.add_argument('--env-file',required=True)
    cli.add_argument('--data-root',type=Path,required=True)
    cli.add_argument('--output-dir',type=Path,required=True)
    cli.add_argument('--child',choices=['wait','fail'])
    cli.add_argument('--port',type=int,default=18001)
    args=cli.parse_args()
    if args.child:return child(args.env_file,args.data_root,args.port,args.child)
    out=args.output_dir.resolve();data=args.data_root.resolve()
    if out.exists() or data.exists():cli.error('Use fresh directories')
    out.mkdir(parents=True)
    processes=[];logs=[];checks={};states={}
    def key(tag):return data.name+'-'+tag
    def write():
        (out/'runtime.json').write_text(json.dumps(dict(checks=checks,states=states,status='in_progress'),ensure_ascii=False,indent=2)+'\n')
    def launch(mode,port,label):
        log=(out/(label+'.log')).open('w');logs.append(log)
        p=subprocess.Popen([sys.executable,__file__,'--env-file',str(Path(args.env_file).resolve()),'--data-root',str(data),'--output-dir',str(out),'--child',mode,'--port',str(port)],stdout=log,stderr=log,cwd=ROOT)
        processes.append(p);return p
    def wait_health(client,p):
        for _ in range(100):
            if p.poll() is not None:raise RuntimeError('Child app terminated')
            try:
                if client.get('/health').status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.1)
        raise RuntimeError('Child app startup timed out')
    try:
        p=launch('wait',args.port,'before-kill')
        with httpx.Client(base_url=f'http://127.0.0.1:{args.port}',timeout=10,trust_env=False) as client:
            wait_health(client,p)
            pdf=(ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()+b'\n% recovery fixture '+uuid4().hex.encode()+b'\n'
            response=client.post('/api/documents',headers={'Idempotency-Key':key('recovery-upload')},files={'file':('recovery-fault-fixture.pdf',pdf)},data={'meta':json.dumps(dict(kind='inspection_report',source_kind='synthetic',bridge_id='B-DEMO-001'))})
            assert response.status_code==201;docid=response.json()['document_id']
            requested=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':key('recovery-process')}).json();op=requested['operation_id']
            for _ in range(100):
                if (data/'parser-entered').exists():break
                time.sleep(.1)
            assert (data/'parser-entered').exists()
            states['before']=client.get('/api/operations/'+op).json();write()
            second=launch('fail',args.port+1,'concurrent-refused')
            try:second.wait(timeout=8)
            except subprocess.TimeoutExpired:raise AssertionError('Concurrent app must refuse startup')
            checks['second_process_refused']=second.returncode!=0 and client.get('/api/operations/'+op).json()['status']=='running'
            duplicate=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':key('recovery-duplicate')}).json()
            checks['running_operation_reused']=duplicate['operation_id']==op
            p.kill();p.wait();(data/'parser-entered').unlink()
            resumed=launch('fail',args.port,'after-kill');wait_health(client,resumed)
            states['after']=client.get('/api/operations/'+op).json()
            checks['actual_process_death_interrupted']=states['after']['status']=='interrupted' and client.get('/api/documents/'+docid).json()['processing_status']=='interrupted'
            time.sleep(.4)
            checks['no_automatic_parser_or_model']=not (data/'parser-entered').exists() and not (data/'unexpected-model-call').exists()
            response=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':key('process-after-crash')})
            checks['explicit_retry_required']=response.status_code==409 and response.json()['error']['code']=='EXPLICIT_RETRY_REQUIRED'
            response=client.post('/api/documents/'+docid+'/retry',headers={'Idempotency-Key':key('recovery-explicit-retry')},json={'stage':'failed_stage'})
            assert response.status_code==202;retry=response.json()['operation_id']
            for _ in range(100):
                state=client.get('/api/operations/'+retry).json()
                if state['status'] not in ('queued','running'):break
                time.sleep(.1)
            states['retry']=state
            checks['retry_link_and_failure_diagnostic']=state['retry_of']==op and state['status']=='failed' and state['error_code']=='INJECTED_PARSE_FAILURE'
            checks['no_model_calls']=not (data/'unexpected-model-call').exists()
            assert all(checks.values()),checks
            write();(out/'runtime.json').write_text(json.dumps(dict(checks=checks,states=states,status='passed',provider_calls=0,parser='explicit fault fixture'),ensure_ascii=False,indent=2)+'\n')
    except Exception as error:
        (out/'runtime.json').write_text(json.dumps(dict(checks=checks,states=states,status='failed',error_type=type(error).__name__),ensure_ascii=False,indent=2)+'\n')
        raise
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill();p.wait()
        for log in logs:log.close()
    print(json.dumps(dict(status='passed',checks=len(checks),provider_calls=0)))


if __name__=='__main__':main()
