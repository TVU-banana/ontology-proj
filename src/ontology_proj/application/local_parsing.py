"""Synchronous local preparation pipeline; not graph registration or an HTTP runner."""
from datetime import datetime, timezone
from uuid import uuid4
import re

from ontology_proj.application.files import FileStoreError
from ontology_proj.application.parsing import ParseStatus
from ontology_proj.application.ports.document_normalizer import DocumentNormalizer
from ontology_proj.application.ports.document_parser import DocumentParser
from ontology_proj.application.ports.local_processing_store import LocalProcessingStore


def _time():
    return datetime.now(timezone.utc).isoformat()


class LocalParsingService:
    def __init__(self, store: LocalProcessingStore, parser: DocumentParser, normalizer: DocumentNormalizer):
        self.store, self.parser, self.normalizer = store, parser, normalizer

    def prepare(self, data, original_filename, context):
        with self.store.exclusive():
            return self.store.save_source(data, original_filename, context)

    def read(self, document_id):
        with self.store.exclusive():
            result = self.store.read_published(document_id)
            if result is None:
                raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Published parse artifacts have not been generated')
            return result

    def recover(self):
        """Explicit startup scan, only when the previous process is no longer running."""
        recovered = []
        with self.store.exclusive():
            for operation in self.store.list_operations():
                if operation['status'] not in ('queued', 'running'):
                    continue
                manifest = self.store.read_published(operation['document_id'])
                if manifest and manifest['operation_id'] == operation['operation_id']:
                    operation.update(status='succeeded_with_warnings' if manifest['warnings'] else 'succeeded', stage='published', error_code=None)
                else:
                    operation.update(status='interrupted', error_code='LOCAL_PROCESS_INTERRUPTED')
                operation['updated_at'] = _time()
                self.store.save_operation(operation)
                recovered.append(operation['operation_id'])
        return recovered

    def process(self, document_id, *, retry=False):
        with self.store.exclusive():
            source = self.store.read_source(document_id)
            manifest = self.store.read_published(document_id)
            if manifest is not None:
                return {'document_id': document_id, 'operation_id': manifest['operation_id'], 'reused': True, 'manifest': manifest}
            previous = [o for o in self.store.list_operations() if o['document_id'] == document_id]
            if any(o['status'] in ('queued', 'running') for o in previous):
                raise FileStoreError('LOCAL_OPERATION_ACTIVE', 'An unfinished operation requires startup recovery, not duplicate execution')
            if previous and not retry:
                raise FileStoreError('EXPLICIT_RETRY_REQUIRED', 'Failed or interrupted processing requires an explicit retry')
            configuration = self.parser.configuration()
            if previous and previous[-1]['configuration'] != configuration:
                raise FileStoreError('PARSER_CONFIG_CONFLICT', 'Retry must use the same fixed parser configuration')
            operation = {'operation_id': str(uuid4()), 'document_id': document_id, 'scope': 'local_parse',
                         'status': 'running', 'stage': 'parsing', 'error_code': None,
                         'created_at': _time(), 'updated_at': _time(), 'configuration': configuration,
                         'artifact_keys': []}
            self.store.save_operation(operation)
            try:
                parsed = self.parser.parse(self.store.source_path(document_id), document_id)
                if parsed.ocr is not None:
                    operation['ocr'] = parsed.ocr
                    self.store.save_operation(operation)
                if parsed.native_document is not None:
                    self.store.save_operation_artifact(operation['operation_id'], 'docling', parsed.native_document)
                    operation['artifact_keys'].append(f"operations/{operation['operation_id']}/docling.json")
                    self.store.save_artifact(document_id, 'docling', parsed.native_document)
                    operation['artifact_keys'].append(f'documents/{document_id}/docling.json')
                if parsed.status != ParseStatus.SUCCESS:
                    code = parsed.diagnostics[0].code if parsed.diagnostics else 'PARSER_CONVERSION_FAILED'
                    operation.update(status='failed', error_code=code)
                else:
                    if parsed.document_id != document_id or parsed.native_document is None:
                        raise FileStoreError('ARTIFACT_MISMATCH', 'Parser result identity is inconsistent')
                    operation.update(stage='normalizing', updated_at=_time())
                    self.store.save_operation(operation)
                    extra = {'pdf_layout': parsed.pdf_layout} if parsed.pdf_layout is not None else {}
                    if parsed.ocr is not None:
                        extra['ocr'] = parsed.ocr
                    normalized = self.normalizer.convert(parsed.native_document, document_id, source.context,
                                                         self.store.page_rotations(document_id), **extra)
                    self.store.save_artifact(document_id, 'normalized', normalized)
                    operation['artifact_keys'].append(f'documents/{document_id}/normalized.json')
                    operation.update(stage='publishing', updated_at=_time())
                    self.store.save_operation(operation)
                    manifest = self.store.publish(document_id, operation['operation_id'], configuration, parsed.versions)
                    operation.update(status='succeeded_with_warnings' if manifest['warnings'] else 'succeeded', stage='published')
                operation['updated_at'] = _time()
                self.store.save_operation(operation)
            except Exception as error:
                # A manifest may have been committed before diagnostic write failed.
                # Do not turn a committed publication into a failed parse.
                if self.store.read_published(document_id) is not None:
                    raise FileStoreError('OPERATION_DIAGNOSTIC_PENDING', 'Artifacts are published; startup recovery must finish operation diagnostics') from None
                code = getattr(error, 'code', 'LOCAL_PROCESS_FAILED')
                if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
                    code = 'LOCAL_PROCESS_FAILED'
                operation.update(status='failed', error_code=code, updated_at=_time())
                self.store.save_operation(operation)
            return {'document_id': document_id, 'operation_id': operation['operation_id'], 'reused': False, 'operation': operation}
