"""S4.1 independent checks on existing publication, real TCP and real report files.

No parse/extract/index/generate/embed endpoint is called. Existing reports kept.
"""
import argparse
import hashlib
import json
import socket
import threading
import time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import uvicorn
from ontology_proj.bootstrap import create_app,build_graph
from ontology_proj.settings import Settings

parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
s=Settings(_env_file=args.config);expected=json.loads(Path('specs/006-observation-report/expectations.json').read_text())
checks=[];reports=[];server=None;thread=None;graph=build_graph(s)
def check(name,value):checks.append({'name':name,'passed':bool(value)})
def stable(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def snapshot():
    with graph._session() as session:
        nodes=session.run('MATCH (n) WHERE any(l IN labels(n) WHERE l IN ["Bridge","Component","Defect","Observation","Evidence","TextBlock","SourceDocument","DocumentChunk"]) RETURN labels(n) AS labels,properties(n) AS value').data()
        rels=session.run('MATCH (a)-[r]->(b) WHERE NOT a:Report AND NOT a:Operation AND NOT a:AuditEvent AND NOT b:Report AND NOT b:Operation AND NOT b:AuditEvent RETURN labels(a) AS al,properties(a) AS a,type(r) AS type,properties(r) AS r,labels(b) AS bl,properties(b) AS b').data()
    return stable([sorted(nodes,key=stable),sorted(rels,key=stable)])
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
def call(path,body=None,raw=False):
    request=Request(f'http://127.0.0.1:{port}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=30) as r:return r.status,r.read().decode() if raw else json.load(r),dict(r.headers)
    except HTTPError as e:return e.code,e.read().decode() if raw else json.load(e),dict(e.headers)
def generate(name,body):
    code,response,_=call('/api/reports',body);check(name+' accepted 202',code==202)
    rid=response['report_id'];operation=response['operation_id']
    for _ in range(600):
        code,result,_=call('/api/reports/'+rid)
        if result['status'] not in ('running','queued'):break
        time.sleep(.05)
    (out/(name+'-response.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    check(name+' registered',result['status']=='succeeded')
    reports.append({'case':name,'report_id':rid,'operation_id':operation,'status':result['status']})
    if result['status']!='succeeded':return None
    report=result['report'];(out/(name+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    code,md,headers=call('/api/reports/'+rid+'/markdown',raw=True);(out/(name+'-report.md')).write_text(md)
    check(name+' markdown published',code==200 and 'text/markdown' in headers.get('content-type',''))
    check(name+' five fixed sections',[v['section_id'] for v in report['sections']]==expected['sections'])
    check(name+' professional not_run',report['professional_review']=='not_run')
    check(name+' all checks passed',all(report['checks'][k]=='passed' for k in expected['checks']))
    check(name+' no current citation changes',not result['citation_status_changes'])
    code,review,_=call('/api/reports/'+rid+'/professional-review',{});check(name+' review 501',code==501 and review['review_status']=='not_run')
    code,retry,_=call('/api/reports/'+rid+'/retry',{});check(name+' duplicate retry reused',code==202 and retry['reused'])
    code,op,_=call('/api/operations/'+operation);check(name+' shared operation status',code==200 and op['status']=='succeeded')
    with graph._session() as session:
        n=session.run('MATCH (r:Report {report_id:$id}) OPTIONAL MATCH (r)-[:CITES]->(e:Evidence) RETURN count(DISTINCT r) AS reports,count(DISTINCT e) AS citations',id=rid).single()
        a=session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='report-success:'+operation).single()['n']
    check(name+' one graph report/audit',n['reports']==1 and a==1)
    check(name+' graph CITES equals index',n['citations']==len(report['sections'][4]['rows']))
    for e in report['sections'][4]['rows']:
        code,raw,_=call('/api/evidence/'+e['evidence_id']);check(name+' quote '+e['evidence_id'],code==200 and raw['evidence']['quote']==e['quote'] and raw['evidence']['document_id']==e['document_id'] and raw['document']['source_kind']==e['source_kind'])
    if report['sections'][4]['rows']:
        eid=report['sections'][4]['rows'][0]['evidence_id'];code,html,_=call('/evidence/'+eid,raw=True);check(name+' stable viewer route',code==200 and '/static/query.js' in html)
    return report
try:
    before=snapshot()
    prior={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in s.data_root.rglob('*') if p.is_file() and p.suffix in ('.json','.pdf')}
    app=create_app(s);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    check('actual TCP started',server.started)
    original=expected['original'];scope=dict(bridge_id=original['bridge_id'],document_ids=[original['document_id']])
    report=generate('original',scope)
    if report:
        rows=report['sections'][2]['rows'];check('original 3 observations',len(rows)==original['observations'])
        check('original independent areas',{r['values']['observation_code']:r['values']['area_value'] for r in rows}==original['areas'])
        check('original unknown dates and m²',all(r['values']['observed_at'] is None and r['values']['area_unit']=='m²' and r['values']['source_kind']=='synthetic' for r in rows))
        check('original negative preserved as statement',any(r['quote']==original['negative_quote'] and r['kind']=='negative' for r in report['sections'][3]['rows']))
        check('negative not an extra Observation',len(rows)==3)
    dated=generate('dated',dict(scope,date_from='2026-01-01',date_to='2026-12-31'))
    if dated:
        check('dated zero rows',dated['sections'][2]['rows']==[])
        check('dated unknown count 3',any('3 条未知日期' in v for v in dated['missing_information']))
    with graph._session() as session:
        documents=session.run('MATCH (d:SourceDocument) WHERE d.bridge_id=$id AND d.document_id <> $original AND d.processing_status IN ["graph_ready","graph_ready_with_warnings"] RETURN d.document_id AS id',id=scope['bridge_id'],original=original['document_id']).data()
    check('single preserved missing-area source',len(documents)==1)
    if len(documents)==1:
        missing=generate('missing-area',dict(bridge_id=scope['bridge_id'],document_ids=[documents[0]['id']]))
        if missing:
            check('missing-area 2 accepted rows',len(missing['sections'][2]['rows'])==2)
            check('missing-area pending count 1',any('1 条 pending' in v for v in missing['missing_information']))
    empty=generate('empty',dict(bridge_id='S41-NONEXISTENT'))
    if empty:check('empty no data explicit',not empty['source_document_ids'] and not empty['sections'][2]['rows'] and bool(empty['missing_information']))
    code,_,_=call('/api/reports',dict(scope,expert_requirements='给出评级'));check('free-text rejected 422',code==422)
    code,_,_=call('/api/reports',dict(scope,template_id='rating'));check('other template rejected 422',code==422)
    # New HTTP instance, same graph/files, no processing or model invocation.
    server.should_exit=True;thread.join(15);check('first HTTP stopped',not thread.is_alive())
    app=create_app(s);server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(200):
        if server.started:break
        time.sleep(.05)
    for item in reports:
        code,result,_=call('/api/reports/'+item['report_id']);check('restart read '+item['case'],code==200 and result['status']=='succeeded')
    check('old JSON/PDF files unchanged',all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in prior.items()))
    check('business and vector graph unchanged',snapshot()==before)
finally:
    if server:server.should_exit=True
    if thread:thread.join(15)
    graph.close()
    result={'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'reports':reports,'generation_calls':0,'embedding_calls':0,'parsing_calls':0,'extraction_calls':0,'indexing_calls':0}
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','reports')}))
if result['failed']:raise SystemExit(1)
