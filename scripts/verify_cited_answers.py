"""Real TCP/relay/graph answers, independent expectations applied afterwards."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import uvicorn
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import create_app,build_graph
from ontology_proj.adapters.ingestion_store import IngestionStore
from ontology_proj.adapters.answer_generator import RelayAnswerGenerator
from ontology_proj.application.semantic import stable

parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);parser.add_argument('--case');args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
expect=json.loads(Path('specs/005-cited-answer/results/preparation/expectations.json').read_text());s=Settings(_env_file=args.config)
store=IngestionStore(s.data_root);records=store.read_business_artifact(expect['document_id'],'records')
selected={o['observation_code']:o['observation_id'] for o in records['observations']}
(out/'selectors.json').write_text(json.dumps(selected,indent=2))
assert next(o for o in records['observations'] if o['observation_code']=='O-001')['area_value']=='0.04'
checks=[];cases=[];calls=[];server=None;thread=None;graph=build_graph(s)
original=RelayAnswerGenerator.generate

def tracked(self,request):
    calls.append({'model':request['model'],'automatic_retry':False})
    return original(self,request)
RelayAnswerGenerator.generate=tracked

def check(name,condition):checks.append({'name':name,'passed':bool(condition)})
def graph_snapshot():
    with graph._session() as session:
        nodes=session.run('MATCH (n) RETURN labels(n) AS labels,properties(n) AS value').data()
        relationships=session.run('MATCH (a)-[r]->(b) RETURN labels(a) AS a_labels,properties(a) AS a,type(r) AS type,properties(r) AS r,labels(b) AS b_labels,properties(b) AS b').data()
    return stable([sorted(nodes,key=stable),sorted(relationships,key=stable)])
def call(path,body=None):
    request=Request('http://127.0.0.1:18000'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=190) as r:return r.status,json.load(r)
    except HTTPError as e:return e.code,json.load(e)
try:
    prior={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in s.data_root.rglob('*') if p.is_file() and p.suffix in ('.json','.pdf')}
    app=create_app(s);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18000,log_level='warning'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    check('actual HTTP started',server.started)
    before=graph_snapshot()
    for expected in expect['cases']:
        if args.case and expected['id']!=args.case:continue
        body={'question':expected['question'],'scope':{'document_ids':[expect['document_id']],'bridge_id':expected.get('wrong_bridge',expect['bridge_id'])},'top_k':5}
        selector=expected.get('graph')
        if selector=='bridge_components':body['graph_query']={'query_type':'bridge_components'}
        elif selector:body['graph_query']={'query_type':'record_by_id','record_id':selected[selector.split(':')[1]]}
        dirs={p.name for p in (s.data_root/'answers').glob('*')};count=len(calls)
        status,answer=call('/api/answers',body)
        folder=out/'cases'/expected['id'];folder.mkdir(parents=True)
        for path in (s.data_root/'answers').glob('*'):
            if path.name not in dirs:shutil.copytree(path,folder/path.name)
        (folder/'http.json').write_text(json.dumps({'request':body,'status':status,'response':answer},ensure_ascii=False,indent=2))
        check(expected['id']+' HTTP',status==200)
        check(expected['id']+' status',answer.get('status')==expected['status'])
        if 'field' in expected:check(expected['id']+' exact assertion',any(a['field']==expected['field'] and a['value']==expected['value'] and a['unit']==expected['unit'] for c in answer.get('claims',[]) for a in c['assertions']))
        if 'names' in expected:check(expected['id']+' complete names',set(expected['names'])=={a['value'] for c in answer.get('claims',[]) for a in c['assertions'] if a['field']=='name'})
        if 'source_contains' in expected:check(expected['id']+' negative quote',any(c['kind']=='source_statement' and expected['source_contains'] in c['text'] for c in answer.get('claims',[])))
        if 'reason' in expected:check(expected['id']+' fixed refusal',expected['reason'] in answer.get('reason_codes',[]) and not answer.get('claims'))
        if 'generation_calls' in expected:check(expected['id']+' no model call',len(calls)-count==expected['generation_calls'])
        for c in answer.get('claims',[]):
            for eid in c['evidence_ids']:
                code,e=call('/api/evidence/'+eid);check(expected['id']+' citation '+eid,code==200 and e['document']['document_id']==expect['document_id'] and e['evidence']['quote'] in e['block_text'])
        if answer.get('claims'):check(expected['id']+' synthetic warning',any('synthetic' in w for w in answer['warnings']))
        cases.append({'id':expected['id'],'http_status':status,'answer_status':answer.get('status'),'generation_requests':len(calls)-count})
        print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
    check('business graph and index unchanged',graph_snapshot()==before)
    check('prior runtime artifacts unchanged',all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in prior.items()))
    # No new generation is needed after the actual HTTP app restart.
    server.should_exit=True;thread.join(15)
    app=create_app(s);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18000,log_level='warning'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    check('HTTP actual restart',server.started)
    count=len(calls);code,empty=call('/api/answers',{'question':'有哪些构件？','scope':{'document_ids':[expect['document_id']],'bridge_id':'OUTSIDE-BRIDGE'}})
    check('restart scoped refusal without model',code==200 and empty['status']=='insufficient_evidence' and len(calls)==count)
except Exception as exc:
    checks.append({'name':'unexpected verification failure','passed':False,'type':type(exc).__name__,'message':str(exc)})
finally:
    RelayAnswerGenerator.generate=original
    if server:server.should_exit=True
    if thread:thread.join(15)
    graph.close()
    result={'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'cases':cases,'generation_requests':calls,'checks':checks}
    (out/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:result[k] for k in ['passed','failed']},ensure_ascii=False),flush=True)
if result['failed']:raise SystemExit(1)
