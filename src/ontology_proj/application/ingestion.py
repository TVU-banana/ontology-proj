"""Single-process ingestion orchestration shared by HTTP and future tools."""
from dataclasses import asdict
import hashlib
import json
import re
from queue import Queue
from threading import RLock, Thread

from ontology_proj.application.files import FileStoreError
from ontology_proj.application.ports.graph_repository import GraphRepository
from ontology_proj.application.ports.bridge_identity import BridgeDirectory


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class IngestionService:
    def __init__(self, store, parsing, extraction, graph: GraphRepository, *, validator=None, catalog: BridgeDirectory | None=None, actor='local_operator', lease=None, max_upload_bytes=50*1024*1024, max_pages=100):
        self.store, self.parsing, self.extraction, self.graph = store, parsing, extraction, graph
        self.validator = validator if validator is not None else getattr(extraction, 'validator', None)
        self.catalog = catalog
        self.actor, self.lease = actor, lease
        self.max_upload_bytes, self.max_pages = max_upload_bytes, max_pages
        self._writes = RLock()
        self._queue = Queue()
        self._thread = None
        self._initialized = False

    def _request(self, key, use_case, payload):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
            raise FileStoreError('INVALID_REQUEST', 'A valid Idempotency-Key is required')
        return dict(key=fingerprint([self.actor, use_case, key]), payload_hash=fingerprint(payload), actor=self.actor, use_case=use_case)

    def _ensure(self):
        if self.lease is not None and self.lease.stream is None:
            raise FileStoreError('FILE_STORE_UNAVAILABLE', 'Application writer lease is unavailable')
        if not self._initialized:
            self.graph.initialize()
            self.graph.recover()
            self.parsing.recover()
            if self.catalog and self.validator and hasattr(self.graph,'existing_bridges'):
                for bridge in self.graph.existing_bridges():
                    document_id=bridge.get('source_document_id')
                    if not document_id:continue
                    try:
                        document=self.store.read_artifact(document_id,'normalized')
                        records=self.store.read_business_artifact(document_id,'records')
                    except FileStoreError:
                        # Do not import graph-only or another DATA_ROOT's identity.
                        continue
                    matches=[r for r in records['bridges'] if r['bridge_id']==bridge['bridge_id'] and r['name']==bridge.get('name') and r.get('bridge_code')==bridge.get('bridge_code')]
                    if len(matches)==1 and not self.validator.validate_records(records,document):
                        self.catalog.adopt_existing(matches[0],document['source_kind'])
            self._initialized = True

    def start(self):
        if self.lease:
            self.lease.acquire()
        try:
            with self._writes:
                self._ensure()
        except FileStoreError:
            # Missing/down graph is a partial availability state, not a health failure.
            pass
        self._thread = Thread(target=self._worker, name='ingestion-runner', daemon=True)
        self._thread.start()

    def close(self):
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join()
            self._thread = None
        self.graph.close()
        if self.lease:
            self.lease.release()

    def upload(self, data, filename, context, key):
        if len(data) > self.max_upload_bytes:
            raise FileStoreError('UPLOAD_TOO_LARGE', 'PDF exceeds upload limit')
        request = self._request(key, 'register_document', dict(sha256=hashlib.sha256(data).hexdigest(), meta=asdict(context)))
        with self._writes:
            page_count=self.store.validate_pdf(data, self.max_pages)
            try:
                self._ensure()
                cached = self.graph.replay(request)
            except FileStoreError as error:
                if error.code=='GRAPH_UNAVAILABLE':
                    source=self.store.save_source(data,filename,context).source
                    self.store.save_registration_failure(source.document_id,error.code)
                raise
            if cached is not None:
                self.store.read_source(cached['document_id'])
                return cached
            # Validate PDF before writing; registration failure preserves a local draft.
            # A resolved identity may differ from immutable original upload metadata.
            # Re-upload with the canonical selection reuses original source bytes/context.
            effective_context=context
            if self.catalog:
                try:
                    existing=self.store.read_source('doc_'+hashlib.sha256(data).hexdigest())
                except FileStoreError as error:
                    if error.code!='SOURCE_NOT_FOUND':raise
                else:
                    if context.bridge_id!=existing.context.bridge_id:
                        try:
                            doc=self.store.read_artifact(existing.document_id,'normalized')
                            binding=self.catalog.binding(doc)
                        except FileStoreError:
                            binding=None
                        if binding and context.bridge_id==binding['bridge_id'] and context.kind==existing.context.kind and context.source_kind==existing.context.source_kind:
                            effective_context=existing.context
            source = self.store.save_source(data, filename, effective_context).source
            props = asdict(source)
            props.pop('context')
            props['bytes']=props.pop('bytes_count')
            props.update(kind=context.kind.value, source_kind=context.source_kind.value, bridge_id=source.context.bridge_id,upload_bridge_id=source.context.bridge_id,page_count=page_count,updated_at=source.created_at)
            try:
                return self.graph.register(props, request)
            except FileStoreError as error:
                if error.code=='GRAPH_UNAVAILABLE':self.store.save_registration_failure(source.document_id,error.code)
                raise

    def request_processing(self, document_id, key, *, retry=False, stage='failed_stage'):
        if stage != 'failed_stage':
            raise FileStoreError('STAGE_NOT_RETRYABLE', 'Only failed ingestion stages can be retried')
        request = self._request(key, 'retry_document' if retry else 'process_document', dict(document_id=document_id, stage=stage))
        with self._writes:
            self._ensure()
            cached = self.graph.replay(request)
            if cached is not None:
                return cached
            self.store.read_source(document_id)
            if self.extraction is None:
                published = self.store.read_business(document_id)
                if published is None:
                    raise FileStoreError('MODEL_NOT_CONFIGURED', 'Extraction model is not configured and no verified publication exists')
                extractor_configuration = published['configuration']
            else:
                extractor=self.extraction.extractor
                extractor_configuration = extractor.configuration_for(document_id) if hasattr(extractor,'configuration_for') else extractor.configuration()
            configuration = fingerprint(dict(parser=self.parsing.parser.configuration(), extractor=extractor_configuration))
            result = self.graph.request_processing(document_id, request, configuration, retry)
            if result.pop('enqueue'):
                self._queue.put(result['operation_id'])
            return result

    def document(self, document_id):
        result = self.graph.document(document_id)
        if self.catalog:
            try:result['bridge_resolution']=self.resolve_bridge(document_id, read_only=True)
            except FileStoreError as e:result['bridge_resolution']={'status':'not_available','reason':e.code}
        return result

    def bridges(self):
        if not self.catalog:raise FileStoreError('BRIDGE_CATALOG_NOT_CONFIGURED','Bridge directory unavailable')
        return self.catalog.list()

    def register_bridge(self,body):
        if not self.catalog:raise FileStoreError('BRIDGE_CATALOG_NOT_CONFIGURED','Bridge directory unavailable')
        with self._writes:
            if self.lease is not None and self.lease.stream is None:raise FileStoreError('FILE_STORE_UNAVAILABLE','Application writer lease unavailable')
            self._ensure()  # Discover locally verified legacy identities before allowing a new ID.
            return self.catalog.register(body)

    def resolve_bridge(self,document_id,selected=None,read_only=False,confirm_details=False):
        if not self.catalog:raise FileStoreError('BRIDGE_CATALOG_NOT_CONFIGURED','Bridge directory unavailable')
        with self._writes:
            document=self.store.read_artifact(document_id,'normalized')
            published=self.store.read_business(document_id)
            if published:
                records=self.store.read_business_artifact(document_id,'records')
                if records.get('identity_mode')!='catalog':return {'status':'legacy_preserved','bridge_ids':[b['bridge_id'] for b in records['bridges']]}
                return self.catalog.binding(document)
            registration=getattr(self.validator,'registration',None)
            if registration and registration.get('document_id')==document_id:return {'status':'legacy_registration','bridge_id':document['import_context']['bridge_id']}
            if read_only:
                return self.catalog.status(document)
            if self.lease is not None and self.lease.stream is None:raise FileStoreError('FILE_STORE_UNAVAILABLE','Application writer lease unavailable')
            return self.catalog.resolve(document,selected or document.get('import_context',{}).get('bridge_id'),confirm_details)

    def operation(self, operation_id):
        return self.graph.operation(operation_id)

    def artifact(self, document_id, artifact):
        self.graph.document(document_id)
        if artifact == 'candidates':
            if self.store.read_business(document_id) is not None:
                return self.store.read_business_artifact(document_id,'candidate')
            return self.store.read_latest_business_artifact(document_id, 'candidate')
        if artifact not in ('docling', 'normalized'):
            raise FileStoreError('INVALID_REQUEST', 'Unknown artifact type')
        return self.store.read_artifact(document_id, artifact)

    def pdf(self, document_id):
        self.graph.document(document_id)
        return self.store.read_pdf(document_id)

    def _worker(self):
        while True:
            operation_id = self._queue.get()
            try:
                if operation_id is None:
                    return
                self.run(operation_id)
            finally:
                self._queue.task_done()

    def run(self, operation_id):
        artifacts = []
        counts = None
        try:
            operation = self.graph.operation(operation_id)
            document_id = operation['document_id']
            self.graph.advance(operation_id, 'parsing', 'parsing')
            parsed = self.parsing.process(document_id, retry=bool(operation.get('retry_of')))
            if not parsed['reused'] and parsed['operation']['status'] == 'failed':
                raise FileStoreError(parsed['operation']['error_code'], 'Parsing failed')
            normalized = self.store.read_artifact(document_id, 'normalized')
            artifacts += [f'documents/{document_id}/docling.json', f'documents/{document_id}/normalized.json']
            self.graph.advance(operation_id, 'parsed', 'parsed')
            published_before_identity = self.store.read_business(document_id)
            if self.catalog and published_before_identity is None:
                resolution=self.resolve_bridge(document_id)
                if resolution['status']=='needs_confirmation':
                    raise FileStoreError('BRIDGE_CONFIRMATION_REQUIRED','桥梁归属待确认；请读取归属结果并选择已有桥或登记新桥')
            self.graph.advance(operation_id, 'extracting', 'extracting')
            # Reuse only hash-verified publication. Explicit retry can create a new
            # attempt after rejection/interruption; no model request on graph retry.
            published = self.store.read_business(document_id)
            if published is None:
                if self.extraction is None:
                    raise FileStoreError('MODEL_NOT_CONFIGURED', 'Extraction model is not configured and no verified publication exists')
                self.store.interrupt_business_runs(document_id)
                result = self.extraction.execute(document_id, fresh=bool(operation.get('retry_of')))
                artifacts += self.store.business_artifact_keys(document_id, result['run_id'])
                if result['status'] != 'succeeded':
                    try:
                        candidate=self.store.read_latest_business_artifact(document_id,'candidate')
                        total=len(candidate.get('entities',[])) if isinstance(candidate.get('entities'),list) else 0
                        counts=dict(candidate_count=total,accepted_count=0,pending_count=0,rejected_count=total)
                    except FileStoreError:
                        pass
                    raise FileStoreError(result['error_code'], 'Business extraction or validation failed')
            else:
                artifacts += [v['key'] for v in published['artifacts'].values()]
            records = self.store.read_business_artifact(document_id, 'records')
            if self.validator is None:
                raise FileStoreError('EXTRACTION_INVALID', 'Authoritative record validator is unavailable')
            errors = self.validator.validate_records(records, normalized)
            if errors:
                raise FileStoreError('EXTRACTION_INVALID', 'Published records failed authoritative evidence verification')
            self.graph.advance(operation_id, 'graph_writing', 'extracting')
            with self._writes:
                if records.get('identity_mode')=='catalog':normalized={**normalized,'bridge_binding':self.catalog.binding(normalized)}
                self.graph.commit_bundle(operation_id, normalized, records, artifacts)
        except Exception as error:
            code = getattr(error, 'code', 'PROCESS_FAILED')
            if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
                code = 'PROCESS_FAILED'
            try:
                with self._writes:
                    self.graph.fail(operation_id, code, artifacts, counts)
            except Exception:
                # Graph may be unavailable; retain independent failure diagnostics.
                try:
                    self.store.save_ingestion_failure(operation_id, code, artifacts)
                except Exception:
                    # Both persistence channels failed. Keep the runner alive and
                    # disclose the degraded audit without exception/secret text.
                    import logging
                    logging.getLogger(__name__).error('INGESTION_AUDIT_UNAVAILABLE operation=%s code=%s', operation_id, code)
