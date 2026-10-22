"""Actual graph state transition during fake generation; fixture only, no model call."""
import pytest
from test_neo4j import graph,bundle,register
from test_graph_queries import cleanup
from ontology_proj.adapters.graph_queries import Neo4jQueries
from ontology_proj.application.query import LookupGraph
from ontology_proj.application.answer import AnswerQuestion
from ontology_proj.application.files import FileStoreError
from ontology_proj.adapters.answer_journal import LocalAnswerJournal


def test_actual_graph_revocation_during_generation(graph,bundle,tmp_path):
    doc,records=bundle;id=doc['document_id'];obs=records['observations'][0];rid=obs['observation_id']
    op=register(graph,doc)
    try:
        graph.commit_bundle(op,doc,records,[])
        queries=Neo4jQueries(graph);lookup=LookupGraph(queries)
        class Evidence:
            def read(self,eid):
                raw=queries.evidence(eid)
                return {'evidence':raw['evidence'],'records':raw['records'],'document':raw['document']}
        class Context:
            def execute(self,r):
                facts=lookup.execute(dict(query_type='record_by_id',record_id=rid,scope=r['scope']))['records']
                return dict(status='degraded',facts=facts,business_passages=[],source_statements=[],warnings=['fixture embedding failure'])
        class Generator:
            def request(self,*args):return {}
            def generate(self,r):
                value=next(e['evidence_id'] for e in records['evidence'] if e['evidence_id'] in obs['evidence_ids'] and e['target_field']=='area_original_value')
                unit=next(e['evidence_id'] for e in records['evidence'] if e['evidence_id'] in obs['evidence_ids'] and e['target_field']=='area_original_unit')
                with graph._session() as session:
                    session.run('MATCH (n:Observation {observation_id:$id}) SET n.acceptance_status="rejected"',id=rid).consume()
                return {'candidate':{'status':'answered','reason_codes':[],'claims':[{'kind':'fact','record_ids':[rid],'evidence_ids':[value,unit],'assertions':[{'record_id':rid,'field':'area_value','value':'0.04','unit':'m²'}]}]},'diagnostics':[]}
        answer=AnswerQuestion(Context(),lookup,Evidence(),Generator(),LocalAnswerJournal(tmp_path))
        with pytest.raises(FileStoreError) as exc:answer.execute(dict(question='面积是多少？',scope={'document_ids':[id]}))
        assert exc.value.code=='ANSWER_OUTPUT_INVALID' and exc.value.question_id
        assert list((tmp_path/'answers').glob('*/validation.json')) and not list((tmp_path/'answers').glob('*/answer.json'))
    finally:cleanup(graph,id)
