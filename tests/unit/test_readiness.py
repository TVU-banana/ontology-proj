import pytest

from ontology_proj.adapters.readiness import DataDirectoryProbe
from ontology_proj.application.dto import CapabilityState, CapabilityStatus, ImportContext
from ontology_proj.application.readiness import CapabilityCheck, CheckReadiness
from ontology_proj.domain.source import DocumentKind, SourceKind


class Probe:
    def __init__(self, state):
        self.state = state

    def check(self):
        return CapabilityStatus(self.state, True)


def test_optional_model_failure_does_not_hide_available_base():
    graph = Probe(CapabilityState.READY)
    use_case = CheckReadiness((
        CapabilityCheck("graph", True, graph),
        CapabilityCheck("model", False, Probe(CapabilityState.UNAVAILABLE)),
    ))
    assert use_case.execute().ready
    graph.state = CapabilityState.UNAVAILABLE
    assert not use_case.execute().ready


def test_directory_probe_reads_writes_and_leaves_no_probe_files(tmp_path):
    path = tmp_path / "new" / "data"
    assert DataDirectoryProbe(path).check().state == CapabilityState.READY
    assert list(path.iterdir()) == []


def test_file_in_place_of_directory_returns_safe_failure(tmp_path):
    path = tmp_path / "private-name"
    path.write_text("keep-existing-content")
    result = DataDirectoryProbe(path).check()
    assert result.state == CapabilityState.UNAVAILABLE
    assert result.error_code == "DATA_ROOT_UNAVAILABLE"
    assert "private-name" not in repr(result)
    assert path.read_text() == "keep-existing-content"


def test_source_context_keeps_unknown_identity_and_synthetic_kind():
    context = ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC)
    assert context.bridge_id is None
    with pytest.raises(ValueError):
        ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, " ")
    with pytest.raises(ValueError):
        ImportContext("inspection_report", "synthetic")
