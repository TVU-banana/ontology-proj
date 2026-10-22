from typing import Protocol

from ontology_proj.application.dto import ImportContext


class DocumentNormalizer(Protocol):
    def convert(self, native_document: dict, document_id: str, context: ImportContext,
                page_rotations: dict[int, int], *, pdf_layout: dict | None = None, ocr: dict | None = None) -> dict: ...
