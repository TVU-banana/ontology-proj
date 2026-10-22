from typing import Protocol

from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import SavedSource, SourceDraft


class FileStore(Protocol):
    def save_source(self, data: bytes, original_filename: str, context: ImportContext) -> SavedSource: ...

    def read_source(self, document_id: str) -> SourceDraft: ...

    def read_pdf(self, document_id: str) -> bytes: ...
