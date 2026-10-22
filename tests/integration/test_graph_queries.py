"""Explicit synthetic query mechanisms; fixture dates are not new inspections."""
import json
import pytest
from test_neo4j import graph, bundle, register
from ontology_proj.adapters.graph_queries import Neo4jQueries
from ontology_proj.application.query import LookupGraph
from ontology_proj.application.files import FileStoreError


def cleanup(graph,id):
    with graph._session() as session:
        ops=[r['id'] for r in session.run('MATCH (o:Operation {document_id:$id}) RETURN o.operation_id AS id',id=id)]
        keys=[r['key'] for r in session.run('MATCH (w:WriteRequest {actor:"s23_test_fixture"}) RETURN w.idempotency_key AS key,w.result_json AS result') if json.loads(r['result']).get('document_id')==id]
        session.run('MATCH (w:WriteRequest) WHERE w.idempotency_key IN $keys DELETE w',keys=keys).consume()
        session.run('MATCH (a:AuditEvent) WHERE a.actor="s23_test_fixture" AND a.object_id IN $ids DELETE a',ids=[id,*ops]).consume()
        session.run('MATCH (n) WHERE n.document_id=$id OR n.source_document_id=$id DETACH DELETE n',id=id).consume()


def test_actual_dates_decimal_scope_and_fixed_relationships(graph,bundle):
    document,records=bundle;id=document['document_id']
    # These are deliberate mechanics fixtures, not claimed extracted facts.
    a,b=records['observations'][:2]
    b['defect_id']=a['defect_id'];b['component_id']=a['component_id']
    a['observed_at']='2026-01-01';b['observed_at']='2026-02-01';a['area_value']='10.0'
    op=register(graph,document)
    try:
        graph.commit_bundle(op,document,records,[])
        use=LookupGraph(Neo4jQueries(graph));scope={'document_ids':[id]}
        q={'query_type':'defect_history','defect_id':a['defect_id'],'scope':scope}
        assert use.execute(q)['count']==2
        dated={**q,'scope':{**scope,'date_from':'2026-02-01','date_to':'2026-02-01'}}
        assert [r['record_id'] for r in use.execute(dated)['records']]==[b['observation_id']]
        assert [r['record_id'] for r in use.execute({**q,'area_min':'3'})['records']]==[a['observation_id']]
        assert use.execute({**q,'scope':{**scope,'bridge_id':'unrelated'}})['count']==0
        assert use.execute({**q,'scope':{**scope,'component_ids':['unrelated']}})['count']==0
        assert use.execute({**q,'scope':{**scope,'include_synthetic':False}})['count']==0
        with graph._session() as session:
            session.run('MATCH (n:Observation {observation_id:$id}) SET n.review_status="confirmed"',id=a['observation_id']).consume()
        assert use.execute({**q,'scope':{**scope,'review_mode':'confirmed_only'}})['count']==1
        with graph._session() as session:
            session.run('MATCH (c:Component {component_id:$id})-[r:SUPPORTED_BY]->(e:Evidence {document_id:$doc}) SET r.acceptance_status="pending_review"',id=a['component_id'],doc=id).consume()
        inspector=use.execute({'query_type':'bridge_components','scope':scope,'acceptance_mode':'pending_review'})
        assert inspector['count']==1
        assert inspector['records'][0]['acceptance_status']=='accepted'
        assert inspector['records'][0]['source_acceptance_status']=='pending_review'
    finally:cleanup(graph,id)


def test_actual_501_candidate_limit(graph,bundle):
    document,records=bundle;id=document['document_id'];op=register(graph,document)
    try:
        graph.commit_bundle(op,document,records,[])
        observation=records['observations'][0]
        with graph._session() as session:
            session.run('''MATCH (f:Defect {defect_id:$defect})
MATCH (e:Evidence {evidence_id:$evidence})
UNWIND range(1,501) AS i
CREATE (n:Observation {observation_id:$prefix+toString(i),source_document_id:$doc,
acceptance_status:"accepted",review_status:"unreviewed",defect_id:$defect})
CREATE (f)-[:HAS_OBSERVATION]->(n)
CREATE (n)-[:SUPPORTED_BY {acceptance_status:"accepted"}]->(e)''',defect=observation['defect_id'],evidence=observation['evidence_ids'][0],prefix=id+':limit:',doc=id).consume()
        with pytest.raises(FileStoreError) as e:
            LookupGraph(Neo4jQueries(graph)).execute({'query_type':'defect_history','defect_id':observation['defect_id'],'scope':{'document_ids':[id]}})
        assert e.value.code=='RESULT_LIMIT_EXCEEDED'
    finally:cleanup(graph,id)
