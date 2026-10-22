from dataclasses import dataclass

from ontology_proj.application.dto import CapabilityResult, CapabilityState, ReadinessReport
from ontology_proj.application.ports.capability import CapabilityProbe


@dataclass(frozen=True)
class CapabilityCheck:
    name: str
    required: bool
    probe: CapabilityProbe


class CheckReadiness:
    def __init__(self, checks: tuple[CapabilityCheck, ...]):
        if not checks or not any(c.required for c in checks):
            raise ValueError("Readiness requires at least one base capability")
        if len({c.name for c in checks}) != len(checks):
            raise ValueError("Capability names must be unique")
        self._checks = checks

    def execute(self) -> ReadinessReport:
        results = tuple(CapabilityResult(c.name, c.required, c.probe.check()) for c in self._checks)
        ready = all(r.status.state == CapabilityState.READY for r in results if r.required)
        return ReadinessReport(ready, results)
