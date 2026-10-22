"""Fixed, parameterized, read-only graph queries."""
from ontology_proj.adapters.neo4j_repository import guarded
from ontology_proj.domain.graph_mapping import LABELS
from ontology_proj.application.files import FileStoreError


class Neo4jQueries:
    def __init__(self, graph):self.graph=graph

    @guarded
    def lookup(self, request):
        s=request['scope'];q=request['query_type']
        kinds=['components'] if q=='bridge_components' else ['observations'] if q in ('component_observations','defect_history') else list(LABELS)
        result=[]
        with self.graph._session() as session:
            for kind in kinds:
                label,identity,_=LABELS[kind]
                # Fixed ancestry relationships provide bridge and component scope.
                ancestry={
                    'Bridge':'WITH n, n AS bridge, null AS component',
                    'Component':'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(n) WITH n, bridge, n AS component',
                    'Defect':'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(component:Component)-[:HAS_DEFECT]->(n) WITH n, bridge, component',
                    'Observation':'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(component:Component)-[:HAS_DEFECT]->(defect:Defect)-[:HAS_OBSERVATION]->(n) WITH n, bridge, component',
                }[label]
                condition=''
                if q=='record_by_id':condition=f' AND n.{identity}=$record'
                if q=='defect_history':condition=' AND n.defect_id=$defect'
                rows=session.run(f'''MATCH (n:{label}) {ancestry}
WHERE ($bridge IS NULL OR bridge.bridge_id=$bridge)
 AND (size($components)=0 OR component.component_id IN $components)
MATCH (n)-[r:SUPPORTED_BY]->(e:Evidence)-[:FROM_BLOCK]->(:TextBlock)-[:IN_DOCUMENT]->(d:SourceDocument)
WHERE (size($documents)=0 OR d.document_id IN $documents)
 AND ($synthetic OR d.source_kind="real")
 AND (n.acceptance_status=$mode OR ($mode="pending_review" AND n.acceptance_status="accepted")) AND r.acceptance_status=$mode
 AND ($review="accepted" OR n.review_status="confirmed") {condition}
WITH n, collect(DISTINCT e.evidence_id) AS evidence_ids,
 collect(DISTINCT d.document_id) AS documents, collect(DISTINCT d.source_kind) AS sources
RETURN properties(n) AS value, evidence_ids, documents, sources LIMIT 501''',bridge=s['bridge_id'],components=s['component_ids'],documents=s['document_ids'],synthetic=s['include_synthetic'],review=s['review_mode'],mode=request['acceptance_mode'],record=request.get('record_id'),defect=request.get('defect_id')).data()
                for row in rows:
                    result.append({**row['value'],'record_type':label,'record_id':row['value'][identity], 'source_acceptance_status':request['acceptance_mode'], 'evidence_ids':sorted(row['evidence_ids']),'source_document_ids':sorted(row['documents']),'source_kinds':sorted(row['sources'])})
                if len(result)>500:raise FileStoreError('RESULT_LIMIT_EXCEEDED','Please narrow the query scope')
        return result

    @guarded
    def evidence(self, evidence_id):
        with self.graph._session() as session:
            row=session.run('''MATCH (e:Evidence {evidence_id:$id})-[:FROM_BLOCK]->(b:TextBlock)-[:IN_DOCUMENT]->(d:SourceDocument)
RETURN properties(e) AS evidence, b.text AS block_text, properties(d) AS document''',id=evidence_id).single()
            if row is None:raise FileStoreError('OBJECT_NOT_FOUND','Evidence does not exist')
            result=dict(row);records=[]
            for label,identity,_ in LABELS.values():
                rows=session.run(f'''MATCH (n:{label})-[r:SUPPORTED_BY]->(e:Evidence {{evidence_id:$id}})
RETURN n.{identity} AS record_id, n.acceptance_status AS acceptance_status,
 r.acceptance_status AS source_acceptance_status, n.review_status AS review_status LIMIT 501''',id=evidence_id).data()
                records.extend(dict(r,record_type=label) for r in rows)
                if len(records)>500:raise FileStoreError('RESULT_LIMIT_EXCEEDED','Evidence references exceed limit')
            result['records']=records
            return result
