"""Actual graph/shared application with explicit parser/model fault fixtures."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ontology_proj.adapters.ingestion_store import IngestionStore, ProcessLease
from ontology_proj.adapters.business_validation import BusinessValidator
from ontology_proj.adapters.document_normalizer import DoclingNormalizer
from ontology_proj.application.business_extraction import ExtractBusiness
from ontology_proj.application.local_parsing import LocalParsingService
from ontology_proj.application.ingestion import IngestionService
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.parsing import ParseOutcome, ParseStatus
from ontology_proj.bootstrap import build_graph, build_readiness
from ontology_proj.interfaces.http.app import create_http_app
from ontology_proj.settings import Settings

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(tmp_path):
    config=os.environ.get('S23_NEO4J_CONFIG')
    if not config:pytest.skip('Actual graph tests require explicit S23_NEO4J_CONFIG')
    settings=Settings(_env_file=config, data_root=tmp_path)
    graph=build_graph(settings);store=IngestionStore(tmp_path)
    native=json.loads((ROOT/'specs/002-ingestion-foundation/results/s2.1/t06-run-003/docling.json').read_text())
    class Parser:
        calls=0
        def configuration(self):return {'fixture':'saved-docling-with-fault-model'}
        def parse(self,path,docid):
            self.calls+=1
            return ParseOutcome(docid,ParseStatus.SUCCESS,native,(),{'fixture':'saved'},0)
    class Extractor:
        calls=0
        def configuration(self):return {'fixture':'invalid-quote-no-provider'}
        def request(self,document):return document
        def extract(self,document):
            self.calls+=1
            candidate=json.loads((ROOT/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1.4/results/baseline-01/candidate.json').read_text())
            old=candidate['document_id'];new=document['document_id']
            candidate=json.loads(json.dumps(candidate).replace(old,new))
            candidate['entities'][0]['sources'][0]['exact_quote']='故障注入：无原文支持'
            return {'response':{'fixture':'not-an-AI-response'},'candidate':candidate,'diagnostics':[]}
    parser=Parser();extractor=Extractor()
    service=IngestionService(store,LocalParsingService(store,parser,DoclingNormalizer()),ExtractBusiness(store,extractor,BusinessValidator()),graph,actor='s23-test-'+uuid4().hex,lease=ProcessLease(store))
    app=create_http_app(build_readiness(settings,graph),service)
    pdf=(ROOT/'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes()+b'\n% S23 fault fixture '+uuid4().hex.encode()+b'\n'
    yield service,graph,store,parser,extractor,app,pdf


def upload(client,pdf,key):
    return client.post('/api/documents',headers={'Idempotency-Key':key},files={'file':('fault-fixture.pdf',pdf,'application/pdf')},data={'meta':json.dumps(dict(kind='inspection_report',source_kind='synthetic',bridge_id='B-DEMO-001'))})


def test_actual_registration_failure_retains_draft_then_duplicate_file_can_register(runtime):
    service,graph,store,parser,extractor,app,pdf=runtime
    original=graph.register
    def fault(*args):raise FileStoreError('GRAPH_UNAVAILABLE','Injected graph registration outage',retryable=True)
    with TestClient(app) as client:
        graph.register=fault
        first=upload(client,pdf,'register-once')
        assert first.status_code==503
        sources=list(store._root.rglob('source.json'));assert len(sources)==1
        docid=json.loads(sources[0].read_text())['document_id']
        assert (sources[0].parent/'registration-failure.json').exists()
        assert client.get('/api/documents/'+docid).status_code==404
        graph.register=original
        second=upload(client,pdf,'register-once')
        assert second.status_code==201 and not second.json()['duplicate']
        assert client.get('/api/documents/'+docid).json()['processing_status']=='uploaded'
        assert parser.calls==extractor.calls==0


def test_actual_invalid_quote_keeps_candidate_diagnostics_and_zero_business_graph(runtime):
    service,graph,store,parser,extractor,app,pdf=runtime
    with TestClient(app) as client:
        docid=upload(client,pdf,'source').json()['document_id']
        request=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':'process'})
        assert request.status_code==202
        op=request.json()['operation_id']
        for _ in range(100):
            status=client.get('/api/operations/'+op).json()
            if status['status'] not in ('queued','running'):break
            time.sleep(.02)
        assert status['status']=='failed' and status['error_code']=='CANDIDATE_REJECTED'
        assert client.get('/api/documents/'+docid).json()['counts']['rejected']==9
        assert len(graph.lookup_document(docid,accepted_only=False)['observations'])==0
        assert store.read_business(docid) is None
        assert client.get('/api/documents/'+docid+'/parsed?artifact=candidates').status_code==200
        assert client.get('/api/documents/'+docid+'/parsed?artifact=normalized').status_code==200
        assert list(store._root.rglob('validation.json'))
        assert parser.calls==extractor.calls==1
        response=client.post('/api/documents/'+docid+'/process',headers={'Idempotency-Key':'new-process'})
        assert response.status_code==409 and response.json()['error']['code']=='EXPLICIT_RETRY_REQUIRED'
        assert parser.calls==extractor.calls==1
        # Tampering is not reinterpreted as successful reuse or repaired output.
        normalized=store._directory(docid)/'normalized.json';normalized.write_text('{}')
        response=client.get('/api/documents/'+docid+'/parsed?artifact=normalized')
        assert response.status_code==409 and response.json()['error']['code']=='ARTIFACT_MISMATCH'
        normalized.unlink()
        response=client.get('/api/documents/'+docid+'/parsed?artifact=normalized')
        assert response.status_code==410 and response.json()['error']['code']=='ARTIFACT_MISSING'
