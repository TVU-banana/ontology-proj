"""Local source drafts, never a graph registry or a processing state store.

Single process; serialize calls on this adapter. Reject symlinks under DATA_ROOT.
The operator owns DATA_ROOT: adversarial concurrent filesystem mutation is outside
this MVP's scope. source.json is written last; incomplete pairs are not reused.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import shutil
from threading import RLock

from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError, SavedSource, SourceDraft
from ontology_proj.domain.source import DocumentKind, SourceKind


_DOCUMENT_ID = re.compile(r"doc_[0-9a-f]{64}\Z")
_FIELDS = {"document_id", "sha256", "original_filename", "bytes", "kind", "source_kind", "bridge_id", "created_at", "pdf_key"}


def _error(code: str) -> FileStoreError:
    messages = {
        "INVALID_SOURCE_INPUT": "Source bytes, filename or context are invalid",
        "INVALID_DOCUMENT_ID": "Document identity is invalid",
        "UNSAFE_STORAGE_PATH": "Storage path contains an unsupported link or entry",
        "SOURCE_NOT_FOUND": "Source draft does not exist",
        "SOURCE_CONTEXT_CONFLICT": "Existing source context differs from this submission",
        "ARTIFACT_MISSING": "Source artifacts are incomplete",
        "ARTIFACT_MISMATCH": "Source artifacts do not match their metadata",
        "FILE_STORE_UNAVAILABLE": "Local source storage operation failed",
    }
    return FileStoreError(code, messages[code], retryable=code == "FILE_STORE_UNAVAILABLE")


class LocalFileStore:
    def __init__(self, data_root: Path):
        self._root = data_root.expanduser().resolve()
        self._lock = RLock()

    def _directory(self, document_id: str) -> Path:
        if not isinstance(document_id, str) or not _DOCUMENT_ID.fullmatch(document_id):
            raise _error("INVALID_DOCUMENT_ID")
        for path in (self._root, self._root / "documents", self._root / "documents" / document_id):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise _error("UNSAFE_STORAGE_PATH")
        return self._root / "documents" / document_id

    @staticmethod
    def _regular(path: Path) -> None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise _error("UNSAFE_STORAGE_PATH")

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".source-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_source(self, data: bytes, original_filename: str, context: ImportContext) -> SavedSource:
        if not isinstance(data, bytes) or not data or not isinstance(original_filename, str) or not original_filename.strip() or "\x00" in original_filename or not isinstance(context, ImportContext):
            raise _error("INVALID_SOURCE_INPUT")
        digest = hashlib.sha256(data).hexdigest()
        document_id = "doc_" + digest
        with self._lock:
            try:
                directory = self._directory(document_id)
                pdf, metadata = directory / "source.pdf", directory / "source.json"
                self._regular(pdf)
                self._regular(metadata)
                if directory.exists():
                    existing = self._read_source(document_id)
                    if existing.context != context:
                        raise _error("SOURCE_CONTEXT_CONFLICT")
                    return SavedSource(existing, duplicate=True)
                source = SourceDraft(document_id, digest, original_filename, len(data), context,
                                     datetime.now(timezone.utc).isoformat(), f"documents/{document_id}/source.pdf")
                payload = asdict(source)
                payload.pop("context")
                payload["bytes"] = payload.pop("bytes_count")
                payload.update(kind=context.kind.value, source_kind=context.source_kind.value, bridge_id=context.bridge_id)
                # Publish the source pair as one directory on this filesystem.
                # A killed writer may leave staging, but never a final half-pair.
                directory.parent.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(dir=directory.parent, prefix=".source-publish-"))
                try:
                    self._atomic_write(staging / 'source.pdf', data)
                    self._atomic_write(staging / 'source.json', (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                    self._sync_directory(staging)
                    os.rename(staging, directory)
                    self._sync_directory(directory.parent)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
                return SavedSource(source, duplicate=False)
            except OSError:
                raise _error("FILE_STORE_UNAVAILABLE") from None

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _read_source(self, document_id: str) -> SourceDraft:
        directory = self._directory(document_id)
        if not directory.exists():
            raise _error("SOURCE_NOT_FOUND")
        pdf, metadata = directory / "source.pdf", directory / "source.json"
        self._regular(pdf)
        self._regular(metadata)
        if not pdf.exists() or not metadata.exists():
            raise _error("ARTIFACT_MISSING")
        try:
            payload = json.loads(metadata.read_bytes())
            if not isinstance(payload, dict) or set(payload) != _FIELDS:
                raise ValueError
            digest = document_id[4:]
            expected_key = f"documents/{document_id}/source.pdf"
            if payload["document_id"] != document_id or payload["sha256"] != digest or payload["pdf_key"] != expected_key:
                raise ValueError
            if type(payload["bytes"]) is not int or payload["bytes"] <= 0:
                raise ValueError
            if not isinstance(payload["original_filename"], str) or not payload["original_filename"].strip() or "\x00" in payload["original_filename"]:
                raise ValueError
            timestamp = datetime.fromisoformat(payload["created_at"])
            if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
                raise ValueError
            context = ImportContext(DocumentKind(payload["kind"]), SourceKind(payload["source_kind"]), payload["bridge_id"])
            data = pdf.read_bytes()
            if len(data) != payload["bytes"] or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError
            return SourceDraft(document_id, digest, payload["original_filename"], len(data), context, payload["created_at"], expected_key)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise _error("ARTIFACT_MISMATCH") from None

    def read_source(self, document_id: str) -> SourceDraft:
        with self._lock:
            try:
                return self._read_source(document_id)
            except OSError:
                raise _error("FILE_STORE_UNAVAILABLE") from None

    def read_pdf(self, document_id: str) -> bytes:
        with self._lock:
            try:
                source = self._read_source(document_id)
                data = (self._directory(document_id) / "source.pdf").read_bytes()
                if len(data) != source.bytes_count or hashlib.sha256(data).hexdigest() != source.sha256:
                    raise _error("ARTIFACT_MISMATCH")
                return data
            except OSError:
                raise _error("FILE_STORE_UNAVAILABLE") from None
