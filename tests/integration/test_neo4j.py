"""Opt-in actual graph tests. No model calls; preserved S2.2 records are fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j.exceptions import Neo4jError

from ontology_proj.adapters.neo4j_repository import Neo4jRepository, CONSTRAINTS
from ontology_proj.application.ingestion import fingerprint
from ontology_proj.application.files import FileStoreError
from ontology_proj.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'specs/002-ingestion-foundation/results/s2.2/baseline-1.1.4'


@pytest.fixture
def graph():
    config=os.environ.get('S23_NEO4J_CONFIG')
    if not config:pytest.skip('Actual graph tests require explicit S23_NEO4J_CONFIG')
    s=Settings(_env_file=config)
    repository=Neo4jRepository(s.neo4j_uri,s.neo4j_user,s.neo4j_password.get_secret_value(),s.neo4j_database)
    repository.initialize()
    yield repository
    repository.close()


@pytest.fixture
def bundle():
    token=uuid4().hex
    document=json.loads((BASE/'baseline-normalized.json').read_text())
    records=json.loads((BASE/'results/baseline-01/records.json').read_text())
    replacements={document['document_id']:'doc_'+hashlib.sha256(token.encode()).hexdigest(),'B-DEMO-001':'S23-FIXTURE-'+token}
    for plural, field in [('components','component_id'),('defects','defect_id'),('observations','observation_id'),('evidence','evidence_id')]:
        for r in records[plural]:replacements[r[field]]=r[field].split('_')[0]+'_'+hashlib.sha256((token+r[field]).encode()).hexdigest()
    def remap(value):
        if isinstance(value,str):
            for a,b in replacements.items():value=value.replace(a,b)
            return value
        if isinstance(value,list):return [remap(v) for v in value]
        if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
        return value
    document,records=remap(document),remap(records)
    blocks={b['block_id']:b for b in document['blocks']}
    # Renamed fixture codes change text length. Recompute fixture spans, without
    # changing the preserved input or treating this as business validation.
    for e in records['evidence']:
        start=blocks[e['block_id']]['text'].index(e['quote'])
        e['charspan']=[start,start+len(e['quote'])]
    return document,records


def request(use_case='fixture', payload=None):
    return dict(key=uuid4().hex,payload_hash=fingerprint(payload or {}),actor='s23_test_fixture',use_case=use_case)


def register(graph,document):
    graph.register(dict(document_id=document['document_id'],sha256=document['document_id'][4:],source_kind='synthetic',kind='inspection_report',bridge_id=document['import_context']['bridge_id'],original_filename='s23-integration-fixture.pdf',created_at='2026-10-11T00:00:00+00:00'),request())
    op=graph.request_processing(document['document_id'],request(), 'fixture-config',False)['operation_id']
    graph.advance(op,'graph_writing','extracting')
    return op


def test_actual_constraints_repeat_unique_and_ready(graph):
    graph.initialize();assert graph.check().state=='ready'
    with graph._session() as session:
        assert len(set(CONSTRAINTS) & {r['name'] for r in session.run('SHOW CONSTRAINTS YIELD name RETURN name')})==11
        identity='S23-UNIQUE-'+uuid4().hex
        with pytest.raises(Neo4jError):
            def write(tx):
                tx.run('CREATE (:Bridge {bridge_id:$id}), (:Bridge {bridge_id:$id})',id=identity).consume()
            session.execute_write(write)
        assert session.run('MATCH (n:Bridge {bridge_id:$id}) RETURN count(n) AS n',id=identity).single()['n']==0


def test_actual_registration_replay_and_conflict(graph,bundle):
    document,_=bundle
    source=dict(document_id=document['document_id'],sha256=document['document_id'][4:],source_kind='synthetic',kind='inspection_report')
    req=request(payload=source)
    assert not graph.register(source,req)['duplicate']
    assert graph.register(source,req)==graph.replay(req)
    assert graph.register(source,request())['duplicate']
    with pytest.raises(FileStoreError) as error:graph.register(source,{**req,'payload_hash':'other'})
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_actual_business_transaction_roundtrip_replay_and_audit(graph,bundle):
    document,records=bundle;op=register(graph,document)
    graph.commit_bundle(op,document,records,['fixture/records.json'])
    facts=graph.lookup_document(document['document_id'])
    assert [len(facts[k]) for k in ('bridges','components','defects','observations')]==[1,2,3,3]
    assert all(o.get('observed_at') is None and isinstance(o['area_value'],str) for o in facts['observations'])
    assert all(e['text'][slice(*e['evidence']['charspan'])]==e['evidence']['quote'] for e in facts['evidence'])
    graph.commit_bundle(op,document,records,['fixture/records.json'])
    with graph._session() as session:
        assert session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='success:'+op).single()['n']==1
    req=request();assert not graph.request_processing(document['document_id'],req,'fixture-config',False)['enqueue']


def test_actual_mid_transaction_failure_rolls_back_all_new_graph_objects(graph,bundle):
    document,records=bundle;op=register(graph,document)
    def existing_business():
        with graph._session() as session:
            rows=session.run('MATCH (n) WHERE n:Bridge OR n:Component OR n:Defect OR n:Observation RETURN labels(n) AS labels, properties(n) AS props').data()
        return sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in rows)
    before=existing_business()
    original=graph._commit
    def fault(*args):
        original(*args)
        raise RuntimeError('Injected after all graph writes, before transaction commit')
    graph._commit=fault
    with pytest.raises(RuntimeError):graph.commit_bundle(op,document,records,[])
    graph._commit=original
    assert existing_business()==before
    assert graph.lookup_document(document['document_id'],accepted_only=False)['observations']==[]
    assert graph.document(document['document_id'])['processing_status']=='extracting'
    with graph._session() as session:
        assert session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='success:'+op).single()['n']==0
        assert session.run('MATCH (b:Bridge {bridge_id:$id}) RETURN count(b) AS n',id=records['bridges'][0]['bridge_id']).single()['n']==0
    graph.commit_bundle(op,document,records,[])


def test_actual_recovery_interrupts_without_execution_and_preserves_committed(graph,bundle):
    document,records=bundle;op=register(graph,document)
    assert op in graph.recover()
    assert graph.operation(op)['status']=='interrupted'
    assert graph.document(document['document_id'])['processing_status']=='interrupted'
    with pytest.raises(FileStoreError) as error:graph.request_processing(document['document_id'],request(),'changed',True)
    assert error.value.code=='PROCESS_CONFIG_CONFLICT'
    retry=graph.request_processing(document['document_id'],request(),'fixture-config',True)
    assert graph.operation(retry['operation_id'])['retry_of']==op
    graph.advance(retry['operation_id'],'graph_writing','extracting');graph.commit_bundle(retry['operation_id'],document,records,[])
    # Exercise the post-commit diagnostic window without undoing business state.
    with graph._session() as session:session.run('MATCH (o:Operation {operation_id:$id}) SET o.status="running"',id=retry['operation_id']).consume()
    graph.recover()
    assert graph.operation(retry['operation_id'])['status']=='succeeded_with_warnings'
    assert len(graph.lookup_document(document['document_id'])['observations'])==3


def test_actual_existing_shared_conflict_keeps_accepted_object_and_pending_children(graph,bundle):
    document,records=bundle
    old={**records['bridges'][0], 'name':'existing accepted name'};old.pop('evidence_ids');old.pop('quality_score')
    with graph._session() as session:session.run('CREATE (b:Bridge) SET b += $props',props=old).consume()
    op=register(graph,document);graph.commit_bundle(op,document,records,[])
    state=graph.document(document['document_id'])
    assert state['accepted_count']==0 and state['pending_count']==9 and 'OBJECT_CONFLICT' in state['warnings']
    assert graph.lookup_document(document['document_id'])['observations']==[]
    with graph._session() as session:
        b=session.run('MATCH (b:Bridge {bridge_id:$id}) RETURN properties(b) AS b',id=old['bridge_id']).single()['b']
    assert b['name']=='existing accepted name' and b['acceptance_status']=='accepted'


def test_actual_missing_area_pending_and_graph_null(graph,bundle):
    document,records=bundle
    o=next(o for o in records['observations'] if o['observation_code']=='O-002')
    o.update(area_value=None,area_original_value=None,area_is_approximate=None,area_conversion=None,acceptance_status='pending_review')
    records['issues']=[{'code':'AREA_UNNORMALIZABLE'}]
    op=register(graph,document);graph.commit_bundle(op,document,records,[])
    assert graph.document(document['document_id'])['pending_count']==1
    assert len(graph.lookup_document(document['document_id'])['observations'])==2
    all_facts=graph.lookup_document(document['document_id'],accepted_only=False)
    target=next(o for o in all_facts['observations'] if o['observation_code']=='O-002')
    assert target.get('area_value') is None and target.get('area_is_approximate') is None
