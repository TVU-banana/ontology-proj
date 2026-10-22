"""Read-only boundaries; no driver objects leave adapters."""
from typing import Protocol


class QueryRepository(Protocol):
    def lookup(self, request: dict) -> list[dict]: ...
    def evidence(self, evidence_id: str) -> dict: ...


class EvidenceReader(Protocol):
    def read(self, evidence_id: str) -> dict: ...
