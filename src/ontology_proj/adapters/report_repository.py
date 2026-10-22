"""Report-only graph registration; business records are never created or replaced."""
import json
from ontology_proj.adapters.neo4j_repository import guarded, now
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.report import FIELDS
from ontology_proj.domain.graph_mapping import decode_measurement

class Neo4jReports:
    def __init__(self,graph,actor):self.graph,self.actor=graph,actor

    @guarded
    def create(self,rid,op,request):
        with self.graph._session() as session:
            session.execute_write(lambda tx:tx.run('CREATE (o:Operation {operation_id:$op,report_id:$id,kind:"report",status:"queued",stage:"report_queued",created_at:$time,updated_at:$time,actor:$actor,request_json:$request})',op=op,id=rid,time=now(),actor=self.actor,request=json.dumps(request,ensure_ascii=False)).consume())

    @guarded
    def get(self,rid):
        with self.graph._session() as session:
            row=session.run('MATCH (o:Operation {report_id:$id,kind:"report"}) OPTIONAL MATCH (r:Report {report_id:$id}) RETURN properties(o) AS op,properties(r) AS report',id=rid).single()
            if row is None:raise FileStoreError('OBJECT_NOT_FOUND','Report operation does not exist')
            result=dict(row['op']);result['request']=json.loads(result.pop('request_json'))
            result['registration']=row['report']
            return result

    @guarded
    def advance(self,rid):
        with self.graph._session() as session:
            row=session.execute_write(lambda tx:tx.run('MATCH (o:Operation {report_id:$id,kind:"report"}) WHERE o.status <> "succeeded" SET o.status="running",o.stage="report_generating",o.updated_at=$time REMOVE o.error_code RETURN o.operation_id AS id',id=rid,time=now()).single())
            if row is None:raise FileStoreError('GRAPH_OPERATION_INACTIVE','Report is not active')

    @guarded
    def fail(self,rid,code):
        def write(tx):
            row=tx.run('MATCH (o:Operation {report_id:$id,kind:"report"}) WHERE o.status <> "succeeded" SET o.status="failed",o.stage="report_failed",o.error_code=$code,o.updated_at=$time RETURN o.operation_id AS op',id=rid,code=code,time=now()).single()
            if row:self.graph._audit(tx,'report-failure:'+row['op'],self.actor,'GenerateReport',rid,'failed','running','failed',code)
        with self.graph._session() as session:session.execute_write(write)

    @guarded
    def recover(self):
        with self.graph._session() as session:
            session.execute_write(lambda tx:tx.run('MATCH (o:Operation {kind:"report"}) WHERE o.status IN ["queued","running"] SET o.status="interrupted",o.stage="report_interrupted",o.error_code="PROCESS_INTERRUPTED",o.updated_at=$time',time=now()).consume())

    @guarded
    def documents(self,scope):
        with self.graph._session() as session:
            rows=session.run('''MATCH (d:SourceDocument) WHERE d.processing_status IN ["graph_ready","graph_ready_with_warnings"]
AND d.bridge_id=$bridge AND (size($docs)=0 OR d.document_id IN $docs) AND ($synthetic OR d.source_kind="real")
AND (size($components)=0 OR EXISTS { MATCH (c:Component)-[s:SUPPORTED_BY]->(:Evidence)-[:FROM_BLOCK]->(:TextBlock)-[:IN_DOCUMENT]->(d) WHERE c.component_id IN $components AND c.bridge_id=$bridge AND c.acceptance_status="accepted" AND s.acceptance_status="accepted" })
RETURN properties(d) AS value ORDER BY d.document_id LIMIT 101''',bridge=scope['bridge_id'],docs=scope['document_ids'],synthetic=scope['include_synthetic'],components=scope['component_ids']).data()
            if len(rows)>100:raise FileStoreError('RESULT_LIMIT_EXCEEDED','Narrow report sources')
            return [r['value'] for r in rows]

    @guarded
    def statements(self,scope,document_ids):
        with self.graph._session() as session:
            rows=session.run('''MATCH (c:DocumentChunk)-[:IN_DOCUMENT]->(d:SourceDocument)
WHERE d.document_id IN $docs AND c.content_kind="source_statement"
AND (size($components)=0 OR any(id IN c.component_ids WHERE id IN $components))
MATCH (c)-[:CITES]->(e:Evidence)
RETURN DISTINCT e.evidence_id AS evidence_id,c.semantic_kind AS kind ORDER BY evidence_id LIMIT 501''',docs=document_ids,components=scope['component_ids']).data()
            if len(rows)>500:raise FileStoreError('RESULT_LIMIT_EXCEEDED','Narrow report statements')
            return rows

    @guarded
    def commit(self,report,snapshots,manifest):
        rid=report['report_id']
        def reject(message):raise FileStoreError('GENERATION_VALIDATION_FAILED',message)
        def write(tx):
            operation=tx.run('MATCH (o:Operation {report_id:$id,kind:"report"}) SET o._report_lock=true REMOVE o._report_lock RETURN properties(o) AS value',id=rid).single()
            if operation is None:reject('Report operation missing')
            op=operation['value']
            if op['status']=='succeeded':return
            if op['status']!='running':reject('Report operation is not running')
            for snapshot in snapshots:
                row=tx.run('MATCH (n:Observation {observation_id:$id}) SET n._report_lock=true REMOVE n._report_lock RETURN properties(n) AS value',id=snapshot['record_id']).single()
                if row is None:reject('Referenced observation missing')
                current=decode_measurement(row['value'])
                for field in (*FIELDS,'component_id','defect_id','acceptance_status'):
                    if current.get(field)!=snapshot.get(field):reject('Observation changed during report registration')
                ancestry=tx.run('MATCH (b:Bridge {bridge_id:$bridge})-[:HAS_COMPONENT]->(c:Component {component_id:$cmp})-[:HAS_DEFECT]->(d:Defect {defect_id:$defect})-[:HAS_OBSERVATION]->(o:Observation {observation_id:$obs}) RETURN o.observation_id AS id',bridge=report['scope']['bridge_id'],cmp=snapshot['component_id'],defect=snapshot['defect_id'],obs=snapshot['record_id']).single()
                if ancestry is None:reject('Observation ancestry changed during registration')
                citations=tx.run('''MATCH (n:Observation {observation_id:$id})-[s:SUPPORTED_BY]->(e:Evidence)-[:FROM_BLOCK]->(:TextBlock)-[:IN_DOCUMENT]->(d:SourceDocument)
WHERE s.acceptance_status="accepted" AND d.document_id IN $docs RETURN collect(DISTINCT e.evidence_id) AS ids''',id=snapshot['record_id'],docs=report['source_document_ids']).single()['ids']
                if set(citations)!=set(snapshot['evidence_ids']):reject('Record citations changed during registration')
            for doc in report['source_document_ids']:
                row=tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d._report_lock=true REMOVE d._report_lock RETURN properties(d) AS value',id=doc).single()
                if row is None or row['value']['processing_status'] not in ('graph_ready','graph_ready_with_warnings') or row['value'].get('bridge_id')!=report['scope']['bridge_id']:reject('Source is no longer published in scope')
            for e in report['sections'][4]['rows']:
                raw=tx.run('MATCH (e:Evidence {evidence_id:$id})-[:FROM_BLOCK]->(b:TextBlock)-[:IN_DOCUMENT]->(d:SourceDocument) RETURN properties(e) AS e,b.text AS text,d.document_id AS doc',id=e['evidence_id']).single()
                if raw is None:reject('Citation disappeared')
                value=raw['e'];span=value.get('charspan',[])
                if len(span)!=2 or raw['text'][slice(*span)]!=e['quote'] or any(value.get(k)!=e[k] for k in ('quote','document_id','page_no','target_field')) or raw['doc']!=e['document_id']:reject('Citation changed during registration')
            for item in report['sections'][3]['rows']:
                row=tx.run('MATCH (c:DocumentChunk)-[:CITES]->(e:Evidence {evidence_id:$id}) WHERE c.content_kind="source_statement" AND c.semantic_kind=$kind AND c.document_id IN $docs AND (size($components)=0 OR any(id IN c.component_ids WHERE id IN $components)) RETURN c.chunk_id AS id LIMIT 1',id=item['evidence_id'],kind=item['kind'],docs=report['source_document_ids'],components=report['scope']['component_ids']).single()
                if row is None:reject('Source statement no longer in context')
            props={k:report[k] for k in ('report_id','template_id','template_version','generated_at','source_document_ids','professional_review')}
            props.update(scope_json=json.dumps(report['scope']),artifact_keys=[f'reports/{rid}/report.json',f'reports/{rid}/report.md'],manifest_json=json.dumps(manifest))
            tx.run('CREATE (r:Report) SET r += $props',props=props).consume()
            for e in report['sections'][4]['rows']:tx.run('MATCH (r:Report {report_id:$rid}),(e:Evidence {evidence_id:$eid}) CREATE (r)-[:CITES]->(e)',rid=rid,eid=e['evidence_id']).consume()
            tx.run('MATCH (o:Operation {report_id:$id,kind:"report"}) SET o.status="succeeded",o.stage="report_registered",o.updated_at=$time,o.artifact_keys=$keys REMOVE o.error_code',id=rid,time=now(),keys=props['artifact_keys']).consume()
            self.graph._audit(tx,'report-success:'+op['operation_id'],self.actor,'GenerateReport',rid,'succeeded','running','succeeded')
        with self.graph._session() as session:session.execute_write(write)
