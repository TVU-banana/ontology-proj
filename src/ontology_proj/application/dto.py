from dataclasses import dataclass
from enum import StrEnum

from ontology_proj.domain.source import DocumentKind, SourceKind


@dataclass(frozen=True)
class ImportContext:
    kind: DocumentKind
    source_kind: SourceKind
    bridge_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DocumentKind) or not isinstance(self.source_kind, SourceKind):
            raise ValueError("Import context requires validated source types")
        if self.bridge_id is not None and (
            not isinstance(self.bridge_id, str) or not self.bridge_id.strip()
        ):
            raise ValueError("Bridge identity must be nonempty or unknown")


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    EXTRACTING = "extracting"
    GRAPH_READY = "graph_ready"
    GRAPH_READY_WITH_WARNINGS = "graph_ready_with_warnings"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class IndexStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"


class CapabilityState(StrEnum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    NOT_IMPLEMENTED = "not_implemented"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CapabilityStatus:
    state: CapabilityState
    configured: bool
    error_code: str | None = None


@dataclass(frozen=True)
class CapabilityResult:
    name: str
    required: bool
    status: CapabilityStatus


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    capabilities: tuple[CapabilityResult, ...]


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False
