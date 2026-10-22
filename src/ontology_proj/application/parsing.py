"""Parser contract: plain data, no SDK types or filesystem access."""
from dataclasses import dataclass
from enum import StrEnum


class ParseStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class ParserDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class ParseOutcome:
    document_id: str
    status: ParseStatus
    native_document: dict | None
    diagnostics: tuple[ParserDiagnostic, ...]
    versions: dict[str, str]
    elapsed_seconds: float
    pdf_layout: dict | None = None
    ocr: dict | None = None
