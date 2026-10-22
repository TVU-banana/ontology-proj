"""Three real fresh process launches, only after the timed app has exited."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
from ontology_proj.settings import Settings
from prepare_system import ROOT, OUT, DATA, sha, write_json


def main():
    old=json.loads((OUT/'result.json').read_text())
    assert old['complete'], 'Timed run must finish before new process trials'
    s=Settings(_env_file=DATA/'private/.env.app')
    assert s.data_root==DATA/'runtime' and s.neo4j_uri=='bolt://127.0.0.1:18687' and s.app_port==18100
    result_dir=OUT/'process-restarts';result_dir.mkdir(exist_ok=False)
    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',18100))!=0, 'Previous HTTP still active'
    expected=json.loads((ROOT/'specs/006-observation-report/expectations.json').read_text());did=expected['original']['document_id'];scope={'document_ids':[did]}
    rounds=[]
    for index in (1,2,3):
        checks=[];pid=None;started=time.monotonic();failed=None
        with (result_dir/f'process-{index}.log').open('x') as log:
            process=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','ontology_proj','--env-file',str(DATA/'private/.env.app')],cwd=ROOT,
                env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=log)
            pid=process.pid
            try:
                with httpx.Client(base_url='http://127.0.0.1:18100',timeout=30) as client:
                    deadline=started+30
                    while time.monotonic()<deadline:
                        assert process.poll() is None, 'New process exited before health'
                        try:
                            if client.get('/health').status_code==200 and client.get('/ready').status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.1)
                    checks.append({'name':'ready within30seconds','passed':time.monotonic()<deadline})
                    r=client.post('/api/graph/lookup',json={'query_type':'bridge_components','scope':scope});components=r.json()['records'];checks.append({'name':'two components','passed':r.status_code==200 and len(components)==2})
                    r=client.post('/api/graph/lookup',json={'query_type':'component_observations','scope':{**scope,'component_ids':[c['record_id'] for c in components]}});obs=r.json()['records']
                    checks.append({'name':'three fixed areas and unknown dates','passed':r.status_code==200 and {o['observation_code']:o['area_value'] for o in obs}==expected['original']['areas'] and all(o['observed_at'] is None for o in obs)})
                    for eid in sorted({e for o in obs for e in o['evidence_ids']}):
                        r=client.get('/api/evidence/'+eid);e=r.json();checks.append({'name':'source span '+eid,'passed':r.status_code==200 and e['document']['document_id']==did and e['block_text'][slice(*e['evidence']['charspan'])]==e['evidence']['quote']})
                    r=client.get('/api/documents/'+did+'/file');checks.append({'name':'PDF hash','passed':r.status_code==200 and hashlib.sha256(r.content).hexdigest()==sha(s.data_root/'documents'/did/'source.pdf')})
                    r=client.get('/api/documents/'+did+'/parsed');checks.append({'name':'normalized readable','passed':r.status_code==200 and r.json()['artifact']==json.loads((s.data_root/'documents'/did/'normalized.json').read_text())})
                    for rid in old['normal_reports']+old['fault_reports']:
                        r=client.get('/api/reports/'+rid);v=r.json();checks.append({'name':'report persisted '+rid,'passed':r.status_code==200 and v.get('status')=='succeeded'})
                        r=client.get('/api/reports/'+rid+'/markdown');manifest=json.loads((s.data_root/'reports'/rid/'manifest.json').read_text());checks.append({'name':'Markdown persisted '+rid,'passed':r.status_code==200 and hashlib.sha256(r.content).hexdigest()==manifest['report.md']})
            except Exception as exc:failed=type(exc).__name__;checks.append({'name':'unexpected error','passed':False,'error_type':failed})
            finally:
                process.terminate();process.wait(timeout=30)
                checks.append({'name':'owned process exited','passed':process.poll() is not None})
                with socket.socket() as sock:checks.append({'name':'port released','passed':sock.connect_ex(('127.0.0.1',18100))!=0})
        item={'round':index,'pid':pid,'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks,'passed':all(c['passed'] for c in checks),'failure_type':failed}
        rounds.append(item);write_json(result_dir/f'round-{index}.json',item)
        print(json.dumps({'round':index,'pid':pid,'passed':item['passed']},ensure_ascii=False),flush=True)
    distinct=len({r['pid'] for r in rounds})==3
    result={'rounds':rounds,'distinct_pids':distinct,'passed':distinct and all(r['passed'] for r in rounds),
        'generation_calls':0,'embedding_calls':0,'parsing_calls':0,'boundary':'real sequential OS processes; timed run app had exited; same isolated files/graph only; no provider POST'}
    write_json(result_dir/'result.json',result)
    raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
