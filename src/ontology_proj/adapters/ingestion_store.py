"""Formal runner's local artifacts and process lease; never graph state truth."""
from datetime import datetime, timezone
import fcntl
from io import BytesIO
from uuid import UUID
from contextlib import contextmanager
from threading import RLock

from ontology_proj.adapters.business_store import LocalBusinessStore
from ontology_proj.adapters.local_processing_store import storage_errors
from ontology_proj.application.files import FileStoreError


class IngestionStore(LocalBusinessStore):
    def __init__(self,data_root):
        super().__init__(data_root)
        self._pipeline_lock=RLock()

    @contextmanager
    def exclusive(self):
        # The single runner owns stage writes; this scope serializes parsing and
        # extraction without holding the short source-file lock over model work.
        # Immutable source reads and formal duplicate requests remain responsive.
        with self._pipeline_lock:
            yield

    def validate_pdf(self, data, max_pages):
        from pypdf import PdfReader
        if not data.startswith(b'%PDF-'):
            raise FileStoreError('INVALID_PDF', 'Input must be a readable PDF')
        try:
            pdf = PdfReader(BytesIO(data), strict=True)
            if pdf.is_encrypted or not pdf.pages:
                raise ValueError
            count = len(pdf.pages)
        except Exception:
            raise FileStoreError('INVALID_PDF', 'PDF is invalid, empty or encrypted') from None
        if count > max_pages:
            raise FileStoreError('PAGE_LIMIT_EXCEEDED', 'PDF exceeds page limit')
        return count

    @storage_errors
    def save_registration_failure(self, document_id, code):
        directory=self._directory(document_id)
        path=directory/'registration-failure.json'
        self._regular(path)
        self._atomic_write(path,self._encode(dict(document_id=document_id,stage='registering',error_code=code,formal_registration='failed',graph_audit='unavailable')))

    @storage_errors
    def read_latest_business_artifact(self, document_id, name):
        runs = self._runs(document_id)
        if not runs.exists():
            raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Extraction has not generated candidates')
        attempts = [self._json(p/'diagnostic.json') for p in runs.iterdir() if p.is_dir() and not p.name.startswith('.') and (p/'diagnostic.json').exists()]
        if not attempts:
            raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Extraction has not generated candidates')
        latest = max(attempts, key=lambda r: r['created_at'])
        path = self._run(document_id, latest['run_id'])/(name+'.json')
        if not path.exists():
            raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Latest attempt has no candidate artifact')
        return self._json(path)

    @storage_errors
    def interrupt_business_runs(self, document_id):
        runs = self._runs(document_id)
        if not runs.exists():
            return
        for path in runs.iterdir():
            if not path.is_dir() or path.name.startswith('.'): continue
            run = self._json(self._run(document_id, path.name)/'diagnostic.json')
            if run['status'] == 'running':
                run.update(status='interrupted', error_code='MODEL_REQUEST_OUTCOME_UNKNOWN')
                self.save_business(document_id, run['run_id'], 'diagnostic', run)

    @storage_errors
    def business_artifact_keys(self, document_id, run_id):
        return [f'documents/{document_id}/business-runs/{run_id}/{p.name}' for p in self._run(document_id, run_id).iterdir() if p.is_file() and not p.is_symlink()]

    @storage_errors
    def save_ingestion_failure(self, operation_id, code, artifacts):
        if str(UUID(operation_id)) != operation_id:
            raise ValueError
        directory = self._root/'ingestion-failures'
        if directory.is_symlink():
            raise FileStoreError('UNSAFE_STORAGE_PATH', 'Failure diagnostic path is unsafe')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/(operation_id+'.json')
        self._regular(path)
        self._atomic_write(path, self._encode(dict(operation_id=operation_id, error_code=code, artifact_keys=artifacts, graph_audit='unavailable', updated_at=datetime.now(timezone.utc).isoformat())))


class ProcessLease:
    """OS lock released on process death; refuse two app writers on one DATA_ROOT."""
    def __init__(self, store):
        self.store, self.stream = store, None

    def acquire(self):
        root = self.store._root
        if root.is_symlink() or root.exists() and not root.is_dir():
            raise FileStoreError('FILE_STORE_UNAVAILABLE', 'Data root is not available')
        root.mkdir(parents=True, exist_ok=True)
        path = root/'.ingestion.lock'
        self.store._regular(path)
        stream = path.open('a+b')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise FileStoreError('PROCESS_ALREADY_RUNNING', 'Another app process owns this data root') from None
        self.stream = stream

    def release(self):
        if self.stream:
            self.stream.close()
            self.stream = None
