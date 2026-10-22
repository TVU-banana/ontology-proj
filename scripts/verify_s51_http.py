"""Final new-route TCP checks. No model/parse/extraction/index calls."""
import json
from pathlib import Path
import httpx

OUT=Path('specs/007-agent-web/results/final-http.json')
if OUT.exists():raise RuntimeError('Preserve existing results')
checks=[];responses={}
def check(name,ok):checks.append({'name':name,'passed':bool(ok)})
with httpx.Client(base_url='http://127.0.0.1:18000',timeout=30) as c:
    for path in ['/','/static/workspace.js?v=3','/static/workspace.css?v=2']:
        r=c.get(path);check(path+' readable',r.status_code==200)
    for role,body in [('knowledge',{'instruction':'查询','scope':{}}),('ingestion',{'instruction':'读取','document_id':'not-existing','role':'admin'})]:
        r=c.post('/api/agents/'+role,json=body);responses[role+' invalid']=r.json();check(role+' rejects before model',r.status_code==422);check(role+' trace',bool(r.headers.get('X-Trace-ID')))
    rid='a9aac0f5-0377-42bd-8c91-211e0abbb243';r=c.get('/api/reports/'+rid);data=r.json();responses['report']=data
    check('report restart readable',r.status_code==200 and data['status']=='succeeded')
    report=data['report'];check('five ordered chapters',[s['section_id'] for s in report['sections']]==['scope','sources','observations','limitations','evidence_index'])
    check('professional review remains not_run',report['professional_review']=='not_run')
    rows=report['sections'][2]['rows'];check('independent 3 area values',sorted(row['values']['area_value'] for row in rows)==['0.04','0.9','2.6'])
    check('unknown dates and synthetic',all(row['values']['observed_at'] is None and row['values']['source_kind']=='synthetic' for row in rows))
    check('negative statement retained',any('桥头铺装平顺，未见明显病害' in row['quote'] for row in report['sections'][3]['rows']))
    r=c.get('/api/reports/'+rid+'/markdown');check('markdown trace and complete',r.status_code==200 and bool(r.headers.get('X-Trace-ID')) and '0.04' in r.text and 'not_run' in r.text)
    (OUT.parent/'new-report.md').write_text(r.text)
    eid=rows[0]['evidence_ids'][0];r=c.get('/api/evidence/'+eid);data=r.json();responses['evidence']=data
    check('evidence validated',r.status_code==200 and bool(data['evidence']['quote']))
    r=c.get(data['pdf_url']);check('PDF original readable',r.status_code==200 and r.content.startswith(b'%PDF'))
OUT.write_text(json.dumps({'checks':checks,'responses':responses},ensure_ascii=False,indent=2))
print(json.dumps({'checks':checks},ensure_ascii=False))
