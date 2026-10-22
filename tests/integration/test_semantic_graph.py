import copy
import pytest
from test_neo4j import graph, bundle, register
from test_graph_queries import cleanup
from ontology_proj.settings import Settings
from ontology_proj.bootstrap import build_semantic
from ontology_proj.adapters.semantic_repository import Neo4jSemantic
from ontology_proj.application.semantic import build_chunks, visible, search_request
from ontology_proj.application.files import FileStoreError


def test_transaction_retry_fence_and_current_source_state(graph,bundle):
    settings=Settings(_env_file='data/s23-runtime/.env.s32')
    _,search,_,_=build_semantic(settings,graph,None)
    repo=Neo4jSemantic(graph,1024,'s23_test_fixture');profile=search.profile
    doc,records=bundle;id=doc['document_id'];op=register(graph,doc)
    try:
        graph.commit_bundle(op,doc,records,[])
        chunks,_=build_chunks(doc,records,profile)
        for c in chunks:c['embedding']=[1.0]+[0.0]*1023
        operation=repo.begin(id,profile,False)
        with pytest.raises(FileStoreError) as e:repo.begin(id,profile,False)
        assert e.value.code=='EXPLICIT_RETRY_REQUIRED'
        bad=copy.deepcopy(chunks);bad[-1]['source_spans'][0]['quote']='bad source'
        with pytest.raises(FileStoreError):repo.publish(id,operation['run_id'],profile,bad,[])
        with graph._session() as session:
            assert session.run('MATCH (c:DocumentChunk {document_id:$id}) RETURN count(c) AS n',id=id).single()['n']==0
            assert session.run('MATCH (d:SourceDocument {document_id:$id}) RETURN d.processing_status AS s',id=id).single()['s'].startswith('graph_ready')
        repo.fail(id,operation['run_id'],'FIXTURE_FAILURE')
        next_run=repo.begin(id,profile,True)
        with pytest.raises(FileStoreError) as e:repo.publish(id,operation['run_id'],profile,chunks,[])
        assert e.value.code=='INDEX_OPERATION_SUPERSEDED'
        repo.publish(id,next_run['run_id'],profile,chunks,[])
        assert repo.begin(id,profile,False)['reused']
        scope=search_request(dict(query='q',scope={'document_ids':[id]}))[1]
        rows=[r for r in repo.search([1.0]+[0.0]*1023,profile,200) if r['document_id']==id]
        obs=records['observations'][0]['observation_id']
        assert any(obs in r['record_ids'] and visible(r,scope,{id}) for r in rows)
        with graph._session() as session:
            session.run('MATCH (n:Observation {observation_id:$id})-[r:SUPPORTED_BY]->(:Evidence) SET r.acceptance_status="pending_review"',id=obs).consume()
        rows=[r for r in repo.search([1.0]+[0.0]*1023,profile,200) if obs in r['record_ids']]
        assert rows and not any(visible(r,scope,{id}) for r in rows)
        with graph._session() as session:
            session.run('MATCH (n:Observation {observation_id:$id})-[r:SUPPORTED_BY]->(:Evidence) SET r.acceptance_status="accepted", n.review_status="confirmed",n.observed_at="2026-02-01"',id=obs).consume()
        confirmed=scope|{'review_mode':'confirmed_only','date_from':'2026-02-01','date_to':'2026-02-01'}
        rows=[r for r in repo.search([1.0]+[0.0]*1023,profile,200) if obs in r['record_ids']]
        assert any(visible(r,confirmed,{id}) for r in rows)
        with pytest.raises(FileStoreError) as e:repo.initialize('wrong-profile')
        assert e.value.code=='INDEX_CONFIG_CONFLICT'
    finally:cleanup(graph,id)
