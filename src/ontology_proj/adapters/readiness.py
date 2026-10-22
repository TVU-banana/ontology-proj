from pathlib import Path
from tempfile import TemporaryFile

from ontology_proj.application.dto import CapabilityState, CapabilityStatus


class DataDirectoryProbe:
    def __init__(self, path: Path):
        self._path = path

    def check(self) -> CapabilityStatus:
        try:
            self._path.mkdir(parents=True, exist_ok=True)
            with TemporaryFile(dir=self._path) as probe:
                probe.write(b"ontology-readiness")
                probe.flush()
                probe.seek(0)
                if probe.read() != b"ontology-readiness":
                    raise OSError("Readback failed")
        except OSError:
            return CapabilityStatus(CapabilityState.UNAVAILABLE, True, "DATA_ROOT_UNAVAILABLE")
        return CapabilityStatus(CapabilityState.READY, True)


class UnimplementedProbe:
    """Reports configuration presence, never pretends to connect or execute."""

    def __init__(self, configured: bool):
        self._configured = configured

    def check(self) -> CapabilityStatus:
        state = CapabilityState.NOT_IMPLEMENTED if self._configured else CapabilityState.NOT_CONFIGURED
        return CapabilityStatus(state, self._configured, state.value.upper())
