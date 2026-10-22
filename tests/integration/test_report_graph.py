"""Opt-in actual transactions; published-record fixture, zero provider calls."""
import json
from uuid import uuid4
import pytest
from test_neo4j import graph,bundle,register
from test_graph_queries import cleanup
from ontology_proj.adapters.graph_queries import Neo4jQueries
from ontology_proj.adapters.report_repository import Neo4jReports
from ontology_proj.adapters.report_store import LocalReportStore
from ontology_proj.application.query import LookupGraph
from ontology_proj.application.report import GenerateReport,request_data
from ontology_proj.application.files import FileStoreError


def test_report_transaction_rollback_retry_and_revocation(graph,bundle,tmp_path):
    doc,records=bundle;did=doc['document_id'];bridge=doc['import_context']['bridge_id'];rids=[]
    op=register(graph,doc)
    try:
        graph.commit_bundle(op,doc,records,[])
        queries=Neo4jQueries(graph);lookup=LookupGraph(queries);repository=Neo4jReports(graph,'s41_report_fixture')
        class Evidence:
            def document(self,*args):pass
            def read(self,eid):
                raw=queries.evidence(eid)
                return dict(evidence=raw['evidence'],document=dict(document_id=did,filename='fixture.pdf',source_kind='synthetic'),records=raw['records'])
        service=GenerateReport(lookup,Evidence(),repository,LocalReportStore(tmp_path))
        rid=str(uuid4());rids.append(rid);request=request_data(dict(bridge_id=bridge,document_ids=[did]));service.store.save_input(rid,request)
        repository.create(rid,str(uuid4()),request);repository.advance(rid)
        report=service.build(rid,request['scope']);manifest=service.store.save(report,__import__('ontology_proj.application.report',fromlist=['render']).render(report));snapshots=service.recheck(report)
        original=graph._audit
        def fail(*args):raise FileStoreError('GRAPH_WRITE_FAILED','injected after Report and CITES writes')
        graph._audit=fail
        try:
            with pytest.raises(FileStoreError):repository.commit(report,snapshots,manifest)
        finally:graph._audit=original
        with graph._session() as session:
            assert session.run('MATCH (r:Report {report_id:$id}) RETURN count(r) AS n',id=rid).single()['n']==0
        assert repository.get(rid)['status']=='running'
        repository.fail(rid,'GRAPH_WRITE_FAILED');service.retry(rid);service.run(rid,True)
        assert service.get(rid)['status']=='succeeded'
        service.retry(rid)
        with graph._session() as session:
            assert session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='report-success:'+repository.get(rid)['operation_id']).single()['n']==1
        # New draft: change a current node after recheck, before transaction.
        rid2=str(uuid4());rids.append(rid2);repository.create(rid2,str(uuid4()),request);repository.advance(rid2)
        r2=service.build(rid2,request['scope']);m2=service.store.save(r2,__import__('ontology_proj.application.report',fromlist=['render']).render(r2));snapshots=service.recheck(r2)
        with graph._session() as session:session.run('MATCH (n:Observation {observation_id:$id}) SET n.acceptance_status="rejected"',id=snapshots[0]['record_id']).consume()
        with pytest.raises(FileStoreError) as error:repository.commit(r2,snapshots,m2)
        assert error.value.code=='GENERATION_VALIDATION_FAILED'
        assert snapshots[0]['record_id'] in service.get(rid)['citation_status_changes']
        # Recovery must not confuse a report Operation with ingestion.
        graph.recover();repository.recover()
        assert repository.get(rid2)['status']=='interrupted'
        with graph._session() as session:
            assert session.run('MATCH (r:Report {report_id:$id}) RETURN count(r) AS n',id=rid2).single()['n']==0
    finally:
        with graph._session() as session:
            session.run('MATCH (r:Report) WHERE r.report_id IN $ids DETACH DELETE r',ids=rids).consume()
            session.run('MATCH (o:Operation {kind:"report"}) WHERE o.report_id IN $ids DETACH DELETE o',ids=rids).consume()
            session.run('MATCH (a:AuditEvent) WHERE a.object_id IN $ids DETACH DELETE a',ids=rids).consume()
        cleanup(graph,did)


def test_report_startup_outage_delayed_recovery_and_saved_file_retry(graph,bundle,tmp_path,monkeypatch):
    """Real graph state/transaction with a one-shot recovery fault; zero providers."""
    doc,records=bundle;did=doc['document_id'];rid=str(uuid4());service=None
    op=register(graph,doc)
    try:
        graph.commit_bundle(op,doc,records,[])
        queries=Neo4jQueries(graph);repository=Neo4jReports(graph,'review_fix_fixture')
        class Evidence:
            def document(self,*args):pass
            def read(self,eid):
                raw=queries.evidence(eid)
                return dict(evidence=raw['evidence'],document=dict(document_id=did,filename='fixture.pdf',source_kind='synthetic'),records=raw['records'])
        service=GenerateReport(LookupGraph(queries),Evidence(),repository,LocalReportStore(tmp_path))
        request=request_data(dict(bridge_id=doc['import_context']['bridge_id'],document_ids=[did]))
        service.store.save_input(rid,request);repository.create(rid,str(uuid4()),request);repository.advance(rid)
        report=service.build(rid,request['scope'])
        from ontology_proj.application.report import render
        service.store.save(report,render(report));before=service.store.read(rid)
        recover=repository.recover;attempts=[]
        def outage_once():
            attempts.append('recover')
            if len(attempts)==1:raise FileStoreError('GRAPH_UNAVAILABLE','injected startup outage')
            recover()
        monkeypatch.setattr(repository,'recover',outage_once)
        service.start()
        assert repository.get(rid)['status']=='running'
        assert service.get(rid)['status']=='interrupted'
        monkeypatch.setattr(service.store,'save',lambda *args:pytest.fail('Retry cannot overwrite files'))
        service.retry(rid);service._queue.join()
        assert service.get(rid)['status']=='succeeded' and len(attempts)==2
        assert service.store.read(rid)==before
        service.retry(rid)
        with graph._session() as session:
            assert session.run('MATCH (r:Report {report_id:$id}) RETURN count(r) AS n',id=rid).single()['n']==1
            assert session.run('MATCH (a:AuditEvent {audit_id:$id}) RETURN count(a) AS n',id='report-success:'+repository.get(rid)['operation_id']).single()['n']==1
    finally:
        if service:service.close()
        with graph._session() as session:
            session.run('MATCH (r:Report {report_id:$id}) DETACH DELETE r',id=rid).consume()
            session.run('MATCH (o:Operation {report_id:$id,kind:"report"}) DETACH DELETE o',id=rid).consume()
            session.run('MATCH (a:AuditEvent {object_id:$id}) DETACH DELETE a',id=rid).consume()
        cleanup(graph,did)
