"""T01 actual local file I/O; no parsing, golden, graph or model calls."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile

from ontology_proj.adapters.file_store import LocalFileStore
from ontology_proj.application.dto import ImportContext
from ontology_proj.application.files import FileStoreError
from ontology_proj.domain.source import DocumentKind, SourceKind


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_path = root / "samples/synthetic/bridge-demo-001/input/bridge-demo-001.pdf"
    data = source_path.read_bytes()
    context = ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.SYNTHETIC, "B-DEMO-001")
    checks = {}
    with tempfile.TemporaryDirectory(prefix="ontology-t01-") as temporary:
        storage = Path(temporary)
        store = LocalFileStore(storage)
        first = store.save_source(data, source_path.name, context)
        pdf = storage / first.source.pdf_key
        metadata = pdf.with_name("source.json")
        before = metadata.read_bytes()
        checks["source_bytes_preserved"] = pdf.read_bytes() == data
        checks["full_hash_identity"] = first.source.document_id == "doc_" + hashlib.sha256(data).hexdigest()
        second = store.save_source(data, "alternate-name.pdf", context)
        checks["duplicate_reuses_draft"] = second.duplicate and second.source == first.source and metadata.read_bytes() == before
        try:
            store.save_source(data, source_path.name, ImportContext(DocumentKind.INSPECTION_REPORT, SourceKind.REAL, "B-DEMO-001"))
        except FileStoreError as error:
            checks["conflict_rejected"] = error.code == "SOURCE_CONTEXT_CONFLICT"
        else:
            checks["conflict_rejected"] = False
        checks["conflict_preserves_artifacts"] = metadata.read_bytes() == before and pdf.read_bytes() == data
        reopened = LocalFileStore(storage)
        checks["new_adapter_reads_completed_source"] = reopened.read_source(first.source.document_id) == first.source and reopened.read_pdf(first.source.document_id) == data
        checks["no_parse_or_graph_artifacts"] = sorted(p.name for p in pdf.parent.iterdir()) == ["source.json", "source.pdf"]
        checks["temporary_files_cleaned"] = not list(storage.rglob("*.tmp"))
        draft = asdict(first.source)
    result = {
        "task": "S2.1-T01", "input": str(source_path.relative_to(root)),
        "input_sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "source_draft": draft, "checks": checks, "passed": all(checks.values()),
        "boundary": "Real sample file I/O in a temporary DATA_ROOT, reopened adapter (not process restart); no CLI pipeline, Docling conversion, model or Neo4j. Temporary data cleaned.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"task": result["task"], "checks": len(checks), "passed": result["passed"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
