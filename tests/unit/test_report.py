"""Report behavior and independent data cases; no parser/model/index calls."""
from copy import deepcopy
from uuid import uuid4
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from ontology_proj.application.report import GenerateReport, request_data, render
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.query import LookupGraph
from ontology_proj.adapters.report_store import LocalReportStore
from ontology_proj.interfaces.http.app import create_http_app

class Repository:
    def __init__(self):self.jobs={};self.fail_commit=False;self.commits=0
    def create(self,rid,op,request):self.jobs[rid]={'report_id':rid,'operation_id':op,'status':'queued','request':request}
    def get(self,rid):return deepcopy(self.jobs[rid])
    def advance(self,rid):self.jobs[rid]['status']='running'
    def fail(self,rid,code):self.jobs[rid].update(status='failed',error_code=code)
    def recover(self):
        for v in self.jobs.values():
            if v['status'] in ('running','queued'):v['status']='interrupted'
    def documents(self,scope):return [dict(document_id='doc',original_filename='test.pdf',source_kind='synthetic',processing_status='graph_ready')] if scope['bridge_id']=='B' else []
    def statements(self,*args):return []
    def commit(self,report,snapshots,manifest):
        if self.fail_commit:raise FileStoreError('GRAPH_UNAVAILABLE','fixture')
        self.commits+=1;self.jobs[report['report_id']]['status']='succeeded'

class Lookup:
    def __init__(self):
        self.observation=dict(record_type='Observation',record_id='obs',observation_id='obs',observation_code='O1',component_id='cmp',defect_id='def',observed_at=None,description='疑似，面积约 0.04 m²。',assertion='suspected',scope='single_defect',reported_count=1,source_kind='synthetic',area_original_value='0.04',area_original_unit='m²',area_value='0.04',area_unit='m²',area_conversion='identity',area_is_approximate=True,missing_fields=['observed_at'],review_status='unreviewed',acceptance_status='accepted')
        self.observation['evidence_ids']=['e'+str(i) for i in range(len(FIELDS))]
    def lookup(self,q):
        if q['scope']['bridge_id']!='B':return []
        if q['acceptance_mode']=='pending_review':return []
        if q['scope']['review_mode']=='confirmed_only' and self.observation['review_status']!='confirmed':return []
        if q['query_type']=='bridge_components':return [dict(record_type='Component',record_id='cmp')]
        if self.observation['acceptance_status']!='accepted':return []
        return [deepcopy(self.observation)]

FIELDS=['observation_code','description','assertion','scope','reported_count','area_original_value','area_original_unit','area_is_approximate','observed_at','defect_code']
class Evidence:
    def __init__(self):self.wrong=False;self.missing=False
    def document(self,*args):
        if self.missing:raise FileStoreError('ARTIFACT_MISSING','fixture')
    def read(self,eid):
        return dict(evidence=dict(evidence_id=eid,document_id='other' if self.wrong else 'doc',quote='原文',page_no=1,target_field=FIELDS[int(eid[1:])]),document=dict(document_id='other' if self.wrong else 'doc',filename='test.pdf',source_kind='synthetic'),records=[dict(record_id='obs',acceptance_status='accepted',source_acceptance_status='accepted')])

@pytest.fixture
def service(tmp_path):
    lookup=Lookup();evidence=Evidence();repository=Repository()
    return GenerateReport(LookupGraph(lookup),evidence,repository,LocalReportStore(tmp_path))

@pytest.mark.parametrize('payload',[{}, {'bridge_id':'B','template_id':'unknown'},{'bridge_id':'B','expert_requirements':'给评级'},{'bridge_id':'B','date_from':'2026-02-02','date_to':'2026-01-01'},{'bridge_id':'B','review_mode':'passed'},{'bridge_id':'B','include_synthetic':'yes'},{'bridge_id':'B','unknown':1}])
def test_invalid_report_requests(payload):
    with pytest.raises(FileStoreError):request_data(payload)

def finish(service,request=None):
    result=service.submit(request or {'bridge_id':'B'});service.run(result['report_id']);return result['report_id']

def test_fields_qualifiers_unknowns_and_schema(service):
    rid=finish(service);result=service.get(rid);r=result['report']
    assert result['status']=='succeeded' and r['professional_review']=='not_run'
    assert [s['section_id'] for s in r['sections']]==['scope','sources','observations','limitations','evidence_index']
    row=r['sections'][2]['rows'][0]
    assert row['values']['observed_at'] is None and row['values']['area_value']=='0.04' and row['values']['area_is_approximate'] is True
    assert row['values']['assertion']=='suspected' and '近似' in service.get(rid,True)[0]
    assert r['checks']=={k:'passed' for k in ['schema','references','assertions','artifacts']}

@pytest.mark.parametrize('unit',['㎡','m2','m 2'])
def test_equivalent_raw_area_unit_preserved_in_report(service,unit):
    service.lookup.repository.observation['area_original_unit']=unit
    rid=finish(service);result=service.get(rid)
    assert result['status']=='succeeded'
    row=result['report']['sections'][2]['rows'][0]['values']
    assert row['area_original_unit']==unit and row['area_unit']=='m²'

@pytest.mark.parametrize('unit',['cm²','m','MPa','m3'])
def test_unapproved_unit_does_not_pass_report(service,unit):
    service.lookup.repository.observation['area_original_unit']=unit
    rid=finish(service)
    assert service.get(rid)['status']=='failed'

def test_date_filter_unknown_excluded(service):
    rid=finish(service,dict(bridge_id='B',date_from='2026-01-01'))
    r=service.get(rid)['report'];assert r['sections'][2]['rows']==[]
    assert any('1 条未知日期' in t for t in r['missing_information'])

def test_empty_scope(service):
    rid=finish(service,{'bridge_id':'NONE'});r=service.get(rid)['report']
    assert not r['source_document_ids'] and not r['sections'][2]['rows']

def test_missing_area_not_zero(service):
    o=service.lookup.repository.observation
    for f in ('area_value','area_original_value','area_is_approximate'):o[f]=None
    rid=finish(service);assert service.get(rid)['report']['sections'][2]['rows'][0]['values']['area_value'] is None

def test_registration_failure_retry_same_bytes(service):
    service.repository.fail_commit=True;rid=finish(service)
    assert service.get(rid)['status']=='failed'
    with pytest.raises(FileStoreError):service.get(rid,True)
    before=service.store.read(rid)
    service.repository.fail_commit=False;service.retry(rid);service.run(rid,True)
    assert service.store.read(rid)==before and service.get(rid)['status']=='succeeded'
    service.retry(rid);assert service.repository.commits==1

def test_restart_marks_interrupted_then_registration_retry(service):
    service.repository.fail_commit=True;rid=finish(service)
    service.repository.jobs[rid]['status']='running';service.repository.recover()
    assert service.get(rid)['status']=='interrupted'
    service.repository.fail_commit=False;service.retry(rid);service.run(rid,True)
    assert service.get(rid)['status']=='succeeded'

def test_rejected_or_changed_before_registration(service):
    service.repository.fail_commit=True;rid=finish(service)
    service.lookup.repository.observation['acceptance_status']='rejected'
    service.repository.fail_commit=False;service.retry(rid);service.run(rid,True)
    assert service.get(rid)['error_code']=='GENERATION_VALIDATION_FAILED'

def test_later_rejection_snapshot_kept(service):
    rid=finish(service);before=service.store.read(rid)
    service.lookup.repository.observation['acceptance_status']='rejected'
    assert service.get(rid)['citation_status_changes']==['obs']
    assert service.store.read(rid)==before and service.get(rid,True)[1]==['obs']

@pytest.mark.parametrize('kind',['hash','missing','schema','symlink'])
def test_artifact_integrity(service,kind):
    rid=finish(service);directory=service.store.root/rid;target=directory/'report.md'
    if kind=='hash':target.write_text('tampered')
    elif kind=='missing':target.unlink()
    elif kind=='symlink':target.unlink();target.symlink_to('/etc/hosts')
    else:
        report=service.store.read(rid)[0];report['professional_review']='passed'
        with pytest.raises(FileStoreError):service.store.validate(report)
        return
    with pytest.raises(FileStoreError):service.get(rid)

def test_wrong_scope_evidence_rejected(service):
    service.evidence.wrong=True;rid=finish(service)
    assert service.get(rid)['error_code']=='GENERATION_VALIDATION_FAILED'

def test_missing_field_evidence_rejected(service):
    service.lookup.repository.observation['evidence_ids'].remove('e5')
    rid=finish(service);assert service.get(rid)['error_code']=='GENERATION_VALIDATION_FAILED'

def test_missing_source_rejected(service):
    service.evidence.missing=True;rid=finish(service)
    assert service.get(rid)['error_code']=='ARTIFACT_MISSING'

def test_markdown_injection(service):
    rid=str(uuid4());r=service.build(rid,request_data({'bridge_id':'B'})['scope'])
    r['sections'][2]['rows'][0]['values']['description']='<script>x</script>| [bad](javascript:x)\n# bad'
    md=render(r);assert '<script>' not in md and '[bad]' not in md and '&#124;' in md and '&#35; bad' in md

def test_async_api_and_review(service):
    with TestClient(create_http_app(None,reports=service)) as client:
        result=client.post('/api/reports',json={'bridge_id':'B'})
        assert result.status_code==202;rid=result.json()['report_id']
        service._queue.join()
        assert client.get('/api/reports/'+rid).json()['status']=='succeeded'
        assert client.get('/api/reports/'+rid+'/markdown').status_code==200
        review=client.post('/api/reports/'+rid+'/professional-review')
        assert review.status_code==501 and review.json()['review_status']=='not_run'

def test_pending_count_with_accepted_parent_regression(service):
    repository=service.lookup.repository;original=repository.lookup
    def lookup(q):
        if q['acceptance_mode']=='pending_review' and q['query_type']=='component_observations':
            pending=deepcopy(repository.observation);pending.update(record_id='pending',acceptance_status='pending_review',area_value=None)
            return [pending]
        return original(q)
    repository.lookup=lookup
    rid=finish(service);r=service.get(rid)['report']
    assert len(r['sections'][2]['rows'])==1
    assert any('1 条 pending' in t for t in r['missing_information'])

def test_retry_after_partial_generation_does_not_regenerate(service):
    result=service.submit({'bridge_id':'B'});rid=result['report_id'];service.repository.jobs[rid]['status']='interrupted'
    with pytest.raises(FileStoreError) as error:service.retry(rid)
    assert error.value.code=='ARTIFACT_MISSING' and service.repository.jobs[rid]['status']=='interrupted'

def test_post_does_not_publish_from_file_alone(service):
    service.repository.fail_commit=True;rid=finish(service)
    with TestClient(create_http_app(None,reports=service)) as client:
        result=client.get('/api/reports/'+rid+'/markdown')
        assert result.status_code==409 and result.json()['error']['code']=='REPORT_NOT_PUBLISHED'

def test_report_path_traversal_rejected(service):
    with pytest.raises(FileStoreError):service.store.read('../source')


def test_startup_outage_recovered_on_read_then_retry_without_generation(service,monkeypatch):
    rid=finish(service);service.repository.jobs[rid]['status']='running'
    recover=service.repository.recover;calls=[]
    def intermittent():
        calls.append('recover')
        if len(calls)==1:raise FileStoreError('GRAPH_UNAVAILABLE','fixture outage')
        recover()
    # A new process has files and graph jobs but an empty in-memory queue.
    restarted=GenerateReport(service.lookup,service.evidence,service.repository,service.store)
    monkeypatch.setattr(service.repository,'recover',intermittent)
    restarted.start()
    try:
        assert restarted.get(rid)['status']=='interrupted' and len(calls)==2
        before=restarted.store.read(rid)
        monkeypatch.setattr(restarted.store,'save',lambda *args:pytest.fail('No file regeneration on retry'))
        restarted.retry(rid);restarted._queue.join()
        assert restarted.get(rid)['status']=='succeeded' and len(calls)==2
        assert restarted.store.read(rid)==before
    finally:restarted.close()


def test_failed_recovery_blocks_new_report_before_input_write(service,monkeypatch):
    def outage():raise FileStoreError('GRAPH_UNAVAILABLE','fixture')
    monkeypatch.setattr(service.repository,'recover',outage)
    monkeypatch.setattr(service.store,'save_input',lambda *args:pytest.fail('No input before recovery'))
    with pytest.raises(FileStoreError):service.submit({'bridge_id':'B'})
    assert not service.repository.jobs


def test_recovery_does_not_interrupt_queued_work_in_current_process(service):
    first=service.submit({'bridge_id':'B'})
    assert service.get(first['report_id'])['status']=='queued'
    service.submit({'bridge_id':'B'})
    with pytest.raises(FileStoreError) as error:service.retry(first['report_id'])
    assert error.value.code=='OPERATION_ALREADY_RUNNING'
    assert all(job['status']=='queued' for job in service.repository.jobs.values())


def test_report_trace_and_disabled_record_review(service):
    with TestClient(create_http_app(None,reports=service)) as client:
        response=client.post('/api/reports',json={'bridge_id':'B'})
        rid=response.json()['report_id'];service._queue.join()
        for response in [response,client.get('/api/reports/'+rid),client.post('/api/reports/'+rid+'/retry'),client.post('/api/reports/'+rid+'/professional-review'),client.post('/api/records/obs/review',json={'decision':'confirmed'})]:
            assert response.json()['trace_id']==response.headers['X-Trace-ID']
        assert response.status_code==501 and response.json()['review_status']=='not_run'
        assert service.lookup.repository.observation['review_status']=='unreviewed'
