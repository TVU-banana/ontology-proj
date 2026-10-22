"""One real Docling conversion; no golden facts or business extraction input."""
import hashlib
import importlib.metadata
import json
import platform
import time
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INPUT = ROOT / "samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf"
OUT = HERE / "results"


def main():
    OUT.mkdir(exist_ok=True)
    options = PdfPipelineOptions()
    options.do_ocr = False  # selectable-text fixture; no scan/OCR claim
    options.do_table_structure = True
    options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=4)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    start = time.perf_counter()
    result = converter.convert(INPUT)
    elapsed = time.perf_counter() - start
    result.document.save_as_json(OUT / "docling.json")
    (OUT / "docling.md").write_text(result.document.export_to_markdown(), encoding="utf-8")
    info = {
        "input": str(INPUT.relative_to(ROOT)),
        "input_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "source_kind": "synthetic", "status": str(result.status),
        "elapsed_seconds": elapsed, "python": platform.python_version(), "platform": platform.platform(),
        "versions": {name: importlib.metadata.version(name) for name in ["docling", "docling-core", "docling-parse", "docling-ibm-models", "torch", "transformers"]},
        "pipeline_options": options.model_dump(mode="json"),
        "errors": [str(e) for e in result.errors],
        "note": "No golden/model business extractor/Neo4j; source code unmodified. Timing includes pipeline initialization, not installation.",
    }
    (OUT / "run.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: info[k] for k in ["status", "elapsed_seconds", "versions", "errors"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
