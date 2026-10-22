"""Independent local business runs; parse publication remains immutable."""
from datetime import datetime, timezone
import hashlib
from uuid import UUID, uuid4

from ontology_proj.adapters.local_processing_store import LocalProcessingStore, storage_errors
from ontology_proj.adapters.file_store import _error
from ontology_proj.application.files import FileStoreError


NAMES = {'request', 'response', 'candidate', 'validation', 'records', 'diagnostic'}


class LocalBusinessStore(LocalProcessingStore):
    def _runs(self, document_id):
        path = self._directory(document_id) / 'business-runs'
        if path.is_symlink() or (path.exists() and not path.is_dir()): raise _error('UNSAFE_STORAGE_PATH')
        return path

    def _run(self, document_id, run_id):
        if not isinstance(run_id, str) or str(UUID(run_id)) != run_id: raise ValueError
        path = self._runs(document_id) / run_id
        if path.is_symlink() or (path.exists() and not path.is_dir()): raise _error('UNSAFE_STORAGE_PATH')
        return path

    @storage_errors
    def begin_business(self, document_id, configuration, fresh):
        self.read_artifact(document_id, 'normalized')
        runs = self._runs(document_id)
        previous = []
        if runs.exists():
            for path in runs.iterdir():
                if not path.is_dir() or path.name.startswith('.'): continue
                self._run(document_id, path.name)
                if (path/'diagnostic.json').exists(): previous.append(self._json(path/'diagnostic.json'))
                else: raise FileStoreError('BUSINESS_RUN_INCOMPLETE', 'An incomplete business run cannot be silently reused')
        if previous and any(p['configuration'] != configuration for p in previous):
            raise FileStoreError('EXTRACTION_CONFIG_CONFLICT', 'Use a new baseline directory for changed extraction configuration')
        if previous and not fresh:
            raise FileStoreError('EXPLICIT_RETRY_REQUIRED', 'Explicit fresh execution is required after an unpublished result')
        value = {'run_id': str(uuid4()), 'document_id': document_id, 'scope': 'business_extract', 'status': 'running', 'error_code': None, 'created_at': datetime.now(timezone.utc).isoformat(), 'configuration': configuration}
        self._run(document_id, value['run_id']).mkdir(parents=True)
        self.save_business(document_id, value['run_id'], 'diagnostic', value)
        return value

    @storage_errors
    def save_business(self, document_id, run_id, name, value):
        if name not in NAMES: raise ValueError
        path = self._run(document_id, run_id) / (name+'.json')
        self._regular(path)
        if name != 'diagnostic' and path.exists():
            raise FileStoreError('BUSINESS_ARTIFACT_EXISTS', 'Business attempt artifacts cannot be overwritten')
        self._atomic_write(path, self._encode(value))

    @storage_errors
    def publish_business(self, document_id, run_id):
        self.read_artifact(document_id, 'normalized')
        directory = self._directory(document_id)
        run = self._run(document_id, run_id)
        diagnostic = self._json(run/'diagnostic.json')
        if diagnostic['status'] != 'succeeded': raise ValueError
        artifacts = {}
        for name in NAMES:
            path = run/(name+'.json')
            self._regular(path)
            if not path.exists(): raise _error('ARTIFACT_MISSING')
            data = path.read_bytes()
            artifacts[name] = {'key': f'documents/{document_id}/business-runs/{run_id}/{name}.json', 'sha256': hashlib.sha256(data).hexdigest(), 'bytes':len(data)}
        result = {'document_id':document_id, 'run_id':run_id, 'parse_manifest_sha256':hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(), 'configuration':diagnostic['configuration'], 'artifacts':artifacts}
        path = directory/'business-manifest.json'
        self._regular(path)
        self._atomic_write(path, self._encode(result))
        return result

    @storage_errors
    def read_business(self, document_id):
        self.read_artifact(document_id, 'normalized')
        directory = self._directory(document_id)
        path = directory/'business-manifest.json'
        self._regular(path)
        if not path.exists(): return None
        result = self._json(path)
        if result['document_id'] != document_id or result['parse_manifest_sha256'] != hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest() or set(result['artifacts']) != NAMES: raise ValueError
        run = self._run(document_id, result['run_id'])
        for name, entry in result['artifacts'].items():
            if entry['key'] != f"documents/{document_id}/business-runs/{result['run_id']}/{name}.json": raise ValueError
            artifact = run/(name+'.json')
            self._regular(artifact)
            if not artifact.exists(): raise _error('ARTIFACT_MISSING')
            data = artifact.read_bytes()
            if entry['sha256'] != hashlib.sha256(data).hexdigest() or entry['bytes'] != len(data): raise _error('ARTIFACT_MISMATCH')
        diagnostic = self._json(run/'diagnostic.json')
        if diagnostic['configuration'] != result['configuration'] or diagnostic['status'] != 'succeeded' or diagnostic['run_id'] != result['run_id'] or diagnostic['document_id'] != document_id: raise _error('ARTIFACT_MISMATCH')
        return result

    @storage_errors
    def read_business_artifact(self, document_id, name):
        if name not in NAMES: raise ValueError
        result = self.read_business(document_id)
        if result is None: raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Business results have not been published')
        return self._json(self._run(document_id, result['run_id'])/(name+'.json'))
