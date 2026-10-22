"""Plain file DTOs and sanitized errors; no filesystem or parser dependency."""
from dataclasses import dataclass

from ontology_proj.application.dto import ImportContext


class FileStoreError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class SourceDraft:
    document_id: str
    sha256: str
    original_filename: str
    bytes_count: int
    context: ImportContext
    created_at: str
    pdf_key: str


@dataclass(frozen=True)
class SavedSource:
    source: SourceDraft
    duplicate: bool
