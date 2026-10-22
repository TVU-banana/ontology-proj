import hashlib
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import logging
import sys
from types import SimpleNamespace

import pytest

import ontology_proj.adapters.docling_parser as adapter
from ontology_proj.adapters.docling_parser import DoclingParser, EXPECTED_VERSIONS, REQUIRED_MODEL_FILES
from ontology_proj.application.dto import CapabilityState
from ontology_proj.application.parsing import ParseStatus
from ontology_proj.bootstrap import build_parser
from ontology_proj.settings import Settings


@pytest.fixture
def local_models(tmp_path, monkeypatch):
    # Explicit dummy files for adapter policy tests, not actual model evidence.
    root = tmp_path / "models"
    for name in REQUIRED_MODEL_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"dummy")
    monkeypatch.setattr(DoclingParser, "installed_versions", lambda self: dict(EXPECTED_VERSIONS))
    return root


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.7\nadapter fixture (no actual conversion)")
    return str(path), "doc_" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_config_does_not_import_or_initialize_sdk(monkeypatch):
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: pytest.fail("must not initialize"))
    parser = DoclingParser(None, ocr_mode="off")
    assert parser.check().state == CapabilityState.NOT_CONFIGURED
    assert parser.initialize().code == "PARSER_NOT_CONFIGURED"


def test_missing_models_fail_before_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: pytest.fail("must not initialize"))
    assert DoclingParser(tmp_path, ocr_mode="off").initialize().code == "MODEL_FILES_UNAVAILABLE"


@pytest.mark.parametrize("failure", ["missing", "wrong"])
def test_dependency_preflight(local_models, monkeypatch, failure):
    def versions(self):
        if failure == "missing": raise PackageNotFoundError("private-name")
        return {**EXPECTED_VERSIONS, "torch": "unexpected"}
    monkeypatch.setattr(DoclingParser, "installed_versions", versions)
    expected = "PARSER_DEPENDENCY_UNAVAILABLE" if failure == "missing" else "PARSER_VERSION_MISMATCH"
    assert DoclingParser(local_models, ocr_mode="off").initialize().code == expected


def test_initialization_failure_is_safe_and_can_be_retried(local_models, monkeypatch):
    def fail(*args): raise RuntimeError("private path secret content")
    monkeypatch.setattr(adapter, "_make_converter", fail)
    parser = DoclingParser(local_models, ocr_mode="off")
    result = parser.initialize()
    assert result.code == "PARSER_INITIALIZATION_FAILED"
    assert "secret" not in result.message
    assert parser.check().state == CapabilityState.UNAVAILABLE
    fake = object()
    monkeypatch.setattr(adapter, "_make_converter", lambda *args: fake)
    assert parser.initialize() is None
    assert parser.check().state == CapabilityState.READY


@pytest.mark.parametrize('fail_initialization', [False, True])
def test_sdk_output_is_suppressed_and_logging_restored(local_models, source, monkeypatch, capsys, fail_initialization):
    previous = logging.root.manager.disable
    def noise():
        print('PRIVATE-SDK-PATH')
        print('PRIVATE-SDK-PATH', file=sys.stderr)
        logging.warning('PRIVATE-SDK-PATH')
    def convert(*args, **kwargs):
        noise()
        raise RuntimeError('PRIVATE-SDK-PATH')
    def factory(*args):
        noise()
        if fail_initialization:
            raise RuntimeError('PRIVATE-SDK-PATH')
        return SimpleNamespace(convert=convert)
    monkeypatch.setattr(adapter, '_make_converter', factory)
    result = DoclingParser(local_models, ocr_mode="off").parse(*source)
    assert result.status == ParseStatus.FAILED
    assert logging.root.manager.disable == previous
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


def test_readiness_never_initializes_models(local_models, monkeypatch):
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: pytest.fail("readiness must stay lightweight"))
    parser = build_parser(Settings(docling_artifacts_path=local_models, docling_ocr_mode="off"))
    assert parser.check().error_code == "PARSER_NOT_INITIALIZED"


@pytest.mark.parametrize("status,errors,expected,code", [
    ("success", [], ParseStatus.SUCCESS, None),
    ("partial_success", [], ParseStatus.PARTIAL, "PARSER_PARTIAL_RESULT"),
    ("success", ["private SDK detail"], ParseStatus.PARTIAL, "PARSER_REPORTED_ERRORS"),
    ("failure", [], ParseStatus.FAILED, "PARSER_CONVERSION_FAILED"),
    ("skipped", [], ParseStatus.FAILED, "PARSER_CONVERSION_FAILED"),
])
def test_conversion_status_and_plain_result(local_models, source, monkeypatch, status, errors, expected, code):
    native = {"pages": {"1": {"size": {"width": 1, "height": 2}}}, "texts": [{"text": "未见明显病害"}]}
    class Converter:
        def convert(self, path, *, raises_on_error):
            assert isinstance(path, Path) and raises_on_error is False
            return SimpleNamespace(status=SimpleNamespace(value=status), errors=errors,
                                   document=SimpleNamespace(export_to_dict=lambda: native))
    calls = []
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: calls.append(a) or Converter())
    parser = DoclingParser(local_models, ocr_mode="off")
    result = parser.parse(*source)
    assert result.status == expected
    assert result.native_document == (native if expected != ParseStatus.FAILED else None)
    assert result.versions == EXPECTED_VERSIONS
    assert [d.code for d in result.diagnostics] == ([code] if code else [])
    assert "private SDK detail" not in repr(result)
    parser.parse(*source)
    assert len(calls) == 1


def test_exception_and_empty_result_do_not_publish_success(local_models, source, monkeypatch):
    class Converter:
        def convert(self, *a, **kw): raise RuntimeError("secret local path")
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: Converter())
    result = DoclingParser(local_models, ocr_mode="off").parse(*source)
    assert result.diagnostics[0].code == "PARSER_CONVERSION_FAILED"
    assert "secret" not in repr(result)
    class EmptyConverter:
        def convert(self, *a, **kw):
            return SimpleNamespace(status=SimpleNamespace(value="success"), errors=[],
                                   document=SimpleNamespace(export_to_dict=lambda: {"pages": {}}))
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: EmptyConverter())
    assert DoclingParser(local_models, ocr_mode="off").parse(*source).diagnostics[0].code == "PARSER_EMPTY_RESULT"


@pytest.mark.parametrize("input_kind,code", [("url", "INVALID_PARSER_INPUT"), ("hash", "ARTIFACT_MISMATCH"), ("missing", "SOURCE_UNAVAILABLE"), ("nul", "INVALID_PARSER_INPUT")])
def test_bad_input_rejected_before_sdk(source, monkeypatch, input_kind, code):
    monkeypatch.setattr(adapter, "_make_converter", lambda *a: pytest.fail("bad source must not initialize"))
    path, identity = source
    if input_kind == "url": path = "https://example.invalid/source.pdf"
    if input_kind == "hash": identity = "doc_" + "a" * 64
    if input_kind == "missing": path = str(Path(path).with_name("missing.pdf"))
    if input_kind == "nul": path = "/private/\x00.pdf"
    result = DoclingParser(None, ocr_mode="off").parse(path, identity)
    assert result.status == ParseStatus.FAILED and result.diagnostics[0].code == code


def test_explicit_configuration_and_bounds(tmp_path):
    settings = Settings(docling_artifacts_path=tmp_path, docling_num_threads=4)
    assert settings.docling_artifacts_path == tmp_path.resolve()
    assert Settings(docling_artifacts_path="").docling_artifacts_path is None
    with pytest.raises(ValueError): DoclingParser(tmp_path, 0)
    with pytest.raises(ValueError): Settings(docling_num_threads=0)


def test_success_collects_real_pdf_layout_without_changing_native(local_models, tmp_path, monkeypatch):
    # Docling conversion is a double; PDF geometry is actually read from fixture.
    import json
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / 'source.pdf'
    path.write_bytes((root / 'samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf').read_bytes())
    native = json.loads((root / 'experiments/docling-parse-001/results/docling.json').read_text())
    fake = SimpleNamespace(convert=lambda *a, **kw: SimpleNamespace(status=SimpleNamespace(value='success'),
        errors=[], document=SimpleNamespace(export_to_dict=lambda: native)))
    monkeypatch.setattr(adapter, '_make_converter', lambda *a: fake)
    result = DoclingParser(local_models, ocr_mode="off").parse(str(path), 'doc_' + hashlib.sha256(path.read_bytes()).hexdigest())
    assert result.status == ParseStatus.SUCCESS and result.native_document == native
    assert len(result.pdf_layout['pages']) == 2
    assert result.pdf_layout['revision'] == 'pdf-layout-1'
    assert not result.pdf_layout['warnings']
