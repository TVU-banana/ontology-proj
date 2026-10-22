"""Explicit local S3.2 acceptance; no extraction or generation calls."""
import argparse
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.request import Request,urlopen
import uvicorn
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import create_app,build_graph,build_semantic
from ontology_proj.adapters.ingestion_store import IngestionStore
from ontology_proj.application.semantic import stable
from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding

p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--output',required=True);args=p.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
expected=json.loads(Path('specs/004-semantic-context/results/s3.2/preparation/expectations.json').read_text())
settings=Settings(_env_file=args.config);graph=build_graph(settings)
checks=[];responses={};server=None;thread=None
embedding_requests=[]
original_call=OllamaEmbedding._call
def tracked_call(self,path,body=None):
    if path=='/api/embed':
        embedding_requests.append({'model':body['model'],'input_count':len(body['input']),'truncate':body['truncate']})
    return original_call(self,path,body)
OllamaEmbedding._call=tracked_call

def check(name,condition):
    checks.append(dict(name=name,passed=bool(condition)))
    if not condition:raise AssertionError(name)

def snapshot():
    with graph._session() as session:
        nodes=session.run('MATCH (n) WHERE n:Bridge OR n:Component OR n:Defect OR n:Observation OR n:TextBlock OR (n:Evidence AND NOT n.evidence_id STARTS WITH "ev_chunk_") RETURN labels(n) AS labels,properties(n) AS props').data()
        relationships=session.run('MATCH (a)-[r]->(b) WHERE type(r) IN ["HAS_COMPONENT","HAS_DEFECT","HAS_OBSERVATION","SUPPORTED_BY"] RETURN properties(a) AS a,type(r) AS type,properties(r) AS r,properties(b) AS b').data()
    return stable([sorted(nodes,key=stable),sorted(relationships,key=stable)])

def call(path,body=None):
    request=Request('http://127.0.0.1:18000'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=180) as response:return json.load(response)

try:
    before=snapshot();responses['business_graph_before']=before
    runtime={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in settings.data_root.rglob('*') if p.is_file() and p.suffix in ('.json','.pdf')}
    app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18000,log_level='warning'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    check('actual TCP started',server.started)
    docs=[expected['original_document_id'],expected['missing_document_id']]
    for id in docs:
        response=call('/api/documents/'+id+'/index',{});responses['index_'+id]=response
        check('index ready '+id,response['index_status']=='ready' and response['chunk_count']>0)
        reused=call('/api/documents/'+id+'/index',{});check('HTTP repeat reused '+id,reused['reused'])
    responses['readiness']=call('/ready')
    indexer,search,context,probe=build_semantic(settings,graph,IngestionStore(settings.data_root))
    check('index readiness actual',probe.check().state=='ready')
    count=indexer.provider.calls
    check('direct reuse',indexer.execute(docs[0])['reused'])
    check('reuse zero embeds',indexer.provider.calls==count)
    for i,q in enumerate(expected['questions']):
        result=call('/api/semantic/search',dict(query=q['query'],scope={'document_ids':[docs[0]]},top_k=q['top_k']));responses['question_'+str(i)]=result
        check('ranked expectation '+str(i),result['status']=='ready' and any(c['content_kind']==q['content_kind'] and any(q['expected_quote_contains'] in s['quote'] for s in c['source_spans']) for c in result['chunks']))
    supplemental=json.loads(Path('specs/004-semantic-context/results/s3.2/preparation/supplemental-expectation.json').read_text())
    corrected=call('/api/semantic/search',dict(query=supplemental['query'],scope={'document_ids':[docs[0]]},top_k=5));responses['supplemental_location']=corrected
    check('correct source location and area',corrected['status']=='ready' and any(c['content_kind']=='business_fact' and any(supplemental['expected_quote_contains'] in s['quote'] for s in c['source_spans']) for c in corrected['chunks']))
    for name,scope in [('no synthetic',{'include_synthetic':False}),('wrong bridge',{'bridge_id':'unrelated'}),('wrong component',{'component_ids':['unrelated']}),('confirmed only',{'review_mode':'confirmed_only'})]:
        result=call('/api/semantic/search',dict(query='病害',scope={'document_ids':docs}|scope,top_k=20));responses[name]=result
        check(name,result['status']=='ready' and not result['chunks'])
    dated=call('/api/semantic/search',dict(query='病害',scope={'document_ids':docs,'date_from':'2026-01-01'},top_k=20));responses['date']=dated
    check('unknown dates excluded',not any(c['content_kind']=='business_fact' for c in dated['chunks']))
    missing=call('/api/semantic/search',dict(query='主梁露筋面积',scope={'document_ids':[docs[1]]},top_k=20));responses['missing']=missing
    raw=IngestionStore(settings.data_root).read_business_artifact(docs[1],'records')
    pending={o['observation_id'] for o in raw['observations'] if o['acceptance_status']!='accepted'}
    check('pending observation excluded',not any(pending.intersection(c['record_ids']) for c in missing['chunks']))
    req=dict(query='桥头未见病害',scope={'document_ids':[docs[0]]},top_k=20,graph_query={'query_type':'bridge_components'})
    bundle=call('/api/context',req);responses['context']=bundle
    check('context graph facts',len(bundle['facts'])==2)
    check('context separate and budget',any(c['semantic_kind']=='negative' for c in bundle['source_statements']) and all(c['content_kind']=='source_statement' for c in bundle['source_statements']) and all(c['content_kind']=='business_fact' for c in bundle['business_passages']) and bundle['content_characters']<=8000)
    # Real connection refusal, rather than a fabricated model response. Exact lookup still runs.
    search.provider.base_url='http://127.0.0.1:9'
    degraded=context.execute(req);responses['degraded']=degraded
    check('embedding failure preserves exact graph',degraded['status']=='degraded' and len(degraded['facts'])==2 and degraded['error_code']=='EMBEDDING_UNAVAILABLE')
    search.provider.base_url=settings.embedding_base_url;search.provider.digest='0'*64
    changed=search.execute({k:v for k,v in req.items() if k!='graph_query'});responses['changed_model']=changed
    check('actual identity drift rejected',changed['error_code']=='EMBEDDING_MODEL_CHANGED')
    allchunks=[c for r in responses.values() if isinstance(r,dict) for c in r.get('chunks',[])]+bundle['business_passages']+bundle['source_statements']
    seen={}
    for c in allchunks:
        check('scoped '+c['chunk_id'],c['document_id'] in docs and 'embedding' not in c)
        for s in c['source_spans']:seen[s['evidence_id']]=s
    for eid,s in seen.items():
        evidence=call('/api/evidence/'+eid);check('PDF evidence '+eid,evidence['evidence']['quote']==s['quote'] and evidence['evidence']['charspan']==s['charspan'] and evidence['page_geometry'] and evidence['pdf_url'])
    check('business graph unchanged',snapshot()==before)
    check('published runtime files unchanged',all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in runtime.items()))
    responses['evidence_count']=len(seen);responses['runtime_file_count']=len(runtime)
    # Restart the actual HTTP app and read the already committed index without embedding.
    server.should_exit=True;thread.join(15);check('first server stopped',not thread.is_alive())
    app=create_app(settings);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18000,log_level='warning'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    check('actual app restart',server.started)
    check('restart index reuse',call('/api/documents/'+docs[0]+'/index',{})['reused'])
except Exception as exc:
    responses['failure']={'type':type(exc).__name__,'code':getattr(exc,'code',None),'message':str(exc)}
finally:
    if server:server.should_exit=True
    if thread:thread.join(15)
    graph.close()
    OllamaEmbedding._call=original_call
    responses['embedding_requests']=embedding_requests
    (out/'responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2))
    (out/'checks.json').write_text(json.dumps(dict(passed=sum(c['passed'] for c in checks),failed=sum(not c['passed'] for c in checks),checks=checks,complete='failure' not in responses),ensure_ascii=False,indent=2))
    print(json.dumps(dict(passed=sum(c['passed'] for c in checks),failed=sum(not c['passed'] for c in checks),complete='failure' not in responses,failure=responses.get('failure')),ensure_ascii=False))
if 'failure' in responses:raise SystemExit(1)
