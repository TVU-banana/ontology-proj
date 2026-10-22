"""Pinned OCR assets and lazy local Docling integration. No implicit downloads."""
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

OCR_VERSIONS = {'rapidocr': '3.10.0', 'onnxruntime': '1.23.2'}
OCR_MODELS = {
    'det': ('PP-OCRv6_det_small.onnx', '090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f'),
    'rec': ('PP-OCRv6_rec_small.onnx', '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'),
    'cls': ('ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c'),
}
REVISION = 'ocr-routing-1'


def configuration(mode, force_pages):
    return {'revision': REVISION, 'mode': mode, 'engine': 'rapidocr', 'backend': 'onnxruntime',
            'language': 'ch', 'model_size': 'small', 'force_pages': list(force_pages),
            'versions': dict(OCR_VERSIONS),
            'models': {k: {'file': n, 'sha256': sha} for k, (n, sha) in OCR_MODELS.items()},
            'excluded_labels': ['picture', 'chart'], 'scale': 3.0}


def preflight(root: Path | None):
    if root is None:
        return 'OCR_NOT_CONFIGURED'
    try:
        if any(version(n) != v for n, v in OCR_VERSIONS.items()):
            return 'OCR_VERSION_MISMATCH'
    except PackageNotFoundError:
        return 'OCR_DEPENDENCY_UNAVAILABLE'
    try:
        for name, expected in OCR_MODELS.values():
            path = root / name
            if not path.is_file():
                return 'OCR_MODEL_FILES_UNAVAILABLE'
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    return 'OCR_MODEL_HASH_MISMATCH'
    except OSError:
        return 'OCR_MODEL_FILES_UNAVAILABLE'
    return None


def make_converter(artifacts_path, num_threads, models_path, force_pages):
    """Construct SDK types only after all dependency/model prechecks pass."""
    import time
    from threading import Lock
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, OcrMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    from docling_core.types.doc import BoundingBox, CoordOrigin

    trace = []
    lock = Lock()

    class RoutedOcrModel(RapidOcrModel):
        def get_ocr_rects(self, page):
            if page.page_no in force_pages:
                return [BoundingBox(l=0, t=0, r=page.size.width, b=page.size.height,
                                    coord_origin=CoordOrigin.TOPLEFT)]
            layout = page.predictions.layout
            if layout is None:
                raise RuntimeError('OCR routing requires layout predictions')
            # Work on a shallow page copy; preserve the actual layout for assembly.
            clusters = []
            for cluster in layout.clusters:
                if cluster.label.value in {'picture', 'chart'}:
                    continue
                backend = page._backend
                if backend is not None:
                    text = backend.has_content_in(bbox=cluster.bbox, chars=True, shapes=False, bitmaps=False)
                    bitmap = backend.has_content_in(bbox=cluster.bbox, chars=False, shapes=False, bitmaps=True)
                    # Native tables often contain fills/borders. Those alone must
                    # not rerun OCR over usable text; mixed bitmaps stay eligible.
                    if text is True and bitmap is False:
                        continue
                clusters.append(cluster)
            routed = page.model_copy(update={'predictions': page.predictions.model_copy(
                update={'layout': layout.model_copy(update={'clusters': clusters})})})
            return super().get_ocr_rects(routed)

        def __call__(self, conv_res, page_batch):
            for page in page_batch:
                start = time.perf_counter()
                row = {'page_no': page.page_no, 'regions': [], 'reader_calls': 0,
                       'recognized_cells': 0, 'ocr_cells': 0,
                       'reason': 'no_uncovered_text_regions', 'status': 'skipped'}
                base_rects = self.get_ocr_rects
                base_reader = self.reader
                def rects(p):
                    boxes = base_rects(p)
                    row['regions'] = [[b.l / p.size.width, b.t / p.size.height,
                                       b.r / p.size.width, b.b / p.size.height] for b in boxes]
                    if boxes:
                        row.update(reason='forced_page' if p.page_no in force_pages else 'uncovered_text_regions',
                                   status='selected')
                    return boxes
                def reader(*args, **kwargs):
                    row['reader_calls'] += 1
                    result = base_reader(*args, **kwargs)
                    if result is not None and result.boxes is not None:
                        row['recognized_cells'] += len(result.boxes)
                    return result
                self.get_ocr_rects, self.reader = rects, reader
                # FULL_PAGE's merge policy prefers OCR cells. Mode is restored per page.
                mode = self.options.mode
                self.options.mode = OcrMode.FULL_PAGE if page.page_no in force_pages else OcrMode.PDF_AWARE_LAYOUT_REGIONS
                try:
                    for result in super().__call__(conv_res, [page]):
                        row['ocr_cells'] = sum(c.from_ocr for c in result.cells)
                        if row['reader_calls']:
                            row['status'] = 'recognized' if row['recognized_cells'] else 'empty'
                        yield result
                except Exception:
                    row['status'] = 'failed'
                    raise
                finally:
                    self.get_ocr_rects, self.reader = base_rects, base_reader
                    self.options.mode = mode
                    row['elapsed_seconds'] = round(time.perf_counter() - start, 6)
                    with lock:
                        trace.append(row)

    class RoutedPipeline(StandardPdfPipeline):
        def _make_ocr_model(self, art_path):
            return RoutedOcrModel(enabled=True, artifacts_path=art_path,
                                  options=self.pipeline_options.ocr_options,
                                  accelerator_options=self.pipeline_options.accelerator_options)

    paths = {key: str(models_path / filename) for key, (filename, _) in OCR_MODELS.items()}
    options = PdfPipelineOptions(artifacts_path=artifacts_path, do_ocr=True, do_table_structure=True,
        enable_remote_services=False, allow_external_plugins=False,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=num_threads),
        ocr_options=RapidOcrOptions(backend='onnxruntime', lang=['ch'], model_size='small',
            mode=OcrMode.PDF_AWARE_LAYOUT_REGIONS, det_model_path=paths['det'],
            rec_model_path=paths['rec'], cls_model_path=paths['cls'], use_det=True, use_rec=True, use_cls=True))
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(
        pipeline_cls=RoutedPipeline, pipeline_options=options)})
    converter.initialize_pipeline(InputFormat.PDF)
    # Instance-owned receipt; no changes to SDK globals or plugin registries.
    converter._ontology_ocr_trace = trace
    return converter
