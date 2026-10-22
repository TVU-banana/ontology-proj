"""Report-only continuation after fixed unit guard; no provider/embedding POST."""
import json
import threading
import time
from pathlib import Path
import httpx
import uvicorn
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import create_app
from ontology_proj.adapters.ingestion_store import IngestionStore
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding
from ontology_proj.adapters.docling_parser import DoclingParser
from revalidate_registered_snapshot import ROOT, OUT, DATA
from real_registered import write, sha


def main():
    assert not (OUT/'report-continuation.json').exists()
    s=Settings(_env_file=DATA/'private/.env.app');store=IngestionStore(s.data_root)
    did=json.loads((OUT/'result.json').read_text())['document_id'];records=store.read_business_artifact(did,'records')
    def prohibited(*args,**kwargs):raise RuntimeError('No additional generation/conversion authorized')
    original=OllamaEmbedding._call
    def metadata_only(adapter,path,body=None):
        if path=='/api/embed':raise RuntimeError('Completed embeddings must not be repeated')
        return original(adapter,path,body)
    RelayFactExtractor.extract=prohibited;DoclingParser.parse=prohibited;OllamaEmbedding._call=metadata_only
    write(OUT/'report-continuation-freeze.json',{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__)]+[p for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]})
    server=uvicorn.Server(uvicorn.Config(create_app(s),host='127.0.0.1',port=19100,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();client=httpx.Client(base_url='http://127.0.0.1:19100',timeout=30)
    result={}
    try:
        deadline=time.monotonic()+60
        while not server.started and time.monotonic()<deadline and thread.is_alive():time.sleep(.1)
        assert server.started
        scope={'bridge_id':records['bridges'][0]['bridge_id'],'component_ids':[c['component_id'] for c in records['components']],
               'document_ids':[did],'include_synthetic':False}
        response=client.post('/api/reports',json=scope);assert response.status_code==202
        rid=response.json()['report_id'];deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            body=client.get('/api/reports/'+rid).json()
            if body.get('status') in ('succeeded','failed','interrupted'):break
            time.sleep(.2)
        write(OUT/'report-continuation-state.json',body);assert body['status']=='succeeded'
        markdown=client.get('/api/reports/'+rid+'/markdown');assert markdown.status_code==200
        with (DATA/'report-reviewed.md').open('x') as f:f.write(markdown.text)
        with (DATA/'report-reviewed.json').open('x') as f:f.write(json.dumps(body['report'],ensure_ascii=False,indent=2)+'\n')
        evidence=[]
        for ev in records['evidence']:
            response=client.get('/api/evidence/'+ev['evidence_id']);assert response.status_code==200
            evidence.append({'evidence_id':ev['evidence_id'],'http':200,'body':response.json()})
        write(DATA/'actual-evidence.json',evidence)
        write(OUT/'browser-ready.json',{'url':'http://127.0.0.1:19100/query','evidence_example':records['evidence'][0]['evidence_id'],'report_id':rid})
        result={'status':'succeeded','report_id':rid,'actual_evidence_count':len(evidence),'new_generation_embedding_parser_calls':0,
                'earlier_report_failure_retained':True,'professional_review':'not_run'}
        print(json.dumps({'event':'browser_ready','report_id':rid},ensure_ascii=False),flush=True)
        until=time.monotonic()+300
        while not (DATA/'stop-browser-window').exists() and time.monotonic()<until:time.sleep(.5)
    except Exception as exc:
        result={'status':'failed','exception_type':type(exc).__name__,'error_code':getattr(exc,'code',None),'new_generation_embedding_parser_calls':0}
        print(json.dumps(result),flush=True)
    finally:
        server.should_exit=True;thread.join(30);client.close()
        result['owned_http_stopped']=not thread.is_alive();write(OUT/'report-continuation.json',result)


if __name__=='__main__':main()
