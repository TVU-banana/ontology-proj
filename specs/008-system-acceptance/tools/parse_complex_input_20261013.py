"""One-shot local-only PDF conversion. No providers, HTTP app, graph or retries."""
import hashlib
import json
import os
from pathlib import Path
import socket
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "specs/008-system-acceptance/results/trial-complex-input-20261013-01"
DATA = ROOT / "data/trial-complex-parse-20261013-01"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    # Claim the conversion once, even when it fails or is interrupted.
    with (DATA / "parsed/attempt-started.json").open("x") as stream:
        json.dump({"started": True, "auto_retry": False}, stream)
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[key] = "1"
    os.environ["HF_HOME"] = str(DATA / "cache/huggingface")
    os.environ["TORCH_HOME"] = str(DATA / "cache/torch")
    network_attempts = []

    def deny(operation):
        def blocked(*args, **kwargs):
            # Never log destination/payloads, tokens or arbitrary SDK arguments.
            network_attempts.append(operation)
            raise RuntimeError("Local PDF run forbids network and downloads")
        return blocked

    socket.socket.connect = deny("socket.connect")
    socket.socket.connect_ex = deny("socket.connect_ex")
    socket.create_connection = deny("socket.create_connection")
    socket.getaddrinfo = deny("socket.getaddrinfo")
    import huggingface_hub
    import huggingface_hub.file_download
    import huggingface_hub._snapshot_download
    huggingface_hub.hf_hub_download = deny("hf_hub_download")
    huggingface_hub.snapshot_download = deny("snapshot_download")
    huggingface_hub.file_download.hf_hub_download = deny("hf_hub_download_module")
    huggingface_hub._snapshot_download.snapshot_download = deny("snapshot_download_module")

    from ontology_proj.adapters.docling_parser import DoclingParser
    from ontology_proj.adapters.document_normalizer import DoclingNormalizer
    from ontology_proj.application.dto import ImportContext
    from ontology_proj.domain.source import DocumentKind, SourceKind

    pdf = DATA / "input/shengli-original.pdf"
    with pdf.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = json.loads((OUT / "authorization.json").read_text())["input_sha256"]
    assert digest == expected
    parser = DoclingParser(ROOT / "data/s23-runtime/models", 4)
    started = time.monotonic()
    outcome = parser.parse(str(pdf), "doc_" + digest)
    result = {
        "document_id": outcome.document_id,
        "status": outcome.status.value,
        "elapsed_seconds": outcome.elapsed_seconds,
        "wall_seconds": round(time.monotonic() - started, 6),
        "diagnostics": [{"code": d.code, "message": d.message} for d in outcome.diagnostics],
        "configuration": parser.configuration(),
        "versions": outcome.versions,
        "network_or_download_attempts": network_attempts,
        "generation_calls": 0, "embedding_calls": 0, "graph_writes": 0,
        "application_starts": 0, "ocr_executed": False,
        "professional_review": "not_run",
        "boundary": "First full real PDF Docling conversion only; no business extraction or acceptance.",
    }
    if outcome.native_document is not None:
        native = DATA / "parsed/docling.json"
        write(native, outcome.native_document)
        result["native_pages"] = len(outcome.native_document["pages"])
        result["native_sha256"] = hashlib.sha256(native.read_bytes()).hexdigest()
        inventory = json.loads((DATA / "inspection/page-inventory.json").read_text())
        rotations = {p["physical_page"]: p["rotation"] for p in inventory["pages"]}
        try:
            normalized = DoclingNormalizer().convert(
                outcome.native_document, outcome.document_id,
                ImportContext(source_kind=SourceKind.REAL, kind=DocumentKind.INSPECTION_REPORT, bridge_id=None), rotations,
            )
            path = DATA / "parsed/normalized.json"
            write(path, normalized)
            result["normalized_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            result["normalization_status"] = "success"
        except Exception as error:
            result["normalization_status"] = "failed"
            result["normalization_exception_type"] = type(error).__name__
    write(OUT / "parser-result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "success" and not network_attempts and result.get("normalization_status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
