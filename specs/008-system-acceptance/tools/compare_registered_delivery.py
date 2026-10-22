import json,hashlib
from pathlib import Path
ROOT=Path('/path/to/user/project/ontology-proj');data=ROOT/'data/s52-real-shengli-revalidated-20261012-01';out=ROOT/'specs/008-system-acceptance/results/real-shengli-revalidated-20261012-01'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
r=read(next((data/'runtime').rglob('records.json'))); report=read(data/'report-reviewed.json'); expected=read(ROOT/'data/s52-local-shengli-20261012-01/review/confirmed-20261012-01/execution-expectations.json');evs=read(data/'actual-evidence.json'); checks=[]
def ck(name,value):checks.append({'name':name,'passed':bool(value)})
ck('independent expected golden hash unchanged',expected['source_golden_sha256']==sha(ROOT/'data/s52-local-shengli-20261012-01/review/confirmed-20261012-01/golden-facts.json'))
ck('scope counts only 1/3/3/3/47/1',[len(r[k]) for k in ['bridges','components','defects','observations','evidence','statements']]==[1,3,3,3,47,1])
items=[]
for e in expected['core_expected_values']:
 comps=[c for c in r['components'] if c['location_text']==e['location_text']];ck(e['fact_id']+' one component',len(comps)==1)
 c=comps[0];ds=[d for d in r['defects'] if d['component_id']==c['component_id']];obs=[o for o in r['observations'] if o['defect_id'] in {d['defect_id'] for d in ds}];ck(e['fact_id']+' one defect/observation',len(ds)==len(obs)==1);o=obs[0]
 ck(e['fact_id']+' area/date/type',o['area_value']==e['area_value_m2'] and o['area_unit']=='m²' and o['observed_at'] is None and c['component_type']==e['component_type'])
 ck(e['fact_id']+' positive single/not approximate',o['assertion']=='positive' and o['scope']=='single_defect' and o['area_is_approximate'] is False)
 oe=[v for v in r['evidence'] if v['evidence_id'] in o['evidence_ids']];ck(e['fact_id']+' physical page',bool(oe) and all(v['page_no'] in e['physical_pages'] for v in oe))
 items.append({'fact_id':e['fact_id'],'component':c['name'],'location_text':ds[0]['location_text'],'area_value':o['area_value'],'area_original_unit':o['area_original_unit'],'observed_at':o['observed_at'],'component_type':c['component_type'],'pages':sorted({v['page_no'] for v in oe})})
ck('paper codes remain null',all(b['bridge_code'] is None for b in r['bridges']) and all(c['component_code'] is None for c in r['components']))
ck('records accepted remains unreviewed',all(v['review_status']=='unreviewed' for k in ['bridges','components','defects','observations'] for v in r[k]))
ck('negative bridgehead not affirmative object',r['statements'][0]['kind']=='negative' and r['statements'][0]['location_text']=='桥头')
ck('47 actual evidence HTTP200 unique exact record set',len(evs)==47 and all(e['http']==200 for e in evs) and {e['evidence_id'] for e in evs}=={v['evidence_id'] for v in r['evidence']})
for e in evs:
 v=e['body']['evidence']; lo,hi=v['charspan']; ck('span '+e['evidence_id'],e['body']['block_text'][lo:hi]==v['quote'] and v['document_id']==r['document_id'] and v['location_level']=='region' and len(v['bbox'])==4 and all(0<=n<=1 for n in v['bbox']) and e['body']['document']['source_kind']=='real')
rows=report['sections'][2]['rows']; ck('report five chapters',[s['section_id'] for s in report['sections']]==['scope','sources','observations','limitations','evidence_index']);ck('report three unique actual observations',{row['observation_id'] for row in rows}=={o['observation_id'] for o in r['observations']} and len(rows)==3)
for row in rows:
 o=next(o for o in r['observations'] if o['observation_id']==row['observation_id']);ck('report actual fields '+row['row_id'],all(row['values'][k]==o[k] for k in ['area_value','area_unit','area_original_unit','area_original_value','observed_at','assertion','scope','description']))
ck('report not professionally reviewed',report['professional_review']=='not_run');ck('report excludes synthetic',report['scope']['include_synthetic'] is False);ck('report deterministic checks',all(v=='passed' for v in report['checks'].values()))
questions=[]
for i in range(1,6):
 a=read(out/f'http-answer-Q{i:02}.json');b=a['body'];ck(f'Q{i:02} HTTP200',a['http']==200)
 if i in (1,2): vals=[str(x['value']) for c in b['claims'] for x in c['assertions'] if x['field']=='area_value']; ck(f'Q{i:02} independent values',sorted(vals)==(['0.04'] if i==1 else ['0.9','2.6']) and b['status']=='answered')
 if i==3:ck('Q03 unknown date',b['status']=='insufficient_evidence' and 'MISSING_DATE' in b['reason_codes'] and b['claims']==[])
 if i==4:ck('Q04 negative source quote',b['status']=='answered' and any('未见明显病害' in c['text'] and c['kind']=='source_statement' and 'negative' in c['semantic_kinds'] for c in b['claims']))
 if i==5:ck('Q05 no professional assertion',b['claims']==[] and 'PROFESSIONAL_NOT_SUPPORTED' in b['reason_codes'])
 questions.append({'question':a['question'],'status':b['status'],'reason_codes':b['reason_codes'],'claims':[c['text'] for c in b['claims']]})
res={'passed':all(c['passed'] for c in checks),'checks':checks,'scope_items':items,'questions':questions,'professional_review':'not_run','boundary':'Frozen F03-F05 pilot only. 47 actual API spans/region boxes, browser p14/p18 separately; no professional position acceptance or whole-PDF accuracy claim.','new_provider_calls':0}
assert not (out/'independent-comparison.json').exists();(out/'independent-comparison.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':res['passed'],'checks':len(checks),'failed':[c for c in checks if not c['passed']]}));assert res['passed']
