"""Offline-only, lazy Docling adapter. No experiment paths or automatic downloads."""
import hashlib
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
from importlib.metadata import PackageNotFoundError, version
import logging
from pathlib import Path
import re
from threading import RLock
import time

from ontology_proj.application.dto import CapabilityState, CapabilityStatus
from ontology_proj.application.parsing import ParseOutcome, ParseStatus, ParserDiagnostic
from ontology_proj.adapters import ocr_routing


EXPECTED_VERSIONS = {
    "docling": "2.137.0", "docling-core": "2.101.1", "docling-parse": "7.22.2",
    "docling-ibm-models": "4.0.3", "torch": "2.14.1", "transformers": "5.19.0",
}
REQUIRED_MODEL_FILES = (
    "docling-project--docling-layout-heron/config.json",
    "docling-project--docling-layout-heron/preprocessor_config.json",
    "docling-project--docling-layout-heron/model.safetensors",
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json",
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors",
)
_MESSAGES = {
    "PARSER_NOT_CONFIGURED": "Explicit local model artifacts are required",
    "PARSER_DEPENDENCY_UNAVAILABLE": "Locked parser dependencies are unavailable",
    "PARSER_VERSION_MISMATCH": "Installed parser versions differ from the supported lock",
    "MODEL_FILES_UNAVAILABLE": "Required local parser model files are missing or unreadable",
    "PARSER_NOT_INITIALIZED": "Local parser models have not been initialized",
    "PARSER_INITIALIZATION_FAILED": "Local parser model initialization failed",
    "INVALID_PARSER_INPUT": "Local PDF path or document identity is invalid",
    "SOURCE_UNAVAILABLE": "Local source PDF is unavailable",
    "ARTIFACT_MISMATCH": "Source PDF does not match its document identity",
    "PARSER_CONVERSION_FAILED": "PDF conversion failed",
    "PARSER_PARTIAL_RESULT": "PDF conversion returned only a partial document",
    "PARSER_REPORTED_ERRORS": "Parser reported errors; inspect the retained native result",
    "PARSER_EMPTY_RESULT": "Parser returned no document pages",
    "OCR_NOT_CONFIGURED": "Explicit local OCR model directory is required",
    "OCR_DEPENDENCY_UNAVAILABLE": "Locked OCR dependencies are unavailable",
    "OCR_VERSION_MISMATCH": "Installed OCR dependencies differ from the supported lock",
    "OCR_MODEL_FILES_UNAVAILABLE": "Required local OCR model files are unavailable",
    "OCR_MODEL_HASH_MISMATCH": "OCR model file hash differs from the fixed model manifest",
    "OCR_FORCE_PAGE_OUT_OF_RANGE": "Forced OCR physical page is outside the PDF",
    "OCR_EMPTY_RESULT": "Selected OCR regions returned no usable OCR text on a page",
    "OCR_ROUTING_FAILED": "OCR routing or inference failed; inspect the retained operation",
}


def _diagnostic(code: str) -> ParserDiagnostic:
    return ParserDiagnostic(code, _MESSAGES[code])


@contextmanager
def _quiet_sdk():
    """Single-process parser scope: SDK progress/errors must not leak paths.

    Logging state is restored even on failure. Raw SDK output is discarded;
    structured status/native results remain the diagnostic boundary.
    """
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


def _make_converter(artifacts_path: Path, num_threads: int):
    # Heavy SDK imports happen only after explicit local artifact preflight.
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(
        artifacts_path=artifacts_path, do_ocr=False, do_table_structure=True,
        enable_remote_services=False, allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=num_threads),
    )
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


class DoclingParser:
    def __init__(self, artifacts_path: Path | None, num_threads: int = 4, *,
                 ocr_mode: str = "auto", ocr_models_path: Path | None = None, ocr_force_pages=()):
        if type(num_threads) is not int or not 1 <= num_threads <= 64:
            raise ValueError("Parser thread count must be between 1 and 64")
        self._artifacts = artifacts_path.expanduser().resolve() if artifacts_path is not None else None
        self._threads = num_threads
        if ocr_mode not in {'auto', 'off'} or not isinstance(ocr_force_pages, (tuple, list)) or any(
            type(p) is not int or p < 1 for p in ocr_force_pages
        ) or len(set(ocr_force_pages)) != len(ocr_force_pages) or (ocr_mode == 'off' and ocr_force_pages):
            raise ValueError('Invalid OCR mode or forced physical page list')
        self._ocr_mode = ocr_mode
        self._ocr_models = ocr_models_path.expanduser().resolve() if ocr_models_path is not None else None
        self._force_pages = tuple(sorted(ocr_force_pages))
        self._converter = None
        self._lock = RLock()

    def configuration(self) -> dict:
        return {"device": "cpu", "num_threads": self._threads, "do_ocr": self._ocr_mode != 'off', "do_table_structure": True,
                "enable_remote_services": False, "allow_external_plugins": False, "versions": dict(EXPECTED_VERSIONS),
                "pdf_layout_revision": "pdf-layout-1", "structure_revision": "document-structure-1",
                "ocr": ocr_routing.configuration(self._ocr_mode, self._force_pages)}

    def installed_versions(self) -> dict[str, str]:
        return {name: version(name) for name in EXPECTED_VERSIONS}

    def _preflight(self) -> str | None:
        if self._artifacts is None:
            return "PARSER_NOT_CONFIGURED"
        try:
            if self.installed_versions() != EXPECTED_VERSIONS:
                return "PARSER_VERSION_MISMATCH"
        except PackageNotFoundError:
            return "PARSER_DEPENDENCY_UNAVAILABLE"
        try:
            for key in REQUIRED_MODEL_FILES:
                path = self._artifacts / key
                if not path.is_file() or path.stat().st_size == 0:
                    return "MODEL_FILES_UNAVAILABLE"
                with path.open("rb") as stream:
                    stream.read(1)
        except OSError:
            return "MODEL_FILES_UNAVAILABLE"
        return ocr_routing.preflight(self._ocr_models) if self._ocr_mode != 'off' else None

    def check(self) -> CapabilityStatus:
        # Readiness does not load models or trigger conversion/downloads.
        with self._lock:
            code = self._preflight()
            if code is None and self._converter is None:
                code = "PARSER_NOT_INITIALIZED"
            if code:
                state = CapabilityState.NOT_CONFIGURED if code == "PARSER_NOT_CONFIGURED" else CapabilityState.UNAVAILABLE
                return CapabilityStatus(state, self._artifacts is not None, code)
            return CapabilityStatus(CapabilityState.READY, True)

    def initialize(self) -> ParserDiagnostic | None:
        with self._lock:
            code = self._preflight()
            if code:
                return _diagnostic(code)
            if self._converter is None:
                try:
                    with _quiet_sdk():
                        self._converter = (_make_converter(self._artifacts, self._threads) if self._ocr_mode == 'off'
                            else ocr_routing.make_converter(self._artifacts, self._threads, self._ocr_models, self._force_pages))
                except Exception:
                    # SDK exceptions may contain private paths/raw PDF content.
                    return _diagnostic("PARSER_INITIALIZATION_FAILED")
            return None

    def parse(self, source_path: str, document_id: str) -> ParseOutcome:
        start = time.perf_counter()
        versions = {}
        ocr = None
        def outcome(status, native=None, codes=(), layout=None):
            return ParseOutcome(document_id, status, native, tuple(_diagnostic(c) for c in codes), versions,
                                round(time.perf_counter() - start, 6), layout, ocr)
        if not isinstance(source_path, str) or "\x00" in source_path or not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{64}", document_id):
            return outcome(ParseStatus.FAILED, codes=("INVALID_PARSER_INPUT",))
        path = Path(source_path)
        if not path.is_absolute() or path.suffix.lower() != ".pdf":
            return outcome(ParseStatus.FAILED, codes=("INVALID_PARSER_INPUT",))
        with self._lock:
            try:
                if not path.is_file():
                    return outcome(ParseStatus.FAILED, codes=("SOURCE_UNAVAILABLE",))
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != document_id[4:]:
                    return outcome(ParseStatus.FAILED, codes=("ARTIFACT_MISMATCH",))
            except OSError:
                return outcome(ParseStatus.FAILED, codes=("SOURCE_UNAVAILABLE",))
            if self._force_pages:
                from pypdf import PdfReader
                try:
                    if max(self._force_pages) > len(PdfReader(path).pages):
                        return outcome(ParseStatus.FAILED, codes=('OCR_FORCE_PAGE_OUT_OF_RANGE',))
                except Exception:
                    return outcome(ParseStatus.FAILED, codes=('PARSER_CONVERSION_FAILED',))
            diagnostic = self.initialize()
            if diagnostic:
                return outcome(ParseStatus.FAILED, codes=(diagnostic.code,))
            versions = self.installed_versions()
            if self._ocr_mode != 'off':
                versions = {**versions, **ocr_routing.OCR_VERSIONS}
            trace = getattr(self._converter, '_ontology_ocr_trace', None)
            if trace is not None:
                trace.clear()
            ocr = {**ocr_routing.configuration(self._ocr_mode, self._force_pages), 'pages': []}
            def collect_ocr():
                if trace is not None:
                    import copy
                    ocr['pages'] = sorted(copy.deepcopy(trace), key=lambda row: row['page_no'])
            try:
                with _quiet_sdk():
                    converted = self._converter.convert(path, raises_on_error=False)
                    status = converted.status.value
                    native = converted.document.export_to_dict() if status in {"success", "partial_success"} else None
                collect_ocr()
                if native is not None and self._ocr_mode == 'off':
                    ocr['pages'] = [{'page_no': int(key), 'regions': [], 'reader_calls': 0,
                                     'recognized_cells': 0, 'ocr_cells': 0, 'reason': 'disabled',
                                     'status': 'skipped', 'elapsed_seconds': 0.0}
                                    for key in sorted(native.get('pages', {}), key=int)]
                if native is not None and not native.get("pages"):
                    return outcome(ParseStatus.FAILED, codes=("PARSER_EMPTY_RESULT",))
                if status == "partial_success":
                    return outcome(ParseStatus.PARTIAL, native, ("PARSER_PARTIAL_RESULT",))
                if status != "success":
                    return outcome(ParseStatus.FAILED, codes=("PARSER_CONVERSION_FAILED",))
                if converted.errors:
                    return outcome(ParseStatus.PARTIAL, native, ("PARSER_REPORTED_ERRORS",))
                if any(p['status'] == 'failed' for p in ocr['pages']):
                    return outcome(ParseStatus.PARTIAL, native, ('OCR_ROUTING_FAILED',))
                if any(p['status'] == 'empty' for p in ocr['pages']):
                    return outcome(ParseStatus.PARTIAL, native, ('OCR_EMPTY_RESULT',))
                from ontology_proj.adapters.pdf_layout import read_pdf_layout
                try:
                    with _quiet_sdk():
                        layout = read_pdf_layout(path)
                except Exception:
                    # Plain safe diagnostic; native result survives a supplemental failure.
                    layout = {'revision': 'pdf-layout-1', 'pages': [], 'images': [], 'lines': [],
                              'warnings': [{'code': 'PDF_LAYOUT_UNAVAILABLE'}]}
                return outcome(ParseStatus.SUCCESS, native, layout=layout)
            except Exception:
                collect_ocr()
                return outcome(ParseStatus.FAILED, codes=("PARSER_CONVERSION_FAILED",))
