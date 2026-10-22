"""Validate the completed conversion against frozen fixture expectations.

This is not a business extractor. It never patches Docling output with golden data.
"""
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "results"


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    doc = json.loads((OUT / "docling.json").read_text())
    golden_path = ROOT / "samples/synthetic/bridge-demo-001/golden.json"
    golden = json.loads(golden_path.read_text())
    run = json.loads((OUT / "run.json").read_text())
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
    checks = []
    evidence = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    def match(name, quote, page_no):
        hits = []
        for b in blocks:
            if quote in b["text"] and any(l["page_no"]==page_no for l in b["locations"]):
                hits.append(b)
        check(name, len(hits)==1, {"quote": quote, "page_no": page_no, "matches": [b["source_ref"] for b in hits]})
        if len(hits)==1:
            b=hits[0]; start=b["text"].index(quote)
            evidence.append({"check": name, "block_id": b["block_id"], "source_ref": b["source_ref"], "quote": quote, "charspan": [start,start+len(quote)], "page_no": page_no, "bbox": next(l["bbox"] for l in b["locations"] if l["page_no"]==page_no), "location_level": "region"})

    check("conversion_success", run["status"]=="ConversionStatus.SUCCESS" and not run["errors"], run["status"])
    check("page_count", len(pages)==golden["expected_page_count"]==len(reader.pages), len(pages))
    check("input_unchanged", hashlib.sha256((ROOT/run["input"]).read_bytes()).hexdigest()==run["input_sha256"], run["input_sha256"])
    check("table_shapes", [(t["num_rows"],t["num_cols"]) for t in tables]==[(4,2),(3,3),(2,5),(3,5)], [(t["num_rows"],t["num_cols"]) for t in tables])
    for token in [golden["bridge"]["bridge_id"],golden["bridge"]["name"],golden["report_date"]]:
        cells=[b for b in blocks if b["kind"]=="table_cell" and token in b["text"] and b["table_id"]=="#/tables/0"]
        check("bridge_table_"+token, len(cells)==1, [b["source_ref"] for b in cells])
    for c in golden["components"]:
        cells=[b for b in blocks if b["kind"]=="table_cell" and b["table_id"]=="#/tables/1"]
        rows={b["row"] for b in cells if b["text"]==c["component_id"]}
        check(c["component_id"],len(rows)==1 and any(b["row"] in rows and b["text"]==c["name"] for b in cells), sorted(rows))
    for fact in golden["positive_facts"]+golden["negative_facts"]:
        match("statement_"+fact.get("defect_id","negative"),fact["target_text"],fact["page_no"])
    for quote, page_no in [("所有观测日期均未知。报告编制日期不作为观测日期",1),("表中两条观测的日期均未知。",2),("本节观测对象为主梁，构件编号 C-GIRDER-01。",2),("本文件为 synthetic（合成）样本",1)]:
        match("context_"+quote,quote,page_no)
    for fact in golden["positive_facts"]:
        table_id = "#/tables/2" if fact["page_no"]==1 else "#/tables/3"
        cells=[b for b in blocks if b.get("table_id")==table_id]
        rows={b["row"] for b in cells if b["text"]==fact["defect_id"]}
        actual=[[b["text"] for b in sorted(cells,key=lambda b:b["column"]) if b["row"]==row] for row in rows]
        expected=[fact["defect_id"],fact["observation_id"],fact["location_text"],f'{fact["count"]} 处',f'{fact["area_value"]} {fact["original_unit"]}']
        check("table_row_"+fact["defect_id"],actual==[expected],{"actual":actual,"expected":expected})
    valid_locs=[]
    for b in blocks:
        for l in b["locations"]:
            x0,y0,x1,y1=l["bbox"];start,end=l["charspan"]
            valid_locs.append(0<=x0<x1<=1 and 0<=y0<y1<=1 and 0<=start<end<=len(b["text"]))
    check("all_blocks_have_valid_locations",len(valid_locs)==len(blocks) and all(valid_locs),{"blocks":len(blocks),"locations":len(valid_locs)})
    check("evidence_quote_slices",all(next(b for b in blocks if b["block_id"]==e["block_id"])["text"][slice(*e["charspan"])]==e["quote"] for e in evidence),len(evidence))
    order=[b["source_ref"] for b in blocks]
    anchors=["#/tables/0/data/table_cells/0","#/tables/1/data/table_cells/0","#/texts/10","#/texts/11","#/tables/2/data/table_cells/0","#/texts/21","#/texts/22","#/tables/3/data/table_cells/0","#/texts/24"]
    indexes=[order.index(a) for a in anchors]
    check("key_reading_order",indexes==sorted(indexes),indexes)
    check("no_forbidden_source_text",all(not any(x in b["text"] for b in blocks) for x in golden["forbidden_document_text"]),golden["forbidden_document_text"])
    for table in doc["tables"]:
        if not any(c["column_header"] or c["row_header"] for c in table["data"]["table_cells"]):
            warnings.append({"code":"HEADER_FLAGS_ABSENT", "source_ref":table["self_ref"],"message":"Header text/row position retained but native header flags are all false; downstream must validate associations."})
        if not table["captions"]:
            warnings.append({"code":"CAPTION_LINK_ABSENT", "source_ref":table["self_ref"],"message":"Table title retained as nearby text; no native caption reference."})
    warnings.append({"code":"LINE_JOIN_SPACES", "message":"Two long Chinese paragraphs contain inserted wrap spaces; raw block text preserved."})
    normalized={"document_id":run["input_sha256"],"schema_version":"experiment-1","source_kind":"synthetic","pages":pages,"blocks":blocks,"tables":tables,"warnings":warnings,"note":"Experimental structural adaptation only, not production contract completion; no business entities."}
    save("normalized.json",normalized)
    save("evidence-checks.json",evidence)
    revisions={}
    for repo, ref in [("docling-project/docling-layout-heron","main"),("docling-project/docling-models","v2.3.0")]:
        p=Path('/path/to/user/.cache/huggingface/hub')/("models--"+repo.replace('/','--'))/"refs"/ref
        revisions[repo]={"reference":ref,"revision":p.read_text().strip() if p.exists() else None}
    summary={"checks":checks,"passed":sum(c["passed"] for c in checks),"failed":sum(not c["passed"] for c in checks),"warnings":warnings,"golden_sha256":hashlib.sha256(golden_path.read_bytes()).hexdigest(),"golden_status":golden["status"],"model_cache_revisions":revisions,"visual_review":"pending"}
    save("verification.json",summary)
    # Independent page render, annotate every Docling text/cell bbox, not PDF regeneration.
    for p in pages:
        n=p["page_no"]; img=Image.open(OUT/f"page-{n}.png").convert("RGB");draw=ImageDraw.Draw(img)
        for b in blocks:
            for loc in b["locations"]:
                if loc["page_no"]!=n:continue
                x0,y0,x1,y1=loc["bbox"];box=(x0*img.width,y0*img.height,x1*img.width,y1*img.height)
                color="#2464be" if b["kind"]=="table_cell" else "#b02828"
                draw.rectangle(box,outline=color,width=2)
        img.save(OUT/f"positions-page-{n}.png")
    print(json.dumps({"passed":summary["passed"],"failed":summary["failed"],"blocks":len(blocks),"warnings":warnings},ensure_ascii=False))


if __name__=="__main__":
    main()
