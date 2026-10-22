"""Fixed Neo4j 5.x vector index and transactional per-document publication."""
import json
import time
from uuid import uuid4
from neo4j.exceptions import Neo4jError, DriverError
from ontology_proj.adapters.neo4j_repository import guarded, now
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.dto import CapabilityState, CapabilityStatus
from ontology_proj.domain.graph_mapping import LABELS

INDEX = 'ontology_chunk_vector'


class Neo4jSemantic:
    def __init__(self, graph, dimension, actor='local_operator'):
        self.graph, self.dimension, self.actor = graph, dimension, actor

    @guarded
    def initialize(self, profile):
        with self.graph._session() as session:
            session.run('CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:DocumentChunk) REQUIRE n.chunk_id IS UNIQUE').consume()
            session.run('CREATE CONSTRAINT semantic_profile IF NOT EXISTS FOR (n:SemanticProfile) REQUIRE n.key IS UNIQUE').consume()
            def claim(tx):
                row = tx.run('MERGE (p:SemanticProfile {key:"main"}) ON CREATE SET p.profile=$profile, p.dimension=$dimension SET p.lock=coalesce(p.lock,0)+1 RETURN p.profile AS profile, p.dimension AS dimension', profile=profile, dimension=self.dimension).single()
                if row['profile'] != profile or row['dimension'] != self.dimension:
                    raise FileStoreError('INDEX_CONFIG_CONFLICT', 'Existing vector profile differs; explicit rebuild required')
            session.execute_write(claim)
            session.run('CREATE VECTOR INDEX ontology_chunk_vector IF NOT EXISTS FOR (c:DocumentChunk) ON (c.embedding) OPTIONS {indexConfig: {`vector.dimensions`: $dimension, `vector.similarity_function`: "cosine"}}', dimension=self.dimension).consume()
            for attempt in range(40):
                try:
                    self._index(session)
                    break
                except FileStoreError as exc:
                    if exc.code != 'INDEX_NOT_READY' or attempt == 39: raise
                    time.sleep(0.25)

    def _index(self, session):
        row = session.run('SHOW VECTOR INDEXES YIELD name, state, labelsOrTypes, properties, options WHERE name=$name RETURN state, labelsOrTypes, properties, options', name=INDEX).single()
        if row is None: raise FileStoreError('INDEX_NOT_READY', 'Vector index does not exist')
        config = row['options'].get('indexConfig', {})
        if row['labelsOrTypes'] != ['DocumentChunk'] or row['properties'] != ['embedding'] or config.get('vector.dimensions') != self.dimension or str(config.get('vector.similarity_function')).lower() != 'cosine':
            raise FileStoreError('INDEX_CONFIG_CONFLICT', 'Vector index schema differs')
        if row['state'] != 'ONLINE': raise FileStoreError('INDEX_NOT_READY', 'Vector index is not ONLINE', retryable=True)

    @guarded
    def begin(self, document_id, profile, retry):
        self.initialize(profile)
        run_id = str(uuid4())
        def claim(tx):
            row = tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.index_lock=coalesce(d.index_lock,0)+1 RETURN properties(d) AS value', id=document_id).single()
            if row is None: raise FileStoreError('OBJECT_NOT_FOUND', 'Document does not exist')
            doc = row['value']
            if doc['processing_status'] not in ('graph_ready', 'graph_ready_with_warnings'):
                raise FileStoreError('DOCUMENT_NOT_PUBLISHED', 'Index requires a published graph')
            if doc.get('index_profile') not in (None, profile):
                raise FileStoreError('INDEX_CONFIG_CONFLICT', 'Document profile differs')
            if doc.get('index_status') == 'ready':
                return {'document_id': document_id, 'index_status': 'ready', 'reused': True, 'profile': profile,
                        'chunk_count': doc.get('chunk_count', 0)}
            if doc.get('index_status') in ('running', 'failed') and not retry:
                raise FileStoreError('EXPLICIT_RETRY_REQUIRED', 'Index running or failed; stop old worker then retry')
            tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.index_status="running",d.index_run=$run,d.index_profile=$profile,d.index_updated_at=$time REMOVE d.index_error', id=document_id, run=run_id, profile=profile, time=now()).consume()
            return {'document_id': document_id, 'run_id': run_id, 'reused': False}
        with self.graph._session() as session: return session.execute_write(claim)

    @guarded
    def publish(self, document_id, run_id, profile, chunks, excluded):
        def publish(tx):
            doc = tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.index_lock=coalesce(d.index_lock,0)+1 RETURN properties(d) AS value', id=document_id).single()
            if doc is None or doc['value'].get('index_run') != run_id or doc['value'].get('index_status') != 'running':
                raise FileStoreError('INDEX_OPERATION_SUPERSEDED', 'Index operation was superseded')
            if doc['value']['processing_status'] not in ('graph_ready', 'graph_ready_with_warnings'):
                raise FileStoreError('DOCUMENT_NOT_PUBLISHED', 'Document graph is no longer published')
            tx.run('MATCH (c:DocumentChunk {document_id:$id}) DETACH DELETE c', id=document_id).consume()
            for chunk in chunks:
                props = {k: v for k, v in chunk.items() if k != 'source_spans'}
                props['source_spans_json'] = json.dumps(chunk['source_spans'], ensure_ascii=False)
                tx.run('MATCH (d:SourceDocument {document_id:$id}) CREATE (c:DocumentChunk) SET c += $props CREATE (c)-[:IN_DOCUMENT]->(d)', id=document_id, props=props).consume()
                for span in chunk['source_spans']:
                    source = tx.run('MATCH (b:TextBlock {block_id:$block,document_id:$doc}) RETURN b.text AS text', block=span['block_id'], doc=document_id).single()
                    if source is None or source['text'][slice(*span['charspan'])] != span['quote']:
                        raise FileStoreError('EVIDENCE_QUOTE_MISMATCH', 'Graph block differs from published chunk source')
                    props = {k: v for k, v in span.items() if k != 'target_field'}
                    props['target_field'] = 'chunk_source'
                    tx.run('MATCH (c:DocumentChunk {chunk_id:$chunk}), (b:TextBlock {block_id:$block,document_id:$doc}) MERGE (e:Evidence {evidence_id:$evidence}) ON CREATE SET e += $props MERGE (e)-[:FROM_BLOCK]->(b) MERGE (c)-[:CITES]->(e)', chunk=chunk['chunk_id'], block=span['block_id'], doc=document_id, evidence=span['evidence_id'], props=props).consume()
            tx.run('MATCH (d:SourceDocument {document_id:$id}) SET d.index_status="ready",d.index_profile=$profile,d.chunk_count=$count,d.index_excluded_json=$excluded,d.index_updated_at=$time REMOVE d.index_error', id=document_id, profile=profile, count=len(chunks), excluded=json.dumps(excluded), time=now()).consume()
            self.graph._audit(tx, 'index:'+run_id, self.actor, 'IndexDocument', document_id, 'succeeded', 'running', 'ready')
        with self.graph._session() as session:
            self._index(session)
            session.execute_write(publish)

    @guarded
    def fail(self, document_id, run_id, code):
        def fail(tx):
            row = tx.run('MATCH (d:SourceDocument {document_id:$id,index_run:$run}) WHERE d.index_status="running" SET d.index_status="failed",d.index_error=$code,d.index_updated_at=$time RETURN d.document_id AS id', id=document_id, run=run_id, code=code, time=now()).single()
            if row:
                self.graph._audit(tx, 'index:'+run_id, self.actor, 'IndexDocument', document_id, 'failed', 'running', 'failed', code)
        with self.graph._session() as session:
            session.execute_write(fail)

    @guarded
    def allowed_documents(self, scope):
        with self.graph._session() as session:
            rows = session.run('MATCH (d:SourceDocument) WHERE d.processing_status IN ["graph_ready","graph_ready_with_warnings"] AND (size($docs)=0 OR d.document_id IN $docs) AND ($bridge IS NULL OR d.bridge_id=$bridge) AND ($synthetic OR d.source_kind="real") RETURN properties(d) AS value LIMIT 101', docs=scope['document_ids'], bridge=scope['bridge_id'], synthetic=scope['include_synthetic']).data()
            if len(rows) > 100: raise FileStoreError('RESULT_LIMIT_EXCEEDED', 'Narrow the semantic document scope')
            return [r['value'] for r in rows]

    @guarded
    def search(self, vector, profile, pool_limit):
        with self.graph._session() as session:
            self._index(session)
            config = session.run('MATCH (p:SemanticProfile {key:"main"}) RETURN p.profile AS profile').single()
            if config is None or config['profile'] != profile: raise FileStoreError('INDEX_CONFIG_CONFLICT', 'Query profile differs from index')
            rows = session.run('CALL db.index.vector.queryNodes($name,$pool,$vector) YIELD node, score WHERE node.profile=$profile MATCH (node)-[:IN_DOCUMENT]->(d:SourceDocument) WHERE d.index_status="ready" AND d.index_profile=$profile RETURN properties(node) AS value, score', name=INDEX, pool=pool_limit, vector=vector, profile=profile).data()
            result = []
            for row in rows:
                chunk = dict(row['value']);chunk['source_spans'] = json.loads(chunk.pop('source_spans_json'))
                chunk['score'] = row['score'];chunk['current_records'] = []
                for label, identity, _ in LABELS.values():
                    if chunk.get('record_type') != label: continue
                    ancestry = {
                        'Bridge': 'WITH n, n AS bridge, null AS component',
                        'Component': 'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(n) WITH n, bridge, n AS component',
                        'Defect': 'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(component:Component)-[:HAS_DEFECT]->(n) WITH n, bridge, component',
                        'Observation': 'MATCH (bridge:Bridge)-[:HAS_COMPONENT]->(component:Component)-[:HAS_DEFECT]->(:Defect)-[:HAS_OBSERVATION]->(n) WITH n, bridge, component',
                    }[label]
                    states = session.run(f'''MATCH (n:{label}) WHERE n.{identity} IN $ids {ancestry}
WHERE ($bridge IS NULL OR bridge.bridge_id=$bridge) AND (size($components)=0 OR component.component_id IN $components)
MATCH (n)-[r:SUPPORTED_BY]->(e:Evidence {{document_id:$doc}})-[:FROM_BLOCK]->(:TextBlock)-[:IN_DOCUMENT]->(:SourceDocument {{document_id:$doc}})
RETURN n.{identity} AS id,n.acceptance_status AS acceptance_status,n.review_status AS review_status,n.observed_at AS observed_at,collect(DISTINCT r.acceptance_status) AS states''', doc=chunk['document_id'], ids=chunk['record_ids'], bridge=chunk.get('bridge_id'), components=chunk['component_ids']).data()
                    for state in states:
                        state['source_acceptance_status'] = 'accepted' if state.pop('states') == ['accepted'] else 'pending_review'
                        chunk['current_records'].append(state)
                        if chunk.get('record_type') == 'Observation': chunk['observed_at'] = state.get('observed_at')
                result.append(chunk)
            return result

    def check(self, profile=None):
        if not self.dimension: return CapabilityStatus(CapabilityState.NOT_CONFIGURED, False)
        try:
            with self.graph._session() as session:
                self._index(session)
                if profile is not None:
                    row = session.run('MATCH (p:SemanticProfile {key:"main"}) RETURN p.profile AS profile').single()
                    if row is None or row['profile'] != profile:
                        raise FileStoreError('INDEX_CONFIG_CONFLICT', 'Configured profile differs from index')
            return CapabilityStatus(CapabilityState.READY, True)
        except FileStoreError as exc: return CapabilityStatus(CapabilityState.UNAVAILABLE, True, exc.code)
        except (Neo4jError, DriverError, OSError): return CapabilityStatus(CapabilityState.UNAVAILABLE, True, 'GRAPH_UNAVAILABLE')


class SemanticReadiness:
    def __init__(self, repository, provider): self.repository, self.provider = repository, provider

    def check(self):
        status = self.provider.check()
        if status.state != CapabilityState.READY: return status
        return self.repository.check(self.provider.profile)
