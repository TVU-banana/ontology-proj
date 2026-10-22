"""Real isolated graph/HTTP/embeddings/answers/report, bounded first requests."""
import hashlib
import json
import socket
import threading
import time
from pathlib import Path
import httpx
import uvicorn
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import create_app, build_graph, build_semantic
from ontology_proj.adapters.ingestion_store import IngestionStore
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding
from ontology_proj.adapters.docling_parser import DoclingParser
from ontology_proj.application.semantic import build_chunks
from revalidate_registered_snapshot import ROOT, OUT, DATA
from real_registered import sha, write


def main():
    assert not (OUT/'chain-result.json').exists()
    s=Settings(_env_file=DATA/'private/.env.app')
    assert s.data_root==DATA/'runtime' and s.neo4j_uri=='bolt://127.0.0.1:19687' and s.app_port==19100
    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',19100))!=0
    checks=[];calls=[];embeds=[];server=None;thread=None
    def check(name,passed):
        checks.append({'name':name,'passed':bool(passed)})
        if not passed:raise RuntimeError('Check failed: '+name)
    def save(name,value):
        raw=json.dumps(value,ensure_ascii=False)
        assert not any(secret and secret in raw for secret in [s.llm_api_key.get_secret_value(),s.neo4j_password.get_secret_value()])
        write(OUT/name,value)
    graph=build_graph(s);deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:graph.driver.verify_connectivity();break
        except Exception:time.sleep(1)
    else:raise RuntimeError('New isolated graph not ready')
    with graph._session() as session:check('dedicated graph initially empty',session.run('MATCH(n) RETURN count(n) AS n').single()['n']==0)
    graph.initialize();store=IngestionStore(s.data_root);saved=json.loads((OUT/'result.json').read_text());did=saved['document_id']
    records=store.read_business_artifact(did,'records');document=store.read_artifact(did,'normalized')
    indexer,search,_,_=build_semantic(s,graph,store)
    chunks,excluded=build_chunks(document,records,search.profile)
    check('complete index fits approved512chunk budget',len(chunks)<=512)
    save('chunk-preflight.json',{'chunks':len(chunks),'batches':(len(chunks)+15)//16,'unselected_blocks':len(excluded),'budget':512})
    raw_transport=RelayFactExtractor.extract;raw_embed=OllamaEmbedding._call
    def transport(adapter,request):
        check('answer API and model only',adapter.base==s.llm_base_url.rstrip('/') and request['model']==s.llm_model and request['max_tokens']==6000)
        check('answer count budget',len(calls)<5)
        payload=json.loads(request['messages'][1]['content']);context=payload['provided_context']
        check('answer scope isolated real source',context['scope']['document_ids']==[did] and all(e['document_id']==did and e['source_kind']=='real' for e in context['evidence'].values()))
        number=len(calls)+1;calls.append({'number':number,'max_tokens':6000});save(f'answer-{number:02d}-request.json',request)
        result=raw_transport(adapter,request);save(f'answer-{number:02d}-response.json',result)
        calls[-1].update(model=result.get('response',{}).get('model'),usage=result.get('response',{}).get('usage'),diagnostics=result.get('diagnostics'))
        return result
    def embedding(adapter,path,body=None):
        if path=='/api/embed':
            query=bool(body['input'] and body['input'][0].startswith('Instruct: '))
            count=sum(e['query']==query for e in embeds)
            check('embedding query/index count budget',count<(5 if query else 32))
            check('embedding payload batch size',len(body['input'])<=16)
            embeds.append({'query':query,'inputs':len(body['input'])})
        return raw_embed(adapter,path,body)
    def no_parse(*args,**kwargs):raise RuntimeError('No new Docling conversion authorized in this run')
    RelayFactExtractor.extract=transport;OllamaEmbedding._call=embedding;DoclingParser.parse=no_parse
    frozen=[p for p in (ROOT/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]+[Path(__file__)]
    save('chain-freeze.json',{str(p.relative_to(ROOT)):sha(p) for p in frozen})
    client=httpx.Client(base_url='http://127.0.0.1:19100',timeout=150)
    result={}
    try:
        server=uvicorn.Server(uvicorn.Config(create_app(s),host='127.0.0.1',port=19100,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start();deadline=time.monotonic()+60
        while not server.started and time.monotonic()<deadline and thread.is_alive():time.sleep(.1)
        check('real HTTP started',server.started)
        check('HTTP health200',client.get('/health').status_code==200)
        pdf=Path(store.source_path(did)).read_bytes()
        meta={'kind':'inspection_report','source_kind':'real','bridge_id':records['bridges'][0]['bridge_id']}
        response=client.post('/api/documents',files={'file':('shengli-original.pdf',pdf,'application/pdf')},data={'meta':json.dumps(meta)},headers={'Idempotency-Key':'s52-real-register'})
        save('http-registration.json',{'http':response.status_code,'body':response.json()});check('real PDF registered',response.status_code==201)
        response=client.post('/api/documents/'+did+'/process',headers={'Idempotency-Key':'s52-real-process'})
        save('http-processing-request.json',{'http':response.status_code,'body':response.json()});check('real process accepted',response.status_code==202)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            state=client.get('/api/documents/'+did).json()
            if state.get('processing_status') in ('graph_ready','graph_ready_with_warnings','failed'):break
            time.sleep(.2)
        save('http-document.json',state);check('actual graph commit finished',state.get('processing_status') in ('graph_ready','graph_ready_with_warnings'))
        check('published real PDF samebytes',hashlib.sha256(client.get('/api/documents/'+did+'/file').content).hexdigest()==hashlib.sha256(pdf).hexdigest())
        response=client.post('/api/documents/'+did+'/index',json={'retry':False});save('http-index.json',{'http':response.status_code,'body':response.json()});check('real local index ready',response.status_code==200 and response.json()['index_status']=='ready')
        scope={'bridge_id':records['bridges'][0]['bridge_id'],'document_ids':[did],'include_synthetic':False}
        response=client.post('/api/graph/lookup',json={'query_type':'bridge_components','scope':scope});save('http-components.json',response.json());check('three registered components in actual graph',response.status_code==200 and response.json()['count']==3)
        components={c['name']:c['component_id'] for c in records['components']}
        questions=['3跨桥面铺装坑槽面积是多少？','3-1#左侧腹板与挑臂露筋面积是否相同？','这些病害是哪一天检测到的？','桥头存在肯定病害吗？','请判定桥梁安全等级或直接修正BCI值。']
        answers=[]
        for i,question in enumerate(questions,1):
            body={'question':question,'scope':dict(scope),'top_k':5}
            if i in (1,2,3):
                selected=[components['桥面铺装']] if i==1 else [components['3-1#左侧腹板'],components['3-1#挑臂']] if i==2 else list(components.values())
                body['scope']['component_ids']=selected;body['graph_query']={'query_type':'component_observations'}
            response=client.post('/api/answers',json=body);item={'question':question,'http':response.status_code,'body':response.json()};answers.append(item);save(f'http-answer-Q{i:02d}.json',item)
            # Keep each first answer/error; no model retry. Other fixed questions remain independently runnable.
        response=client.post('/api/reports',json=scope);save('http-report-submit.json',{'http':response.status_code,'body':response.json()});check('deterministic report submitted',response.status_code==202)
        rid=response.json()['report_id'];deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            report=client.get('/api/reports/'+rid).json()
            if report.get('status') in ('succeeded','failed','interrupted'):break
            time.sleep(.2)
        save('http-report.json',report);check('report succeeded',report.get('status')=='succeeded')
        markdown=client.get('/api/reports/'+rid+'/markdown');check('report Markdown available',markdown.status_code==200)
        with (DATA/'report-reviewed.md').open('x') as f:f.write(markdown.text)
        evidence=[]
        for ev in records['evidence']:
            response=client.get('/api/evidence/'+ev['evidence_id']);entry={'evidence_id':ev['evidence_id'],'http':response.status_code,'body':response.json()};evidence.append(entry)
            check('actual evidence '+ev['evidence_id'],response.status_code==200)
        write(DATA/'actual-evidence.json',evidence)
        result={'status':'real_chain_executed','answers':answers,'report_id':rid,'document_id':did,'evidence_verified':len(evidence),'professional_review':'not_run','full_s52_complete':False}
        save('browser-ready.json',{'url':'http://127.0.0.1:19100/query','evidence_example':records['evidence'][0]['evidence_id'],'report_id':rid})
        print(json.dumps({'event':'browser_ready','report_id':rid,'answer_http':[a['http'] for a in answers]},ensure_ascii=False),flush=True)
        # Bounded interactive inspection window; no new model requests unless explicit API routes are invoked.
        until=time.monotonic()+300
        while not (DATA/'stop-browser-window').exists() and time.monotonic()<until:time.sleep(.5)
    except Exception as exc:
        result={'status':'chain_failed','exception_type':type(exc).__name__,'code':getattr(exc,'code',None),'professional_review':'not_run','full_s52_complete':False}
        print(json.dumps(result),flush=True)
    finally:
        if server:server.should_exit=True
        if thread:thread.join(30)
        client.close();graph.close()
        result.update(checks=checks,answer_generation_calls=len(calls),embedding_calls=embeds,new_docling_calls=0,provider_calls=calls,
                      owned_http_stopped=thread is None or not thread.is_alive())
        save('chain-result.json',result)


if __name__=='__main__':main()
