import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from threading import Thread, Event

import pytest

from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.application.ingestion import IngestionService, fingerprint
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.graph_mapping import project_bundle, LABELS

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1.4'


def fixture_bundle(case='baseline'):
    return json.loads((BASE/(case+'-normalized.json')).read_text()), json.loads((BASE/'results'/(case+'-01')/'records.json').read_text())


def test_projection_preserves_all_business_scalars_and_evidence_positions():
    document, records = fixture_bundle()
    projected = project_bundle(document, records)
    for plural, (label, identity, _) in LABELS.items():
        for original, node in zip(records[plural], projected['nodes'][label]):
            assert node['properties'] == {k:v for k,v in original.items() if k!='evidence_ids'}
    assert projected['evidence']==records['evidence']
    assert all(b['document_id']==document['document_id'] for b in projected['blocks'])


def test_missing_area_projects_as_null_pending_and_not_zero():
    document, records = fixture_bundle('missing-area')
    projected = project_bundle(document, records)
    target = next(n for n in projected['nodes']['Observation'] if n['properties']['observation_code']=='O-002')['properties']
    assert target['area_value'] is None
    assert target['area_is_approximate'] is None
    assert target['acceptance_status']=='pending_review'


def test_projection_rejects_nested_properties_identity_and_large_bundle():
    document, records = fixture_bundle()
    invalid = copy.deepcopy(records);invalid['bridges'][0]['name']={'bad':'object'}
    with pytest.raises(ValueError):project_bundle(document, invalid)
    invalid=copy.deepcopy(records);invalid['document_id']='wrong'
    with pytest.raises(ValueError):project_bundle(document, invalid)
    invalid=copy.deepcopy(records);invalid['observations']*=200
    with pytest.raises(ValueError):project_bundle(document, invalid)


def test_idempotency_canonical_hash_preserves_value_semantics():
    assert fingerprint({'a':1,'b':2})==fingerprint({'b':2,'a':1})
    assert fingerprint({'a':'x y'})!=fingerprint({'a':'xy'})


@pytest.mark.parametrize('key',[None,'','a b','x'*129,'\nsecret'])
def test_invalid_keys_fail_without_any_graph_or_file_call(key):
    service=IngestionService(None,None,None,None)
    with pytest.raises(FileStoreError,match='Idempotency-Key'):
        service._request(key,'upload',{})


def test_idempotency_namespace_separates_actor_and_use_case():
    a=IngestionService(None,None,None,None,actor='a')
    b=IngestionService(None,None,None,None,actor='b')
    assert len({a._request('key','upload',{})['key'],b._request('key','upload',{})['key'],a._request('key','process',{})['key']})==3


def test_pdf_preflight_invalid_encrypted_and_limits(tmp_path):
    from pypdf import PdfWriter
    from io import BytesIO
    store=IngestionStore(tmp_path)
    for bad in (b'',b'not pdf',b'%PDF-corrupt'):
        with pytest.raises(FileStoreError) as error:store.validate_pdf(bad,100)
        assert error.value.code=='INVALID_PDF'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.add_blank_page(width=100,height=100)
    buf=BytesIO();writer.write(buf)
    with pytest.raises(FileStoreError) as error:store.validate_pdf(buf.getvalue(),1)
    assert error.value.code=='PAGE_LIMIT_EXCEEDED'
    writer.encrypt('private');buf=BytesIO();writer.write(buf)
    with pytest.raises(FileStoreError):store.validate_pdf(buf.getvalue(),100)


def test_second_process_lease_refused_and_release_allows_new_writer(tmp_path):
    first=ProcessLease(IngestionStore(tmp_path));second=ProcessLease(IngestionStore(tmp_path))
    first.acquire()
    try:
        with pytest.raises(FileStoreError) as error:second.acquire()
        assert error.value.code=='PROCESS_ALREADY_RUNNING'
    finally:first.release()
    second.acquire();second.release()


def test_stage_lock_does_not_block_immutable_pdf_reads(tmp_path):
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import DocumentKind, SourceKind
    store=IngestionStore(tmp_path)
    pdf=(ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()
    source=store.save_source(pdf,'source.pdf',ImportContext(DocumentKind.INSPECTION_REPORT,SourceKind.SYNTHETIC,'B-DEMO-001')).source
    held=Event();release=Event();finished=Event()
    def pipeline():
        with store.exclusive():held.set();release.wait(3)
    def read():
        assert store.read_pdf(source.document_id)==pdf
        finished.set()
    thread=Thread(target=pipeline);reader=Thread(target=read)
    thread.start();assert held.wait(1);reader.start()
    try:assert finished.wait(1),'Source read must not wait for parsing/model work'
    finally:release.set();thread.join();reader.join()


def test_retry_index_refused_before_graph_access():
    service=IngestionService(None,None,None,None)
    with pytest.raises(FileStoreError) as error:service.request_processing('doc','key',retry=True,stage='index')
    assert error.value.code=='STAGE_NOT_RETRYABLE'


def test_graph_retry_reuses_verified_parse_and_business_without_model(tmp_path):
    document, records=fixture_bundle()
    events=[]
    class Store:
        def read_artifact(self,*args):return document
        def read_business(self,*args):return {'artifacts':{'records':{'key':'saved-records'}}}
        def read_business_artifact(self,*args):return records
    class Graph:
        def operation(self,op):return {'document_id':document['document_id'],'retry_of':'old'}
        def advance(self,*args):events.append(args)
        def commit_bundle(self,*args):events.append('commit')
        def fail(self,*args):pytest.fail('Unexpected pipeline failure')
    class Parsing:
        def process(self,*args,**kwargs):return {'reused':True}
    extraction=SimpleNamespace(validator=SimpleNamespace(validate_records=lambda *args:[]),execute=lambda *args:pytest.fail('No new model call'))
    IngestionService(Store(),Parsing(),extraction,Graph()).run(str(uuid4()))
    assert events[-1]=='commit'


def test_graph_failure_falls_back_to_sanitized_file_and_worker_can_continue(tmp_path):
    class Graph:
        def operation(self,op):raise FileStoreError('GRAPH_UNAVAILABLE','secret raw error')
        def fail(self,*args):raise RuntimeError('secret raw error')
    store=IngestionStore(tmp_path);operation=str(uuid4())
    IngestionService(store,None,None,Graph()).run(operation)
    artifact=tmp_path/'ingestion-failures'/(operation+'.json')
    assert 'secret' not in artifact.read_text()
    assert json.loads(artifact.read_text())['error_code']=='GRAPH_UNAVAILABLE'


@pytest.mark.parametrize('publication',['valid','none','tampered'])
def test_public_graph_retry_without_model_configuration(publication):
    from ontology_proj.adapters.business_validation import BusinessValidator
    document,records=fixture_bundle();calls=[]
    class Store:
        def read_source(self,*args):return None
        def read_artifact(self,*args):return document
        def read_business(self,*args):
            if publication=='tampered':raise FileStoreError('ARTIFACT_MISMATCH','fixture')
            return None if publication=='none' else {'configuration':{'model':'saved'},'artifacts':{'records':{'key':'records'}}}
        def read_business_artifact(self,*args):return records
    class Graph:
        def replay(self,*args):return None
        def request_processing(self,did,request,configuration,retry):
            assert configuration==fingerprint(dict(parser={'parser':'saved'},extractor={'model':'saved'})) and retry
            calls.append('requested');return dict(operation_id='op',enqueue=True)
        def operation(self,op):return dict(document_id=document['document_id'],retry_of='old')
        def advance(self,*args):pass
        def commit_bundle(self,*args):calls.append('committed')
        def fail(self,*args):pytest.fail('Valid publication should commit')
    parsing=SimpleNamespace(parser=SimpleNamespace(configuration=lambda:{'parser':'saved'}),process=lambda *args,**kw:{'reused':True})
    service=IngestionService(Store(),parsing,None,Graph(),validator=BusinessValidator());service._initialized=True
    if publication=='valid':
        service.request_processing(document['document_id'],'offline-retry',retry=True)
        service.run(service._queue.get_nowait());assert calls==['requested','committed']
    else:
        with pytest.raises(FileStoreError) as error:service.request_processing(document['document_id'],'offline-retry',retry=True)
        assert error.value.code==('MODEL_NOT_CONFIGURED' if publication=='none' else 'ARTIFACT_MISMATCH')
        assert not calls


def test_no_model_reuse_still_rejects_invalid_published_record():
    from ontology_proj.adapters.business_validation import BusinessValidator
    document,records=fixture_bundle();records['observations'][0]['area_value']='999'
    failures=[]
    store=SimpleNamespace(read_artifact=lambda *args:document,read_business=lambda *args:{'artifacts':{}},read_business_artifact=lambda *args:records)
    graph=SimpleNamespace(operation=lambda *args:dict(document_id=document['document_id']),advance=lambda *args:None,commit_bundle=lambda *args:pytest.fail('Invalid records cannot commit'),fail=lambda op,code,*args:failures.append(code))
    IngestionService(store,SimpleNamespace(process=lambda *args,**kw:{'reused':True}),None,graph,validator=BusinessValidator()).run('op')
    assert failures==['EXTRACTION_INVALID']


def test_business_runs_ignores_hidden_and_non_directory_files(tmp_path, monkeypatch):
    store = IngestionStore(tmp_path)
    doc_id = "doc_" + "a" * 64
    runs_dir = store._runs(doc_id)
    runs_dir.mkdir(parents=True, exist_ok=True)

    (runs_dir / ".DS_Store").write_text("junk")
    (runs_dir / "random-file.txt").write_text("junk")
    (runs_dir / ".hidden_dir").mkdir()

    valid_uuid = str(uuid4())
    valid_dir = runs_dir / valid_uuid
    valid_dir.mkdir()
    (valid_dir / "diagnostic.json").write_text(json.dumps({
        "run_id": valid_uuid,
        "status": "running",
        "created_at": "2026-10-12T00:00:00Z",
        "configuration": {"fixture": "test"},
    }))
    (valid_dir / "records.json").write_text(json.dumps({"status": "ok"}))

    store.interrupt_business_runs(doc_id)
    interrupted_diag = json.loads((valid_dir / "diagnostic.json").read_text())
    assert interrupted_diag["status"] == "interrupted"
    assert interrupted_diag["error_code"] == "MODEL_REQUEST_OUTCOME_UNKNOWN"

    artifact = store.read_latest_business_artifact(doc_id, "records")
    assert artifact == {"status": "ok"}

    monkeypatch.setattr(store, "read_artifact", lambda *a, **k: {})
    new_run = store.begin_business(doc_id, {"fixture": "test"}, fresh=True)
    assert new_run["run_id"] is not None


