"""Structural adaptation copied unchanged from baseline; raw text is authoritative.
No business records or inferred headers. Each variant derives IDs from its PDF hash.
"""
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader
ROOT = Path(__file__).resolve().parents[2]


def normalize(out):
    doc = json.loads((out / "docling.json").read_text())
    run = json.loads((out / "run.json").read_text())
    reader = PdfReader(ROOT / run["input"])
    pages = []
    for key, page in sorted(doc["pages"].items(), key=lambda p: int(p[0])):
        rotation = reader.pages[int(key)-1].rotation
        pages.append({"page_no": int(key), **page["size"], "coordinate_unit": "pdf_points", "rotation": rotation, "location_status": "available" if rotation == 0 else "unavailable"})
    sizes = {p["page_no"]: p for p in pages}
    blocks = []
    tables = []
    warnings = []

    def location(page_no, bbox, span):
        p = sizes[page_no]
        if p["rotation"] != 0:
            return None
        w, h = p["width"], p["height"]
        if bbox["coord_origin"] == "BOTTOMLEFT":
            norm = [bbox["l"]/w, (h-bbox["t"])/h, bbox["r"]/w, (h-bbox["b"])/h]
        elif bbox["coord_origin"] == "TOPLEFT":
            norm = [bbox["l"]/w, bbox["t"]/h, bbox["r"]/w, bbox["b"]/h]
        else:
            return None
        return {"page_no": page_no, "bbox": norm, "charspan": span, "raw_bbox": bbox, "location_level": "region"}

    def walk(ref):
        parts = ref.removeprefix("#/").split("/")
        obj = doc[parts[0]] if len(parts)==1 else doc[parts[0]][int(parts[1])]
        if parts[0] == "texts":
            text = obj["text"]
            locs = [location(p["page_no"], p["bbox"], p["charspan"]) for p in obj.get("prov", [])]
            blocks.append({"block_id": hashlib.sha256((run["input_sha256"]+ref).encode()).hexdigest(), "kind": "title" if obj["label"]=="section_header" else "paragraph", "text": text, "source_ref": ref, "source_label": obj["label"], "content_layer": obj["content_layer"], "parent_ref": obj["parent"]["$ref"], "locations": [x for x in locs if x], "location_status": "available" if all(locs) and locs else "unavailable"})
        elif parts[0] == "tables":
            prov = obj.get("prov", [])
            page_no = prov[0]["page_no"] if len(prov)==1 else None
            tables.append({"source_ref": ref, "page_no": page_no, "num_rows": obj["data"]["num_rows"], "num_cols": obj["data"]["num_cols"], "caption_refs": obj["captions"], "header_semantics": "native_flags_only_no_inferred_headers"})
            for i, cell in enumerate(obj["data"]["table_cells"]):
                cell_ref = f"{ref}/data/table_cells/{i}"
                loc = location(page_no, cell["bbox"], [0, len(cell["text"])]) if page_no and cell.get("bbox") else None
                blocks.append({"block_id": hashlib.sha256((run["input_sha256"]+cell_ref).encode()).hexdigest(), "kind": "table_cell", "text": cell["text"], "source_ref": cell_ref, "parent_ref": ref, "table_id": ref, "row": cell["start_row_offset_idx"], "column": cell["start_col_offset_idx"], "row_span": cell["row_span"], "column_span": cell["col_span"], "column_header": cell["column_header"], "row_header": cell["row_header"], "locations": [loc] if loc else [], "location_status": "available" if loc else "unavailable", "span_basis": "application_owned_cell_text_not_native_table_provenance"})
        for child in obj.get("children", []):
            walk(child["$ref"])

    walk("#/body")
    for table in doc["tables"]:
        if not any(c["column_header"] or c["row_header"] for c in table["data"]["table_cells"]):
            warnings.append({"code": "HEADER_FLAGS_ABSENT", "source_ref": table["self_ref"]})
        if not table["captions"]:
            warnings.append({"code": "CAPTION_LINK_ABSENT", "source_ref": table["self_ref"]})
    normalized = {"document_id": run["input_sha256"], "schema_version": "experiment-1", "source_kind": "synthetic", "pages": pages, "blocks": blocks, "tables": tables, "warnings": warnings, "note": "Experimental structural adaptation only; raw text preserved, no inferred headers or business entities"}
    (out / "normalized.json").write_text(json.dumps(normalized, ensure_ascii=False, indent=2)+"\n")
    return normalized
