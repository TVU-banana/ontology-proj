"""Local parse artifacts/diagnostics; manifest is a verified publication marker."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import re
from uuid import UUID

from ontology_proj.adapters.file_store import LocalFileStore, _error
from ontology_proj.application.files import FileStoreError


_ARTIFACTS = {'docling': 'docling.json', 'normalized': 'normalized.json'}
_STATUSES = {'queued', 'running', 'succeeded', 'succeeded_with_warnings', 'failed', 'interrupted'}


def storage_errors(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except OSError:
            raise _error('FILE_STORE_UNAVAILABLE') from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _error('ARTIFACT_MISMATCH') from None
    return guarded


class LocalProcessingStore(LocalFileStore):
    @contextmanager
    def exclusive(self):
        with self._lock:
            yield

    @staticmethod
    def _encode(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')

    def _json(self, path):
        self._regular(path)
        if not path.exists(): raise _error('ARTIFACT_MISSING')
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict): raise ValueError
        return value

    @storage_errors
    def source_path(self, document_id):
        self.read_source(document_id)
        return str(self._directory(document_id) / 'source.pdf')

    @storage_errors
    def page_rotations(self, document_id):
        import pypdfium2 as pdfium
        try:
            with pdfium.PdfDocument(self.source_path(document_id)) as document:
                result = {}
                for i in range(len(document)):
                    page = document[i]
                    result[i+1] = page.get_rotation()
                    page.close()
                return result
        except FileStoreError:
            raise
        except Exception:
            raise FileStoreError('PDF_GEOMETRY_UNAVAILABLE', 'PDF page geometry cannot be read') from None

    @storage_errors
    def save_artifact(self, document_id, artifact, value):
        if artifact not in _ARTIFACTS: raise ValueError
        directory = self._directory(document_id)
        self.read_source(document_id)
        if (directory / 'manifest.json').exists():
            raise FileStoreError('ALREADY_PUBLISHED', 'Published parse artifacts cannot be overwritten')
        path = directory / _ARTIFACTS[artifact]
        self._regular(path)
        self._atomic_write(path, self._encode(value))

    @storage_errors
    def publish(self, document_id, operation_id, configuration, versions):
        source = self.read_source(document_id)
        directory = self._directory(document_id)
        manifest_path = directory / 'manifest.json'
        self._regular(manifest_path)
        if manifest_path.exists():
            raise FileStoreError('ALREADY_PUBLISHED', 'Published parse artifacts cannot be overwritten')
        operation = self.read_operation(operation_id)
        if operation['document_id'] != document_id or operation['configuration'] != configuration or operation['status'] != 'running': raise ValueError
        native = self._json(directory / 'docling.json')
        normalized = self._json(directory / 'normalized.json')
        if normalized['document_id'] != document_id or normalized['schema_version'] != 'normalized-0.1' or normalized['source_kind'] != source.context.source_kind.value or normalized['import_context'] != {'kind': source.context.kind.value, 'bridge_id': source.context.bridge_id}: raise ValueError
        pages = self.page_rotations(document_id)
        if set(map(int, native['pages'])) != set(pages) or {p['page_no'] for p in normalized['pages']} != set(pages): raise ValueError
        artifacts = {}
        for name in ('source.pdf', 'source.json', 'docling.json', 'normalized.json'):
            path = directory / name
            self._regular(path)
            data = path.read_bytes()
            artifacts[name] = {'key': f'documents/{document_id}/{name}', 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        result = {'document_id': document_id, 'source_sha256': source.sha256, 'schema_version': 'normalized-0.1',
                  'operation_id': operation_id, 'configuration': configuration, 'versions': versions,
                  'artifacts': artifacts, 'warnings': normalized['warnings']}
        self._atomic_write(manifest_path, self._encode(result))
        return self.read_published(document_id)

    @storage_errors
    def read_published(self, document_id):
        source = self.read_source(document_id)
        directory = self._directory(document_id)
        path = directory / 'manifest.json'
        self._regular(path)
        if not path.exists(): return None
        manifest = self._json(path)
        if set(manifest) != {'document_id', 'source_sha256', 'schema_version', 'operation_id', 'configuration', 'versions', 'artifacts', 'warnings'}: raise ValueError
        if manifest['document_id'] != document_id or manifest['source_sha256'] != source.sha256 or manifest['schema_version'] != 'normalized-0.1': raise ValueError
        if set(manifest['artifacts']) != {'source.pdf', 'source.json', 'docling.json', 'normalized.json'}: raise ValueError
        operation = self.read_operation(manifest['operation_id'])
        if operation['document_id'] != document_id or operation['configuration'] != manifest['configuration'] or operation['status'] not in ('running', 'succeeded', 'succeeded_with_warnings'): raise ValueError
        for name, entry in manifest['artifacts'].items():
            if entry['key'] != f'documents/{document_id}/{name}': raise ValueError
            artifact_path = directory / name
            self._regular(artifact_path)
            if not artifact_path.exists(): raise _error('ARTIFACT_MISSING')
            data = artifact_path.read_bytes()
            if entry['bytes'] != len(data) or entry['sha256'] != hashlib.sha256(data).hexdigest(): raise ValueError
        return manifest

    @storage_errors
    def read_artifact(self, document_id, artifact):
        if artifact not in _ARTIFACTS: raise ValueError
        if self.read_published(document_id) is None:
            raise FileStoreError('ARTIFACT_NOT_GENERATED', 'Published parse artifacts have not been generated')
        return self._json(self._directory(document_id) / _ARTIFACTS[artifact])

    def _operation_path(self, operation_id):
        if not isinstance(operation_id, str) or str(UUID(operation_id)) != operation_id: raise ValueError
        directory = self._root / 'operations' / operation_id
        for path in (self._root, directory.parent, directory):
            if path.is_symlink() or (path.exists() and not path.is_dir()): raise _error('UNSAFE_STORAGE_PATH')
        path = directory / 'diagnostic.json'
        self._regular(path)
        return path

    @staticmethod
    def _validate_operation(value):
        required = {'operation_id', 'document_id', 'scope', 'status', 'stage', 'error_code', 'created_at', 'updated_at', 'configuration', 'artifact_keys'}
        if not required <= set(value) or set(value) - required - {'ocr'}: raise ValueError
        if 'ocr' in value and not isinstance(value['ocr'], dict): raise ValueError
        if value['scope'] != 'local_parse' or value['status'] not in _STATUSES or value['stage'] not in {'parsing', 'normalizing', 'publishing', 'published'}: raise ValueError
        if value['error_code'] is not None and (not isinstance(value['error_code'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', value['error_code'])): raise ValueError
        if not isinstance(value['configuration'], dict) or not isinstance(value['artifact_keys'], list): raise ValueError

    @storage_errors
    def save_operation_artifact(self, operation_id, artifact, value):
        if artifact not in _ARTIFACTS: raise ValueError
        directory = self._operation_path(operation_id).parent
        self.read_operation(operation_id)
        path = directory / _ARTIFACTS[artifact]
        self._regular(path)
        if path.exists(): raise FileStoreError("ALREADY_PUBLISHED", "Operation snapshots cannot be overwritten")
        self._atomic_write(path, self._encode(value))

    @storage_errors
    def save_operation(self, value):
        self._validate_operation(value)
        self._directory(value['document_id'])
        path = self._operation_path(value['operation_id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, self._encode(value))

    @storage_errors
    def read_operation(self, operation_id):
        value = self._json(self._operation_path(operation_id))
        self._validate_operation(value)
        if value['operation_id'] != operation_id: raise ValueError
        self._directory(value['document_id'])
        return value

    @storage_errors
    def list_operations(self):
        root = self._root / 'operations'
        if root.is_symlink() or (root.exists() and not root.is_dir()): raise _error('UNSAFE_STORAGE_PATH')
        if not root.exists(): return []
        result = []
        for directory in sorted(root.iterdir()):
            if directory.name.startswith('.'): continue
            path = self._operation_path(directory.name)
            # A crash before the first atomic diagnostic write leaves no active record.
            if path.exists(): result.append(self.read_operation(directory.name))
        return sorted(result, key=lambda value: (value['created_at'], value['operation_id']))
