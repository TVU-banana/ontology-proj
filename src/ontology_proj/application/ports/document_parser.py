from typing import Protocol

from ontology_proj.application.parsing import ParseOutcome


class DocumentParser(Protocol):
    def configuration(self) -> dict: ...
    def parse(self, source_path: str, document_id: str) -> ParseOutcome: ...
