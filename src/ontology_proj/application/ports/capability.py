from typing import Protocol

from ontology_proj.application.dto import CapabilityStatus


class CapabilityProbe(Protocol):
    def check(self) -> CapabilityStatus: ...
