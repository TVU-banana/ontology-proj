"""One approved S5.2 run: real isolated HTTP/graph/provider and timed sampling."""
import argparse
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from uuid import uuid4

import httpx
import uvicorn
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import create_app, build_graph
from ontology_proj.adapters.fact_extractor import RelayFactExtractor
from ontology_proj.adapters.ollama_embedding import OllamaEmbedding
from ontology_proj.adapters.docling_parser import DoclingParser
from ontology_proj.adapters.graph_queries import Neo4jQueries
from ontology_proj.adapters.report_repository import Neo4jReports
from ontology_proj.adapters.report_store import LocalReportStore
from ontology_proj.adapters.evidence_reader import PublishedEvidenceReader
from ontology_proj.adapters.ingestion_store import IngestionStore
from ontology_proj.application.query import LookupGraph
from ontology_proj.application.report import GenerateReport, request_data, render
from ontology_proj.application.files import FileStoreError
from prepare_system import ROOT, PHASE, OUT, DATA, CONTAINER, sha, write_json


def stable(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


class Run:
    def __init__(self):
        if (OUT/'result.json').exists():
            raise SystemExit('本轮已有不可覆盖结果，拒绝重跑；新实验须采用新运行ID并重新冻结。')
        self.s=Settings(_env_file=DATA/'private/.env.app')
        assert self.s.neo4j_uri=='bolt://127.0.0.1:18687' and self.s.data_root==DATA/'runtime' and self.s.app_port==18100
        self.baseline=json.loads((OUT/'baseline.json').read_text())
        for n,h in json.loads((OUT/'freeze.json').read_text()).items():
            assert sha(ROOT/n)==h, 'Frozen source/config changed'
        self.checks=[];self.reports=[];self.fault_reports=[];self.provider_results=[];self.transport=[];self.embeds=[]
        self.fault=None;self.parse_calls=0;self.stop_event=threading.Event();self.maintenance=threading.Event();self.sample_lock=threading.Lock()
        self.server=None;self.server_thread=None;self.samples=[];self.sampler_error=None;self.restarts=0;self.db_restarts=0
        self.graph=build_graph(self.s)
        self.client=httpx.Client(base_url='http://127.0.0.1:18100',timeout=30)
        expected=json.loads((ROOT/'specs/006-observation-report/expectations.json').read_text())
        self.did=expected['original']['document_id'];self.scope={'document_ids':[self.did]};self.bridge='B-DEMO-001'
        self.expected=expected
        sources=[json.loads(p.read_text()) for p in self.s.data_root.rglob('source.json')]
        self.missing=next(s['document_id'] for s in sources if s['document_id']!=self.did and s.get('source_kind')=='synthetic')
        store=IngestionStore(self.s.data_root)
        records=store.read_business_artifact(self.did,'records')
        self.oid=next(o['observation_id'] for o in records['observations'] if o['observation_code']=='O-001')
        self.pdf_hashes={d:sha(Path(store.source_path(d))) for d in (self.did,self.missing)}
        self.normalized_hashes={d:sha(self.s.data_root/'documents'/d/'normalized.json') for d in (self.did,self.missing)}
        self.old_reports=sorted(p.name for p in (self.s.data_root/'reports').iterdir() if p.is_dir())
        self.old_bytes={n:h for n,h in self.baseline['copied_file_hashes'].items()}
        self.copied_business=None
        self.install_hooks()

    def check(self,name,value,kind='mechanism'):
        item={'name':name,'passed':bool(value),'kind':kind,'elapsed_seconds':round(time.monotonic()-getattr(self,'started',time.monotonic()),3)}
        self.checks.append(item)
        with (OUT/'checks.jsonl').open('a') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
        if not value:
            print(json.dumps({'failed_check':name,'kind':kind},ensure_ascii=False),flush=True)
        return value

    def note(self,name,**extra):
        value={'event':name,'monotonic':time.monotonic(),**extra}
        with (OUT/'events.jsonl').open('a') as f:f.write(json.dumps(value,ensure_ascii=False)+'\n')
        print(json.dumps(value,ensure_ascii=False),flush=True)

    def save(self,name,value):
        raw=json.dumps(value,ensure_ascii=False,indent=2)
        assert not any(s and s in raw for s in [self.s.llm_api_key.get_secret_value(),self.s.neo4j_password.get_secret_value()])
        write_json(OUT/name,value)

    def install_hooks(self):
        self.orig_transport=RelayFactExtractor.extract;self.orig_embed=OllamaEmbedding._call;self.orig_parse=DoclingParser.parse
        owner=self
        def transport(adapter,request):
            assert request['model']==owner.s.llm_model
            assert request.get('max_tokens') in (2000,6000), 'Extraction is outside this run'
            assert adapter.base==owner.s.llm_base_url.rstrip('/')
            raw=json.dumps(request,ensure_ascii=False)
            assert not any(s and s in raw for s in [owner.s.llm_api_key.get_secret_value(),owner.s.neo4j_password.get_secret_value()])
            assert 'golden.json' not in raw and 'model-expectations.json' not in raw
            if request.get('max_tokens')==6000:
                payload=json.loads(request['messages'][1]['content']);ctx=payload['provided_context']
                assert ctx['scope']['document_ids']==[owner.did]
                assert all(r['source_kinds']==['synthetic'] for r in ctx['records'].values())
                assert all(e['source_kind']=='synthetic' and e['document_id']==owner.did for e in ctx['evidence'].values())
            index=len(owner.transport)+1;started=time.monotonic();fault=owner.fault=='model'
            owner.save(f'transport-{index:02d}-request.json',request)
            item={'index':index,'request_sha256':stable(request),'fault':fault,'destination':adapter.base if not fault else 'closed_loopback_port_9','model':request['model']}
            owner.transport.append(item)
            prior=adapter.base
            try:
                if fault:
                    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',9))!=0
                    adapter.base='http://127.0.0.1:9/v1'
                result=owner.orig_transport(adapter,request)
                owner.save(f'transport-{index:02d}-response.json',result)
                response=result.get('response') or {}
                item.update(error_code=result.get('error_code'),response_model=response.get('model'),usage=response.get('usage'))
                return result
            finally:
                adapter.base=prior;item['elapsed_seconds']=round(time.monotonic()-started,3)
        def embedding(adapter,path,body=None):
            prior=adapter.base_url
            if path=='/api/embed':owner.embeds.append({'fault':owner.fault=='embedding','model':body['model'],'input_count':len(body['input'])})
            try:
                if owner.fault=='embedding':adapter.base_url='http://127.0.0.1:9'
                return owner.orig_embed(adapter,path,body)
            finally:adapter.base_url=prior
        def parse(*args,**kwargs):
            owner.parse_calls+=1
            raise RuntimeError('New Docling conversion is outside the frozen run')
        RelayFactExtractor.extract=transport;OllamaEmbedding._call=embedding;DoclingParser.parse=parse

    def api(self,path,body=None,timeout=30):
        r=self.client.get(path,timeout=timeout) if body is None else self.client.post(path,json=body,timeout=timeout)
        return r.status_code,r.json()

    def start_app(self):
        self.server=uvicorn.Server(uvicorn.Config(create_app(self.s),host='127.0.0.1',port=18100,log_level='error',access_log=False))
        self.server_thread=threading.Thread(target=self.server.run,daemon=True);self.server_thread.start()
        start=time.monotonic()
        while time.monotonic()-start<30:
            if self.server.started:
                self.note('app_started',pid=os.getpid(),duration_seconds=round(time.monotonic()-start,3));return
            if not self.server_thread.is_alive():break
            time.sleep(.1)
        raise RuntimeError('Application startup deadline exceeded')

    def stop_app(self):
        self.server.should_exit=True;self.server_thread.join(30)
        self.check('owned HTTP thread stopped',not self.server_thread.is_alive())
        if self.server_thread.is_alive():raise RuntimeError('Cannot restart while previous app lives')

    def docker(self,*args):
        assert args[-1]==CONTAINER
        r=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=40)
        self.note('docker_action',action=args[0],returncode=r.returncode)
        if r.returncode:raise RuntimeError('Dedicated Docker action failed')

    def business_hash(self):
        labels=['Bridge','Component','Defect','Observation','Evidence','TextBlock','SourceDocument','DocumentChunk']
        with self.graph._session() as session:
            nodes=session.run('MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) RETURN labels(n) AS labels,properties(n) AS props',labels=labels).data()
            rels=session.run('MATCH (a)-[r]->(b) WHERE all(l IN labels(a) WHERE NOT l IN ["Report","Operation","AuditEvent","WriteRequest"]) AND all(l IN labels(b) WHERE NOT l IN ["Report","Operation","AuditEvent","WriteRequest"]) RETURN properties(a) AS a,type(r) AS type,properties(r) AS props,properties(b) AS b').data()
        return stable([sorted(nodes,key=stable),sorted(rels,key=stable)])

    def core(self,number):
        checks=[];responses={};started=time.monotonic()
        def check(name,condition):checks.append({'name':name,'passed':bool(condition)})
        def call(name,path,body=None):
            code,value=self.api(path,body);responses[name]={'http':code,'body':value};return code,value
        code,_=call('health','/health');check('health',code==200)
        code,ready=call('ready','/ready');check('base ready',code==200 and ready['ready'])
        check('configured graph/model/index ready',all(v['status']['state']=='ready' for v in ready['capabilities'] if v['name'] in ('graph','model','index')))
        for did in (self.did,self.missing):
            code,value=call('state-'+did,'/api/documents/'+did);check('state '+did,code==200 and value['processing_status'].startswith('graph_ready'))
            r=self.client.get('/api/documents/'+did+'/file');check('PDF '+did,r.status_code==200 and hashlib.sha256(r.content).hexdigest()==self.pdf_hashes[did])
            r=self.client.get('/api/documents/'+did+'/parsed?artifact=normalized');check('normalized '+did,r.status_code==200 and stable(r.json().get('artifact'))==stable(json.loads((self.s.data_root/'documents'/did/'normalized.json').read_text())))
        code,comps=call('components','/api/graph/lookup',{'query_type':'bridge_components','scope':self.scope});check('two components',code==200 and comps.get('count')==2)
        cids=[r['record_id'] for r in comps.get('records',[])]
        q={'query_type':'component_observations','scope':{**self.scope,'component_ids':cids}}
        code,obs=call('observations','/api/graph/lookup',q)
        rows=obs.get('records',[])
        check('areas/unknown dates/synthetic',code==200 and {r['observation_code']:r['area_value'] for r in rows}=={'O-001':'0.04','O-002':'2.6','O-003':'0.9'} and all(r['observed_at'] is None and r['area_unit']=='m²' and r['source_kinds']==['synthetic'] for r in rows))
        for name,scope in [('no-synthetic',{'include_synthetic':False}),('confirmed',{'review_mode':'confirmed_only'}),('unknown-date-filter',{'date_from':'2026-01-01'})]:
            code,value=call(name,'/api/graph/lookup',{**q,'scope':{**q['scope'],**scope}});check(name,code==200 and value.get('count')==0)
        mscope={'document_ids':[self.missing],'component_ids':cids}
        code,accepted=call('missing-accepted','/api/graph/lookup',{'query_type':'component_observations','scope':mscope})
        code2,pending=call('missing-pending','/api/graph/lookup',{'query_type':'component_observations','scope':mscope,'acceptance_mode':'pending_review'})
        check('missing area2accepted1pending',code==200 and code2==200 and accepted.get('count')==2 and pending.get('count')==1 and pending['records'][0].get('area_value') is None)
        eids=sorted({e for r in rows for e in r['evidence_ids']})
        for eid in eids:
            code,e=call('evidence-'+eid,'/api/evidence/'+eid)
            valid=code==200 and e['document']['document_id']==self.did and e['block_text'][slice(*e['evidence']['charspan'])]==e['evidence']['quote'] and e['page_geometry'] is not None
            check('quote/page/span '+eid,valid)
        for rid in self.old_reports[:1]+list(self.reports)+list(self.fault_reports):
            code,value=call('report-'+rid,'/api/reports/'+rid);check('report readable '+rid,code==200 and value.get('status')=='succeeded')
        current=self.business_hash();check('business graph unchanged',current==self.copied_business)
        oldchanged=[n for n,h in self.old_bytes.items() if not (self.s.data_root/n).is_file() or sha(self.s.data_root/n)!=h]
        check('seed files unchanged',not oldchanged)
        result={'number':number,'started_monotonic':started,'elapsed_seconds':round(time.monotonic()-self.started,3),'duration_seconds':round(time.monotonic()-started,3),
            'checks':checks,'response_sha256':{n:stable({k:v for k,v in r['body'].items() if k!='trace_id'}) for n,r in responses.items()},'business_sha256':current,'passed':all(c['passed'] for c in checks)}
        if number==1:self.save('core-first-responses.json',responses)
        if not result['passed']:self.save(f'sample-{number:03d}-failure-responses.json',responses)
        return result

    def sampler(self):
        next_tick=self.started
        try:
            while not self.stop_event.is_set():
                delay=max(0,next_tick-time.monotonic())
                if self.stop_event.wait(delay):break
                next_tick=max(next_tick+30,time.monotonic()+30)
                with self.sample_lock:
                    if self.maintenance.is_set():
                        self.note('maintenance_sampling_skipped');continue
                    try:result=self.core(len(self.samples)+1)
                    except Exception as exc:
                        result={'number':len(self.samples)+1,'passed':False,'error_type':type(exc).__name__,'elapsed_seconds':round(time.monotonic()-self.started,3)}
                    self.samples.append(result)
                    with (OUT/'samples.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
                    (OUT/'progress.json').write_text(json.dumps({'samples':len(self.samples),'sample_failures':sum(not s['passed'] for s in self.samples),'elapsed_seconds':round(time.monotonic()-self.started,3),'reports':len(self.reports),'model_tasks':len(self.provider_results),'checks_failed':sum(not c['passed'] for c in self.checks),'stage':'running'},ensure_ascii=False))
                    if len(self.samples)%2==0:self.note('sampling_progress',samples=len(self.samples),failed=sum(not s['passed'] for s in self.samples))
        except Exception as exc:
            self.sampler_error=type(exc).__name__;self.note('sampler_error',error_type=self.sampler_error)

    def report_check(self,rid):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            code,state=self.api('/api/reports/'+rid)
            if code==200 and state.get('status') not in ('running','queued'):break
            time.sleep(.1)
        self.check('report '+rid+' succeeded',code==200 and state.get('status')=='succeeded')
        if code!=200 or state.get('status')!='succeeded':return False
        report=state['report'];self.save('report-'+rid+'.json',state)
        rows=report['sections'][2]['rows']
        self.check('report '+rid+' independent assertions',[s['section_id'] for s in report['sections']]==self.expected['sections'] and {r['values']['observation_code']:r['values']['area_value'] for r in rows}==self.expected['original']['areas'] and all(r['values']['observed_at'] is None and r['values']['source_kind']=='synthetic' for r in rows) and report['professional_review']=='not_run' and any(r['kind']=='negative' and r['quote']==self.expected['original']['negative_quote'] for r in report['sections'][3]['rows']))
        code,retry=self.api('/api/reports/'+rid+'/retry',{})
        self.check('report retry reused '+rid,code==202 and retry.get('reused'))
        with self.graph._session() as session:
            r=session.run('MATCH (r:Report {report_id:$id}) RETURN count(r) AS n',id=rid).single()['n']
            a=session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='report-success:'+state['operation_id']).single()['n']
        self.check('single report/audit '+rid,r==1 and a==1)
        return True

    def new_report(self):
        code,result=self.api('/api/reports',{'bridge_id':self.bridge,**self.scope});self.check('normal report accepted',code==202)
        if code==202 and self.report_check(result['report_id']):self.reports.append(result['report_id'])
        self.note('normal_report_result',created=len(self.reports))

    def draft(self):
        queries=Neo4jQueries(self.graph);store=IngestionStore(self.s.data_root)
        service=GenerateReport(LookupGraph(queries),PublishedEvidenceReader(queries,store),Neo4jReports(self.graph,self.s.actor_id),LocalReportStore(self.s.data_root))
        rid=str(uuid4());req=request_data({'bridge_id':self.bridge,**self.scope})
        service.store.save_input(rid,req);service.repository.create(rid,str(uuid4()),req);service.repository.advance(rid)
        report=service.build(rid,req['scope']);service.store.save(report,render(report))
        hashes={n:sha(self.s.data_root/'reports'/rid/n) for n in ['report.json','report.md','manifest.json']}
        return rid,hashes

    def restart(self,kind):
        self.maintenance.set()
        with self.sample_lock:
            started=time.monotonic();rid=None;hashes=None
            if kind in ('draft','outage'):rid,hashes=self.draft()
            self.stop_app()
            if kind=='outage':
                self.docker('stop',CONTAINER);self.db_restarts+=1
            self.start_app();self.restarts+=1
            if kind=='outage':
                code,_=self.api('/health');self.check('F01 health while graph down',code==200)
                code,value=self.api('/ready');self.save('F01-unready.json',value);self.check('F01 actual graph unavailable',code==503 and any(c['name']=='graph' and c['status']['error_code']=='GRAPH_UNAVAILABLE' for c in value['capabilities']))
                code,value=self.api('/api/graph/lookup',{'query_type':'bridge_components','scope':self.scope});self.check('F01 graph lookup error',code==503 and value['error']['code']=='GRAPH_UNAVAILABLE')
                self.docker('start',CONTAINER)
            limit=120 if kind=='outage' else 30
            while time.monotonic()-started<limit:
                try:
                    code,value=self.api('/api/documents/'+self.did)
                    if code==200 and value['processing_status'].startswith('graph_ready'):break
                except Exception:pass
                time.sleep(1)
            self.check(kind+' restart recovery deadline',time.monotonic()-started<limit and code==200)
            if rid:
                code,value=self.api('/api/reports/'+rid);self.save('fault-'+kind+'-interrupted.json',value)
                self.check(kind+' unfinished report interrupted',code==200 and value.get('status')=='interrupted' and value.get('error_code')=='PROCESS_INTERRUPTED')
                code,result=self.api('/api/reports/'+rid+'/retry',{});self.check(kind+' explicit registration retry',code==202 and result.get('reused'))
                if self.report_check(rid):self.fault_reports.append(rid)
                self.check(kind+' retry preserves saved bytes',all(sha(self.s.data_root/'reports'/rid/n)==h for n,h in hashes.items()))
            self.note('restart_complete',kind=kind,duration_seconds=round(time.monotonic()-started,3),app_restarts=self.restarts,db_restarts=self.db_restarts)
        self.maintenance.clear()

    def faults(self):
        self.maintenance.set()
        with self.sample_lock:
            request={'query':'桥头未见病害','scope':self.scope,'graph_query':{'query_type':'bridge_components'},'top_k':20}
            self.fault='embedding'
            try:
                code,value=self.api('/api/context',request);self.save('F02-context.json',value)
                self.check('F02 connection refusal preserves graph',code==200 and value.get('status')=='degraded' and value.get('error_code')=='EMBEDDING_UNAVAILABLE' and len(value.get('facts',[]))==2)
            finally:self.fault=None
            code,value=self.api('/api/semantic/search',{'query':'桥头未见病害','scope':self.scope,'top_k':5});self.save('F02-recovered.json',value);self.check('F02 recovery',code==200 and value.get('status')=='ready')
            self.fault='model'
            try:
                code,value=self.api('/api/answers',{'question':'请列出构件名称。','scope':self.scope,'graph_query':{'query_type':'bridge_components'}},timeout=420);self.save('F03-failed-answer.json',value)
                self.check('F03 no false answer on transport refusal',code==503 and value.get('error',{}).get('code')=='MODEL_TRANSPORT_FAILED' and 'claims' not in value)
            finally:self.fault=None
            store=IngestionStore(self.s.data_root);pdf=Path(store.source_path(self.did));backup=pdf.with_name('source.pdf.s52-fault')
            code,comps=self.api('/api/graph/lookup',{'query_type':'bridge_components','scope':self.scope});eid=comps['records'][0]['evidence_ids'][0]
            pdf.rename(backup)
            try:
                code,value=self.api('/api/evidence/'+eid);self.save('F04-missing.json',value);self.check('F04 missing PDF explicit',code==410 and value.get('error',{}).get('code')=='ARTIFACT_MISSING')
            finally:backup.rename(pdf)
            code,value=self.api('/api/evidence/'+eid);self.check('F04 restored reference',code==200 and sha(pdf)==self.pdf_hashes[self.did])
        self.maintenance.clear()

    def model_suite(self):
        tasks=json.loads((OUT/'model-tasks.json').read_text());oracle=json.loads((OUT/'model-expectations.json').read_text())
        for task in tasks:
            body=copy.deepcopy(task['body']);selector=body.pop('selector',None)
            if selector:body['graph_query']={'query_type':'record_by_id','record_id':self.oid}
            body['scope']=self.scope
            before=len(self.transport);started=time.monotonic()
            try:
                code,value=self.api('/api/answers',body,timeout=420);self.save('answer-'+task['id']+'.json',{'request':body,'http':code,'body':value})
                expected=oracle[task['id']];ok=code==200 and value.get('status')==expected['status'] and len(self.transport)-before==1
                claims=value.get('claims',[])
                if 'names' in expected:ok &= {a['value'] for c in claims for a in c['assertions'] if a['field']=='name'}==set(expected['names'])
                if 'field' in expected:ok &= any(a['field']==expected['field'] and a['value']==expected['value'] and a['unit']==expected['unit'] for c in claims for a in c['assertions'])
                if 'reason' in expected:ok &= expected['reason'] in value.get('reason_codes',[]) and not claims
                if 'source_contains' in expected:ok &= any(c['kind']=='source_statement' and expected['source_contains'] in c['text'] for c in claims)
                for c in claims:
                    for eid in c['evidence_ids']:
                        ec,e=self.api('/api/evidence/'+eid);ok &= ec==200 and e['document']['document_id']==self.did and e['block_text'][slice(*e['evidence']['charspan'])]==e['evidence']['quote']
                item={'task':task['id'],'http_status':code,'passed':bool(ok),'duration_seconds':round(time.monotonic()-started,3),'transport_calls':len(self.transport)-before}
            except Exception as exc:item={'task':task['id'],'passed':False,'error_type':type(exc).__name__}
            self.provider_results.append(item);self.check('provider '+task['id'],item['passed'],'provider');self.note('provider_task',**item)
        for role in ('ingestion','knowledge'):
            for index in (1,2):
                body={'document_id':self.did,'instruction':'只调用一次get_ingestion_result读取资料处理结果，资料已完成，不要process，随后结束。'} if role=='ingestion' else {'scope':self.scope,'instruction':'只调用一次graph_lookup，用bridge_components读取当前资料构件，随后结束，不问答、不生成报告。'}
                before=len(self.transport)
                try:
                    code,value=self.api('/api/agents/'+role,body,timeout=420);self.save(f'agent-{role}-{index}.json',{'request':body,'http':code,'body':value})
                    events=value.get('events',[]);wanted='get_ingestion_result' if role=='ingestion' else 'graph_lookup'
                    good=[e for e in events if e['tool_name']==wanted and e['result']['ok']]
                    ok=code==200 and value.get('status')=='completed' and len(good)==1 and len(events)==1 and len(self.transport)-before>=2
                    if good:
                        result=good[0]['result']['data'];ok &= result.get('document_id')==self.did if role=='ingestion' else len(result.get('records',[]))==2
                    item={'task':role+'-'+str(index),'passed':bool(ok),'http_status':code,'transport_calls':len(self.transport)-before,'run_id':value.get('run_id')}
                except Exception as exc:item={'task':role+'-'+str(index),'passed':False,'error_type':type(exc).__name__}
                self.provider_results.append(item);self.check('provider '+item['task'],item['passed'],'provider');self.note('provider_task',**item)

    def duplicates(self):
        meta={'kind':'inspection_report','source_kind':'synthetic','bridge_id':self.bridge};key='s52-system-20261012-01-upload'
        raw=Path(IngestionStore(self.s.data_root).source_path(self.did)).read_bytes()
        responses=[]
        for _ in range(2):
            r=self.client.post('/api/documents',headers={'Idempotency-Key':key},files={'file':('bridge-demo-001.pdf',raw)},data={'meta':json.dumps(meta)})
            value=r.json();responses.append({'http':r.status_code,'body':value});self.check('duplicate upload reused',r.status_code==200 and value.get('document_id')==self.did and value.get('duplicate'))
        r=self.client.post('/api/documents',headers={'Idempotency-Key':key},files={'file':('bridge-demo-001.pdf',raw)},data={'meta':json.dumps({**meta,'bridge_id':'OTHER'})});responses.append({'http':r.status_code,'body':r.json()});self.check('same key conflicting payload 409',r.status_code==409 and r.json()['error']['code']=='IDEMPOTENCY_CONFLICT')
        for _ in range(2):
            r=self.client.post('/api/documents/'+self.did+'/process',headers={'Idempotency-Key':'s52-system-20261012-01-process'});self.check('duplicate process reuse',r.status_code==202 and r.json().get('reused'))
        self.save('duplicate-responses.json',responses)

    def execute(self):
        self.started=time.monotonic();sampler=None;failure=None
        try:
            self.start_app();self.copied_business=self.business_hash();self.duplicates()
            self.faults()
            # Normal soak starts after deliberate dependency faults, to keep its
            # lifetime distinguishable from fault recovery and provider warmup.
            self.started=time.monotonic();sampler=threading.Thread(target=self.sampler,daemon=True);sampler.start()
            self.model_suite()
            self.check('F03 recovered via next new budgeted answer',bool(self.provider_results and self.provider_results[0]['passed']))
            events=set();reports_due=[1,12,24,36,48,60,72,84,96,108]
            while time.monotonic()-self.started<3600 or len(self.samples)<120:
                if self.sampler_error:raise RuntimeError('Sampler failed')
                count=len(self.samples)
                for point in reports_due:
                    if count>=point and ('report',point) not in events:
                        events.add(('report',point));self.new_report()
                for point,kind in [(20,'normal'),(50,'draft'),(80,'outage')]:
                    if count>=point and ('restart',point) not in events:
                        events.add(('restart',point));self.restart(kind)
                if self.stop_event.wait(.5):break
            self.check('minimum monotonic duration',time.monotonic()-self.started>=3600,'continuous')
            self.check('at least120 normal samples',len(self.samples)>=120,'continuous')
            self.check('all normal samples passed',all(r['passed'] for r in self.samples),'continuous')
            self.check('ten normal reports',len(self.reports)==10,'continuous')
            self.check('three app one graph restart',self.restarts==3 and self.db_restarts==1,'continuous')
            self.check('nine first model tasks',len(self.provider_results)==9 and all(r['passed'] for r in self.provider_results),'provider')
            self.check('business stable',self.business_hash()==self.copied_business,'continuous')
            self.check('zero new parser/extraction',self.parse_calls==0 and all(t['model']==self.s.llm_model for t in self.transport),'continuous')
        except Exception as exc:
            failure=type(exc).__name__;self.check('unhandled run failure',False);self.note('run_exception',error_type=failure)
        finally:
            self.stop_event.set()
            if sampler:sampler.join(60)
            if self.server_thread and self.server_thread.is_alive():self.stop_app()
            self.client.close();self.graph.close()
            RelayFactExtractor.extract=self.orig_transport;OllamaEmbedding._call=self.orig_embed;DoclingParser.parse=self.orig_parse
            result={'complete':failure is None,'elapsed_seconds':round(time.monotonic()-self.started,3),'samples':len(self.samples),'sample_failures':sum(not s['passed'] for s in self.samples),
                'normal_reports':self.reports,'fault_reports':self.fault_reports,'application_restarts':self.restarts,'database_restarts':self.db_restarts,
                'provider_tasks':self.provider_results,'transport_requests':self.transport,'embedding_requests':self.embeds,'parser_calls':self.parse_calls,
                'checks':self.checks,'passed':failure is None and all(c['passed'] for c in self.checks),'failure_type':failure,
                'boundary':'isolated reuse of synthetic publications; actual HTTP/graph/provider; no new conversion/extraction or professional acceptance'}
            self.save('result.json',result)
            (OUT/'progress.json').write_text(json.dumps({'stage':'finished','passed':result['passed'],'samples':len(self.samples),'sample_failures':result['sample_failures']},ensure_ascii=False))
            self.note('run_finished',passed=result['passed'],samples=len(self.samples))
        return result


if __name__=='__main__':
    for name in ['neo4j','neo4j.io','neo4j.pool']:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    runner=Run();result=runner.execute();raise SystemExit(0 if result['passed'] else 1)
