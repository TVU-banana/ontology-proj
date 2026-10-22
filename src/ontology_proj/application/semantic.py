"""Pure provenance-preserving chunks and bounded semantic/context use cases."""
from hashlib import sha256
import json
from math import isfinite, sqrt
from ontology_proj.application.files import FileStoreError
from ontology_proj.application.query import validate, LookupGraph, day
from ontology_proj.domain.graph_mapping import LABELS

QUERY_INSTRUCTION = 'Given a search query, retrieve relevant passages that answer the query'
CHUNK_VERSION = 'evidence-fields-1.0'


def stable(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_vectors(vectors, count, dimension):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise FileStoreError('EMBEDDING_INVALID', 'Embedding count differs from input')
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension or any(type(v) not in (int, float) or not isfinite(v) for v in vector):
            raise FileStoreError('EMBEDDING_INVALID', 'Embedding values or dimensions are invalid')
        norm = sqrt(sum(v*v for v in vector))
        if not isfinite(norm) or norm <= 0:
            raise FileStoreError('EMBEDDING_INVALID', 'Embedding is zero or not finite')
        result.append([v/norm for v in vector])
    return result


def build_chunks(document, records, profile):
    blocks = {b['block_id']: b for b in document['blocks']}
    evidence = {e['evidence_id']: e for e in records['evidence']}
    chunks, excluded = [], []

    def spans(sources):
        result = []
        seen = set()
        for source in sources:
            block = blocks.get(source['block_id'])
            quote = source.get('quote', source.get('exact_quote', ''))
            if not block or not quote or quote not in block['text']:
                raise FileStoreError('EVIDENCE_QUOTE_MISMATCH', 'Chunk source differs from published text')
            span = source.get('charspan')
            if span is None:
                if block['text'].count(quote) != 1:
                    excluded.append({'block_id': block['block_id'], 'reason': 'AMBIGUOUS_QUOTE'})
                    continue
                start = block['text'].index(quote); span = [start, start+len(quote)]
            if not isinstance(span, list) or len(span) != 2 or any(type(x) is not int for x in span) or not 0 <= span[0] < span[1] <= len(block['text']) or block['text'][span[0]:span[1]] != quote:
                raise FileStoreError('EVIDENCE_QUOTE_MISMATCH', 'Chunk source span differs from text')
            location = next((l for l in block['locations'] if l.get('bbox') and l['charspan'][0] <= span[0] and l['charspan'][1] >= span[1]), None)
            if location is None:
                excluded.append({'block_id': block['block_id'], 'reason': 'LOCATION_UNAVAILABLE'})
                continue
            key = (block['block_id'], *span)
            if key in seen: continue
            seen.add(key)
            # A long quote is windowed within its own source block, not guessed geometry.
            offset = 0
            while offset < len(quote):
                end = min(offset+1100, len(quote))
                result.append({'block_id': block['block_id'], 'quote': quote[offset:end],
                               'charspan': [span[0]+offset, span[0]+end], 'page_no': location['page_no'],
                               'bbox': location['bbox'], 'source_ref': block['source_ref'],
                               'target_field': source.get('target_field', 'source_statement'), 'location_level': 'region'})
                if end == len(quote): break
                offset = end-150
        return result

    def emit(sources, meta):
        located = spans(sources)
        group, length = [], 0
        def flush():
            if not group: return
            text = '\n'.join(s['target_field']+': '+s['quote'] for s in group)
            identity = stable([profile, document['document_id'], meta, group])
            source_spans = []
            for s in group:
                eid = 'ev_chunk_'+stable([document['document_id'], s])
                source_spans.append(dict(s, evidence_id=eid, document_id=document['document_id']))
            chunks.append(dict(meta, chunk_id='chunk_'+identity, document_id=document['document_id'],
                               bridge_id=document['import_context'].get('bridge_id'), source_kind=document['source_kind'],
                               text_for_embedding=text, profile=profile, source_spans=source_spans,
                               evidence_ids=[s['evidence_id'] for s in source_spans]))
        for span in located:
            size = len(span['quote'])+len(span['target_field'])+2
            if group and length+size > 1200:
                flush(); group, length = [], 0
            group.append(span); length += size+1
        flush()

    for plural, (label, field, _) in LABELS.items():
        for record in records[plural]:
            if record['acceptance_status'] != 'accepted':
                excluded.append({'record_id': record[field], 'reason': 'NOT_ACCEPTED'})
                continue
            refs = [evidence[eid] for eid in record['evidence_ids']]
            refs.sort(key=lambda e: (e['target_field'] != 'description', e['block_id'], e['target_field']))
            component = record.get('component_id')
            emit(refs, {'content_kind': 'business_fact', 'semantic_kind': 'positive', 'statement_kind': None,
                        'record_ids': [record[field]], 'component_ids': [component] if component else [],
                        'observed_at': record.get('observed_at'), 'record_type': label})
    for statement in records.get('statements', []):
        component = statement.get('component_ref')
        emit(statement['sources'], {'content_kind': 'source_statement', 'semantic_kind': statement['kind'],
                                     'statement_kind': 'other', 'record_ids': [],
                                     'component_ids': [component] if component else [], 'observed_at': None,
                                     'record_type': None})
    covered = {s['block_id'] for c in chunks for s in c['source_spans']}
    excluded.extend({'block_id': bid, 'reason': 'UNSELECTED_BLOCK'} for bid in blocks if bid not in covered)
    return chunks, excluded


class IndexDocument:
    def __init__(self, inputs, repository, provider, profile, dimension):
        self.inputs, self.repository, self.provider = inputs, repository, provider
        self.profile, self.dimension = profile, dimension

    def execute(self, document_id, retry=False):
        if type(retry) is not bool:
            raise FileStoreError('INVALID_REQUEST', 'retry must be boolean')
        if not self.dimension:
            raise FileStoreError('EMBEDDING_NOT_CONFIGURED', 'Embedding dimension is not configured')
        operation = self.repository.begin(document_id, self.profile, retry)
        if operation['reused']: return operation
        run_id = operation['run_id']
        try:
            document, records = self.inputs.read(document_id)
            chunks, excluded = build_chunks(document, records, self.profile)
            for start in range(0, len(chunks), 16):
                batch = chunks[start:start+16]
                vectors = validate_vectors(self.provider.embed([c['text_for_embedding'] for c in batch]), len(batch), self.dimension)
                for chunk, vector in zip(batch, vectors): chunk['embedding'] = vector
            self.repository.publish(document_id, run_id, self.profile, chunks, excluded)
            return {'document_id': document_id, 'index_status': 'ready', 'reused': False,
                    'chunk_count': len(chunks), 'excluded': excluded, 'profile': self.profile}
        except Exception as exc:
            code = exc.code if isinstance(exc, FileStoreError) else 'INDEXING_FAILED'
            try: self.repository.fail(document_id, run_id, code)
            except FileStoreError: pass  # Preserve the cause; graph unavailable means status outcome is unknown.
            if isinstance(exc, FileStoreError): raise
            raise FileStoreError(code, 'Document indexing failed') from None


def search_request(request):
    if not isinstance(request, dict) or set(request)-{'query', 'scope', 'top_k', 'graph_query'}:
        raise FileStoreError('INVALID_REQUEST', 'Unknown semantic fields')
    query = request.get('query')
    k = request.get('top_k', 5)
    if not isinstance(query, str) or not query.strip() or len(query) > 2000 or type(k) is not int or not 1 <= k <= 20:
        raise FileStoreError('INVALID_REQUEST', 'Query or top_k is invalid')
    scope = validate({'query_type': 'record_by_id', 'record_id': '_scope_only_', 'scope': request.get('scope')})['scope']
    return query, scope, k


def visible(chunk, scope, allowed):
    if chunk['document_id'] not in allowed: return False
    if scope['bridge_id'] and chunk.get('bridge_id') != scope['bridge_id']: return False
    if not scope['include_synthetic'] and chunk['source_kind'] != 'real': return False
    if scope['component_ids'] and not set(scope['component_ids']).intersection(chunk['component_ids']): return False
    if chunk['content_kind'] == 'business_fact':
        states = chunk.get('current_records', [])
        if len(states) != len(chunk['record_ids']) or any(s['acceptance_status'] != 'accepted' or s['source_acceptance_status'] != 'accepted' for s in states): return False
        if scope['review_mode'] == 'confirmed_only' and any(s.get('review_status') != 'confirmed' for s in states): return False
        date = chunk.get('observed_at')
        if scope['date_from'] or scope['date_to']:
            if chunk['record_type'] != 'Observation' or not date: return False
            try: day(date)
            except FileStoreError:
                raise FileStoreError('GRAPH_RECORD_INVALID', 'Stored observation date is invalid') from None
            if scope['date_from'] and date < scope['date_from'] or scope['date_to'] and date > scope['date_to']: return False
    elif scope['review_mode'] == 'confirmed_only': return False
    return True


class SemanticSearch:
    def __init__(self, repository, provider, profile, dimension):
        self.repository, self.provider, self.profile, self.dimension = repository, provider, profile, dimension

    def execute(self, request):
        query, scope, k = search_request(request)
        try:
            documents = self.repository.allowed_documents(scope)
            bridges = {d.get('bridge_id') for d in documents if d.get('bridge_id')}
            if not scope['bridge_id'] and len(bridges) > 1:
                return {'status': 'needs_clarification', 'chunks': [], 'warnings': ['Select a bridge'], 'error_code': None}
            if not documents: return {'status': 'ready', 'chunks': [], 'warnings': [], 'error_code': None}
            if any(d.get('index_status') != 'ready' or d.get('index_profile') != self.profile for d in documents):
                raise FileStoreError('INDEX_NOT_READY', 'Selected documents are not indexed with this profile')
            vector = validate_vectors(self.provider.embed([query], query=True), 1, self.dimension)[0]
            rows = self.repository.search(vector, self.profile, 200)
            allowed = {d['document_id'] for d in documents}
            warnings = ['Vector candidates are capped at 200; filtered results may be fewer than top_k']
            chunks = []
            for row in sorted(rows, key=lambda r: (-r['score'], r['chunk_id'])):
                if visible(row, scope, allowed) and row['chunk_id'] not in {c['chunk_id'] for c in chunks}:
                    item = {key: value for key, value in row.items() if key not in ('embedding', 'current_records')}
                    states = row.get('current_records', [])
                    item['acceptance_status'] = 'accepted' if row['content_kind'] == 'business_fact' else 'source_only'
                    item['source_acceptance_status'] = item['acceptance_status']
                    item['review_status'] = 'confirmed' if states and all(s.get('review_status') == 'confirmed' for s in states) else 'unreviewed'
                    chunks.append(item)
                if len(chunks) == k: break
            return {'status': 'ready', 'chunks': chunks, 'warnings': warnings, 'error_code': None}
        except FileStoreError as exc:
            return {'status': 'degraded', 'chunks': [], 'warnings': ['Exact graph lookup remains independent'], 'error_code': exc.code}


class BuildContext:
    def __init__(self, search, lookup): self.search, self.lookup = search, lookup

    def execute(self, request):
        _, scope, _ = search_request(request)
        graph_query = request.get('graph_query')
        facts, warnings = [], []
        if graph_query is not None:
            if not isinstance(graph_query, dict) or set(graph_query) & {'scope', 'acceptance_mode'}:
                raise FileStoreError('INVALID_REQUEST', 'Context graph scope cannot be overridden')
            result = self.lookup.execute({**graph_query, 'scope': scope, 'acceptance_mode': 'accepted'})
            facts = result['records']; warnings.extend(result['warnings'])
        search = self.search.execute({key: value for key, value in request.items() if key != 'graph_query'})
        if search['status'] == 'needs_clarification': facts = []
        output = {'status': search['status'], 'facts': [], 'business_passages': [], 'source_statements': [],
                  'warnings': warnings+search['warnings'], 'error_code': search['error_code'], 'scope': scope}
        used = 0; seen = set()
        # Exact facts first; semantic items retain query ranking across categories.
        # Grouping the response must not let lower-ranked facts consume the budget
        # before the relevant negative/source statement.
        ranked = [('facts', facts)] + [
            ('business_passages' if c['content_kind'] == 'business_fact' else 'source_statements', [c])
            for c in search['chunks']]
        for key, items in ranked:
            for item in items:
                identity = item.get('record_id', item.get('chunk_id'))
                if identity in seen: continue
                size = len(json.dumps(item, ensure_ascii=False))
                if used+size > 8000:
                    output['warnings'].append('Context budget excluded complete items'); continue
                seen.add(identity); output[key].append(item); used += size
        output['content_characters'] = used
        return output
