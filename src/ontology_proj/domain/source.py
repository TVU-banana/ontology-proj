from enum import StrEnum


class SourceKind(StrEnum):
    REAL = "real"
    SYNTHETIC = "synthetic"


class DocumentKind(StrEnum):
    INSPECTION_REPORT = "inspection_report"
    OTHER = "other"
