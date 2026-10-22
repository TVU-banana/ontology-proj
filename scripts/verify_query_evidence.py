"""Read-only TCP acceptance against independently fixed S3.1 expectations."""
import hashlib
import json
from pathlib import Path
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'specs/003-query-evidence/results/tcp-001'
EXPECTED=json.loads((OUT.parent/'preparation/query-expectations.json').read_text())
BASE='http://127.0.0.1:18000'
checks=[]

def api(path,body=None):
    request=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)


def check(name,condition):
    checks.append({'name':name,'passed':bool(condition)})


def query(type='component_observations',doc=None,**extra):
    return {'query_type':type,'scope':{'document_ids':[doc or EXPECTED['original_document_id']],'component_ids':components if type=='component_observations' else []},**extra}


def main():
    global components
    OUT.mkdir(parents=True,exist_ok=False)
    original=EXPECTED['original_document_id'];missing=EXPECTED['missing_document_id']
    c=api('/api/graph/lookup',query('bridge_components'))
    components=[r['record_id'] for r in c['records']]
    check('two components and expected codes',len(c['records'])==2 and sorted(r['component_code'] for r in c['records'])==sorted(EXPECTED['component_codes']))
    baseline=api('/api/graph/lookup',query())
    check('three accepted areas',sorted(r['area_value'] for r in baseline['records'])==sorted(EXPECTED['original_areas']))
    check('unknown date and synthetic',all(r['observed_at'] is None and r['source_kinds']==['synthetic'] for r in baseline['records']))
    check('minimum area', [r['area_value'] for r in api('/api/graph/lookup',query(area_min='1'))['records']]==['2.6'])
    check('maximum area',sorted(r['area_value'] for r in api('/api/graph/lookup',query(area_max='1'))['records'])==['0.04','0.9'])
    dated=query();dated['scope']['date_from']='2026-01-01';check('unknown excluded by dates',api('/api/graph/lookup',dated)['count']==0)
    real=query();real['scope']['include_synthetic']=False;check('synthetic excluded',api('/api/graph/lookup',real)['count']==0)
    confirmed=query();confirmed['scope']['review_mode']='confirmed_only';check('accepted not confirmed',api('/api/graph/lookup',confirmed)['count']==0)
    miss=api('/api/graph/lookup',query(doc=missing));check('missing accepted only',miss['count']==2)
    pending=api('/api/graph/lookup',query(doc=missing,acceptance_mode='pending_review'));check('missing pending null',pending['count']==1 and pending['records'][0]['area_value'] is None and pending['records'][0]['area_is_approximate'] is None and pending['records'][0]['observation_code']=='O-002')
    rid=baseline['records'][0]['record_id']
    check('record ID isolated from other document',api('/api/graph/lookup',query('record_by_id',doc=missing,record_id=rid))['count']==0)
    scoped=query('record_by_id',record_id=rid);scoped['scope']['bridge_id']='unrelated';check('record ID respects bridge',api('/api/graph/lookup',scoped)['count']==0)
    check('record by ID matches',api('/api/graph/lookup',query('record_by_id',record_id=rid))['count']==1)
    check('defect relationship matches',api('/api/graph/lookup',query('defect_history',defect_id=baseline['records'][0]['defect_id']))['records'][0]['record_id']==rid)
    ids=[eid for r in baseline['records'] for eid in r['evidence_ids']]+pending['records'][0]['evidence_ids'];evidences=[]
    for eid in ids:
        e=api('/api/evidence/'+eid);span=e['evidence']['charspan'];check('exact evidence '+eid,e['block_text'][span[0]:span[1]]==e['evidence']['quote']);evidences.append(e)
    pdf=urllib.request.urlopen(BASE+evidences[0]['pdf_url']).read();check('PDF exact original',hashlib.sha256(pdf).hexdigest()==original[4:])
    for name,q in [('missing scope',{'query_type':'record_by_id','record_id':rid,'scope':{}}),('arbitrary Cypher',{**query(),'cypher':'MATCH (n) RETURN n'}),('invalid decimal',query(area_min='NaN'))]:
        try:api('/api/graph/lookup',q);check(name,False)
        except urllib.error.HTTPError as error:check(name,error.code==422)
    (OUT/'results.json').write_text(json.dumps({'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'generation_calls':0,'original':baseline,'missing':miss,'pending':pending,'components':c,'evidences':evidences},ensure_ascii=False,indent=2)+'\n')
    print({'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'generation_calls':0})
    if not all(c['passed'] for c in checks):raise SystemExit(1)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(OUT));args=parser.parse_args();OUT=Path(args.output)
    try:main()
    except Exception as error:
        (OUT/'failure.json').write_text(json.dumps({'checks':checks,'exception':type(error).__name__,'message':str(error),'generation_calls':0},indent=2)+'\n')
        raise

