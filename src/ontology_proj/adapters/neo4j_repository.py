"""Fixed Neo4j queries, bounded transactions, persistent registration and audit."""
from datetime import datetime, timezone
from functools import wraps
import json
from uuid import uuid4

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, DriverError

from ontology_proj.application.dto import CapabilityState, CapabilityStatus
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.graph_mapping import LABELS, project_bundle, decode_measurement


CONSTRAINTS = {
    'source_document_id': ('SourceDocument', 'document_id'), 'source_sha256': ('SourceDocument', 'sha256'),
    'text_block_id': ('TextBlock', 'block_id'), 'evidence_id': ('Evidence', 'evidence_id'),
    'bridge_id': ('Bridge', 'bridge_id'), 'component_id': ('Component', 'component_id'),
    'defect_id': ('Defect', 'defect_id'), 'observation_id': ('Observation', 'observation_id'),
    'operation_id': ('Operation', 'operation_id'), 'audit_id': ('AuditEvent', 'audit_id'),
    'write_request_key': ('WriteRequest', 'idempotency_key'),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def guarded(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Neo4jError:
            code='GRAPH_WRITE_FAILED' if method.__name__=='commit_bundle' else 'GRAPH_UNAVAILABLE'
            raise FileStoreError(code, 'Graph transaction failed', retryable=True) from None
        except (DriverError, OSError):
            raise FileStoreError('GRAPH_UNAVAILABLE', 'Graph service operation failed', retryable=True) from None
    return call


class Neo4jRepository:
    def __init__(self, uri, user, password, database='neo4j'):
        self.configured = bool(uri and user and password)
        self.database = database
        self.driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=3,
                                          connection_acquisition_timeout=5, max_transaction_retry_time=0) if self.configured else None

    def _session(self):
        if self.driver is None:
            raise FileStoreError('GRAPH_UNAVAILABLE', 'Graph service is not configured', retryable=True)
        return self.driver.session(database=self.database)

    def close(self):
        if self.driver:
            self.driver.close()

    @guarded
    def initialize(self):
        with self._session() as session:
            for name, (label, field) in CONSTRAINTS.items():
                session.run(f'CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{field} IS UNIQUE').consume()
            # Compatibility for the early S2.3 source/count fields, before
            # the public SourceDocument contract was completed. Idempotent.
            session.run('MATCH (d:SourceDocument) WHERE d.bytes_count IS NOT NULL SET d.bytes=d.bytes_count REMOVE d.bytes_count').consume()
            session.run('MATCH (d:SourceDocument) WHERE d.processing_status IN ["graph_ready", "graph_ready_with_warnings"] AND d.candidate_count IS NULL OPTIONAL MATCH (n)-[r:SUPPORTED_BY]->(e:Evidence {document_id:d.document_id}) WITH d, collect(DISTINCT CASE WHEN n IS NOT NULL THEN {id:elementId(n), state:r.acceptance_status} ELSE null END) AS records OPTIONAL MATCH (b:TextBlock {document_id:d.document_id}) WITH d, records, count(DISTINCT b) AS blocks SET d.candidate_count=size(records), d.accepted_count=size([x IN records WHERE x.state="accepted"]), d.pending_count=size([x IN records WHERE x.state="pending_review"]), d.rejected_count=0, d.located_block_count=blocks, d.parsed_key="documents/"+d.document_id+"/docling.json", d.normalized_key="documents/"+d.document_id+"/normalized.json"').consume()

    def check(self):
        if not self.configured:
            return CapabilityStatus(CapabilityState.NOT_CONFIGURED, False)
        try:
            with self._session() as session:
                session.run('RETURN 1 AS ok').single(strict=True)
                rows = session.run('SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN name, type, labelsOrTypes, properties').data()
                actual = {r['name']: (r['type'], r['labelsOrTypes'], r['properties']) for r in rows}
                if any(actual.get(name) != ('UNIQUENESS', [label], [field]) for name, (label, field) in CONSTRAINTS.items()):
                    return CapabilityStatus(CapabilityState.UNAVAILABLE, True, 'GRAPH_SCHEMA_NOT_READY')
            return CapabilityStatus(CapabilityState.READY, True)
        except (Neo4jError, DriverError, OSError, FileStoreError):
            return CapabilityStatus(CapabilityState.UNAVAILABLE, True, 'GRAPH_UNAVAILABLE')

    @staticmethod
    def _request(tx, request):
        row = tx.run('MATCH (w:WriteRequest {idempotency_key:$key}) RETURN properties(w) AS value', key=request['key']).single()
        if row is None:
            return None
        if row['value']['payload_hash'] != request['payload_hash']:
            raise FileStoreError('IDEMPOTENCY_CONFLICT', 'Idempotency key belongs to a different request')
        return json.loads(row['value']['result_json'])

    @staticmethod
    def _record_request(tx, request, result):
        tx.run('CREATE (w:WriteRequest {idempotency_key:$key, payload_hash:$payload_hash, actor:$actor, use_case:$use_case, result_json:$result, created_at:$created_at})',
               **request, result=json.dumps(result, ensure_ascii=False), created_at=now()).consume()

    @guarded
    def replay(self, request):
        with self._session() as session:
            return session.execute_read(self._request, request)

    @staticmethod
    def _audit(tx, audit_id, actor, tool, object_id, result, old_status=None, new_status=None, code=None):
        tx.run('MERGE (a:AuditEvent {audit_id:$id}) ON CREATE SET a += $props', id=audit_id,
               props=dict(actor=actor, tool=tool, object_id=object_id, result=result, created_at=now(), old_status=old_status, new_status=new_status, error_code=code)).consume()

    @guarded
    def register(self, source, request):
        audit_id = str(uuid4())
        def write(tx):
            cached = self._request(tx, request)
            if cached is not None:
                return cached
            existing = tx.run('MATCH (d:SourceDocument {document_id:$id}) RETURN properties(d) AS value', id=source['document_id']).single()
            duplicate = existing is not None
            if existing:
                for k in ('sha256', 'source_kind', 'kind', 'bridge_id'):
                    previous=existing['value'].get('upload_bridge_id') if k=='bridge_id' and existing['value'].get('bridge_resolution_version') else existing['value'].get(k)
                    if previous != source.get(k):
                        raise FileStoreError('SOURCE_CONTEXT_CONFLICT', 'Registered source context differs')
                state = existing['value']['processing_status']
            else:
                tx.run('CREATE (d:SourceDocument) SET d += $props', props={**source, 'processing_status': 'uploaded', 'index_status': 'not_started', 'accepted_count': 0, 'pending_count': 0, 'warnings': []}).consume()
                state = 'uploaded'
            result = dict(document_id=source['document_id'], duplicate=duplicate, processing_status=state)
            self._record_request(tx, request, result)
            self._audit(tx, audit_id, request['actor'], 'RegisterDocument', source['document_id'], 'succeeded', new_status=state)
            return result
        with self._session() as session:
            return session.execute_write(write)

    @guarded
    def existing_bridges(self):
        with self._session() as session:
            rows=session.run('MATCH (b:Bridge) RETURN properties(b) AS bridge LIMIT 501').data()
            if len(rows)>500:raise FileStoreError('BRIDGE_CATALOG_IMPORT_LIMIT','Too many existing bridges for automatic local import')
            return [r['bridge'] for r in rows]

    @guarded
    def document(self, document_id):
        with self._session() as session:
            row = session.run('MATCH (d:SourceDocument {document_id:$id}) RETURN properties(d) AS value', id=document_id).single()
            if row is None:
                raise FileStoreError('OBJECT_NOT_FOUND', 'Registered document does not exist')
            return row['value']

    @guarded
    def operation(self, operation_id):
        with self._session() as session:
            row = session.run('MATCH (o:Operation {operation_id:$id}) RETURN properties(o) AS value', id=operation_id).single()
            if row is None:
                raise FileStoreError('OBJECT_NOT_FOUND', 'Operation does not exist')
            value = row['value']
            value.pop('configuration', None)
            return value

    @guarded
    def lookup_document(self, document_id, *, accepted_only=True):
        self.document(document_id)
        result = {}
        with self._session() as session:
            for plural, (label, identity, _) in LABELS.items():
                rows = session.run(f'MATCH (n:{label})-[r:SUPPORTED_BY]->(e:Evidence {{document_id:$id}}) WHERE NOT $accepted OR (n.acceptance_status="accepted" AND r.acceptance_status="accepted") RETURN DISTINCT properties(n) AS value LIMIT 501', id=document_id, accepted=accepted_only).data()
                if len(rows) > 500:
                    raise FileStoreError('RESULT_LIMIT_EXCEEDED', 'Document facts exceed read limit')
                result[plural] = [decode_measurement(r['value']) for r in rows]
            result['evidence'] = session.run('MATCH (e:Evidence {document_id:$id})-[:FROM_BLOCK]->(b:TextBlock)-[:IN_DOCUMENT]->(d:SourceDocument) RETURN properties(e) AS evidence, b.text AS text, d.document_id AS document_id', id=document_id).data()
        return result

    @guarded
    def request_processing(self, document_id, request, configuration, retry=False):
        operation_id, audit_id = str(uuid4()), str(uuid4())
        def write(tx):
            cached = self._request(tx, request)
            if cached is not None:
                return {**cached, 'enqueue': False}
            row = tx.run('MATCH (d:SourceDocument {document_id:$id}) RETURN properties(d) AS value', id=document_id).single()
            if row is None:
                raise FileStoreError('OBJECT_NOT_FOUND', 'Registered document does not exist')
            d = row['value']
            previous = None
            if d.get('operation_id'):
                previous = tx.run('MATCH (o:Operation {operation_id:$id}) RETURN properties(o) AS value', id=d['operation_id']).single()['value']
            if d['processing_status'] in ('graph_ready', 'graph_ready_with_warnings') or previous and previous['status'] in ('queued', 'running'):
                result = dict(document_id=document_id, operation_id=d['operation_id'], reused=True)
                self._record_request(tx, request, result)
                return {**result, 'enqueue': False}
            if previous:
                if not retry:
                    raise FileStoreError('EXPLICIT_RETRY_REQUIRED', 'Failed or interrupted processing requires explicit retry')
                if previous['configuration'] != configuration:
                    raise FileStoreError('PROCESS_CONFIG_CONFLICT', 'Retry configuration differs from the original operation')
            elif retry:
                raise FileStoreError('STAGE_NOT_RETRYABLE', 'No failed operation to retry')
            props = dict(operation_id=operation_id, document_id=document_id, status='queued', stage='queued', actor=request['actor'], created_at=now(), updated_at=now(), configuration=configuration, artifact_keys=[], retry_of=previous['operation_id'] if previous else None)
            tx.run('CREATE (o:Operation) SET o += $props', props=props).consume()
            tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.processing_status="queued", d.operation_id=$op REMOVE d.error_code', id=document_id, op=operation_id).consume()
            result = dict(document_id=document_id, operation_id=operation_id, reused=False)
            self._record_request(tx, request, result)
            self._audit(tx, audit_id, request['actor'], 'RequestProcessing', document_id, 'queued', d['processing_status'], 'queued')
            return {**result, 'enqueue': True}
        with self._session() as session:
            return session.execute_write(write)

    @guarded
    def advance(self, operation_id, stage, document_status):
        with self._session() as session:
            session.execute_write(lambda tx: tx.run('MATCH (o:Operation {operation_id:$op}), (d:SourceDocument {document_id:o.document_id}) WHERE o.status IN ["queued", "running"] SET o.status="running", o.stage=$stage, o.updated_at=$time, d.processing_status=$state, d.updated_at=$time', op=operation_id, stage=stage, state=document_status, time=now()).consume())

    @guarded
    def commit_bundle(self, operation_id, document, records, artifacts):
        try:
            projection = project_bundle(document, records)
        except ValueError:
            raise FileStoreError('GRAPH_RECORD_INVALID', 'Records cannot be projected safely') from None
        audit_id = 'success:' + operation_id
        with self._session() as session:
            return session.execute_write(self._commit, operation_id, document, records, projection, artifacts, audit_id)

    def _commit(self, tx, operation_id, document, records, projection, artifacts, audit_id):
        row = tx.run('MATCH (o:Operation {operation_id:$op}), (d:SourceDocument {document_id:o.document_id}) RETURN properties(o) AS op, properties(d) AS doc', op=operation_id).single()
        if row is None or row['doc']['document_id'] != document['document_id'] or row['doc']['source_kind'] != document['source_kind']:
            raise FileStoreError('GRAPH_RECORD_INVALID', 'Graph operation and record source differ')
        if row['doc']['processing_status'] in ('graph_ready', 'graph_ready_with_warnings'):
            return row['doc']
        if row['op']['status'] != 'running' or row['doc']['operation_id'] != operation_id:
            raise FileStoreError('GRAPH_OPERATION_INACTIVE', 'Graph operation is no longer active')
        blocked = set()
        conflicts = []
        for plural, (label, identity, fields) in LABELS.items():
            for node in projection['nodes'][label]:
                props = node['properties']
                existing = tx.run(f'MATCH (n:{label} {{{identity}:$id}}) RETURN properties(n) AS value', id=node['id']).single()
                parent = props.get('bridge_id') if label == 'Component' else props.get('component_id') if label == 'Defect' else props.get('defect_id') if label == 'Observation' else None
                compare_fields=('name','location','identity_version') if label=='Bridge' and records.get('identity_mode')=='catalog' else fields
                if label=='Bridge' and records.get('identity_mode')=='catalog' and document['bridge_binding']['bridge'].get('origin')=='existing_publication' and existing and existing['value'].get('identity_version') is None:
                    compare_fields=('name',)
                    if existing['value'].get('bridge_code')!=document['bridge_binding']['bridge'].get('legacy_code'):
                        raise FileStoreError('GRAPH_RECORD_INVALID','Legacy bridge code differs from verified import')
                conflict = existing is not None and any(existing['value'].get(k) != props.get(k) for k in compare_fields)
                if conflict or parent in blocked:
                    blocked.add(node['id'])
                    conflicts.append(node['id'])
                    props = {**props, 'acceptance_status': 'pending_review', 'conflict_code': 'OBJECT_CONFLICT'}
                if existing is None:
                    tx.run(f'CREATE (n:{label}) SET n += $props', props=props).consume()
                # Existing objects are immutable; new evidence remains document scoped.
        tx.run('UNWIND $blocks AS props MATCH (d:SourceDocument {document_id:props.document_id}) MERGE (b:TextBlock {block_id:props.block_id}) ON CREATE SET b += props MERGE (b)-[:IN_DOCUMENT]->(d)', blocks=projection['blocks']).consume()
        tx.run('UNWIND $evidence AS props MATCH (b:TextBlock {block_id:props.block_id}) MERGE (e:Evidence {evidence_id:props.evidence_id}) ON CREATE SET e += props MERGE (e)-[:FROM_BLOCK]->(b)', evidence=projection['evidence']).consume()
        for plural, (label, identity, _) in LABELS.items():
            tx.run(f'UNWIND $nodes AS item MATCH (n:{label} {{{identity}:item.id}}) UNWIND item.evidence_ids AS eid MATCH (e:Evidence {{evidence_id:eid}}) MERGE (n)-[r:SUPPORTED_BY]->(e) SET r.acceptance_status=CASE WHEN item.id IN $blocked THEN "pending_review" ELSE item.properties.acceptance_status END', nodes=projection['nodes'][label], blocked=list(blocked)).consume()
        for parent, child, rel, parent_id, child_id, fk in (
            ('Bridge', 'Component', 'HAS_COMPONENT', 'bridge_id', 'component_id', 'bridge_id'),
            ('Component', 'Defect', 'HAS_DEFECT', 'component_id', 'defect_id', 'component_id'),
            ('Defect', 'Observation', 'HAS_OBSERVATION', 'defect_id', 'observation_id', 'defect_id')):
            items = projection['nodes'][child]
            tx.run(f'UNWIND $items AS item MATCH (p:{parent} {{{parent_id}:item.properties.{fk}}}), (c:{child} {{{child_id}:item.id}}) MERGE (p)-[:{rel}]->(c)', items=items).consume()
        warnings = sorted(set(['OBJECT_CONFLICT'] if conflicts else []) | {i['code'] for i in records.get('issues', [])} | {w['code'] for w in document.get('warnings', [])})
        observations = projection['nodes']['Observation']
        all_nodes=[n for items in projection['nodes'].values() for n in items]
        accepted = sum(n['id'] not in blocked and n['properties']['acceptance_status'] == 'accepted' for n in all_nodes)
        pending = len(all_nodes)-accepted
        status = 'graph_ready_with_warnings' if warnings or pending else 'graph_ready'
        props = dict(processing_status=status, candidate_count=len(all_nodes), accepted_count=accepted, pending_count=pending, rejected_count=0, observation_count=len(observations), located_block_count=sum(b.get('page_no') is not None for b in projection['blocks']), warnings=warnings, conflict_ids=conflicts, artifact_keys=artifacts, parsed_key=f"documents/{document['document_id']}/docling.json", normalized_key=f"documents/{document['document_id']}/normalized.json", updated_at=now(), graph_committed_at=now())
        if records.get('identity_mode')=='catalog':
            binding=document['bridge_binding']
            props.update(bridge_id=binding['bridge_id'],bridge_resolution_version=binding['version'],bridge_resolution_reason=binding['reason'],bridge_resolution_source_sha256=binding['source_sha256'],source_bridge_code=binding['facts']['values']['official_code'])
            tx.run('MATCH (d:SourceDocument {document_id:$doc}), (b:Bridge {bridge_id:$bridge}) MERGE (d)-[:ABOUT_BRIDGE]->(b)',doc=document['document_id'],bridge=binding['bridge_id']).consume()
        tx.run('MATCH (d:SourceDocument {document_id:$doc}), (o:Operation {operation_id:$op}) SET d += $props, o.status=$status, o.stage="graph_committed", o.updated_at=$time, o.artifact_keys=$artifacts REMOVE d.error_code, o.error_code', doc=document['document_id'], op=operation_id, props=props, status='succeeded_with_warnings' if status.endswith('warnings') else 'succeeded', time=now(), artifacts=artifacts).consume()
        self._audit(tx, audit_id, row['op']['actor'], 'ProcessDocumentPipeline', document['document_id'], 'succeeded', row['doc']['processing_status'], status)
        return props

    @guarded
    def fail(self, operation_id, code, artifacts, counts=None):
        def write(tx):
            row = tx.run('MATCH (o:Operation {operation_id:$op}), (d:SourceDocument {document_id:o.document_id}) RETURN properties(o) AS op, d.processing_status AS state', op=operation_id).single()
            if row is None or row['state'] in ('graph_ready', 'graph_ready_with_warnings'):
                return
            tx.run('MATCH (o:Operation {operation_id:$op}), (d:SourceDocument {document_id:o.document_id}) SET o.status="failed", o.error_code=$code, o.updated_at=$time, o.artifact_keys=$artifacts, d.processing_status="failed", d.error_code=$code', op=operation_id, code=code, time=now(), artifacts=artifacts).consume()
            if counts:
                safe={k:v for k,v in counts.items() if k in {'candidate_count','accepted_count','pending_count','rejected_count'} and type(v) is int and v>=0}
                tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d += $props',id=row['op']['document_id'],props=safe).consume()
            self._audit(tx, 'failure:'+operation_id, row['op']['actor'], 'ProcessDocumentPipeline', row['op']['document_id'], 'failed', row['state'], 'failed', code)
        with self._session() as session:
            session.execute_write(write)

    @guarded
    def recover(self):
        def write(tx):
            rows = tx.run('MATCH (o:Operation) WHERE o.document_id IS NOT NULL AND o.status IN ["queued", "running"] RETURN properties(o) AS value').data()
            recovered = []
            for row in rows:
                o = row['value']
                state = tx.run('MATCH (d:SourceDocument {document_id:$id}) RETURN d.processing_status AS state', id=o['document_id']).single()['state']
                committed = state in ('graph_ready', 'graph_ready_with_warnings')
                tx.run('MATCH (o:Operation {operation_id:$op}) SET o.status=$status, o.error_code=$code, o.updated_at=$time', op=o['operation_id'], status='succeeded_with_warnings' if state.endswith('warnings') else 'succeeded' if committed else 'interrupted', code=None if committed else 'PROCESS_INTERRUPTED', time=now()).consume()
                if not committed:
                    tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.processing_status="interrupted", d.error_code="PROCESS_INTERRUPTED"', id=o['document_id']).consume()
                self._audit(tx, 'recovery:'+o['operation_id'], 'local_operator', 'RecoverProcessing', o['document_id'], 'recovered' if committed else 'interrupted', state, state if committed else 'interrupted')
                recovered.append(o['operation_id'])
            return recovered
        with self._session() as session:
            return session.execute_write(write)
