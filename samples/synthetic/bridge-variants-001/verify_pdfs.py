"""Independent PDF artifact QA against frozen deltas; no Docling or model calls."""
import copy
import hashlib
import json
import re
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(text):
    # Artifact QA only: never use normalized text for Evidence offsets.
    return re.sub(r"\s+", "", text)


def snapshot(path):
    with pdfplumber.open(path) as pdf:
        pages = []
        for page in pdf.pages:
            found = page.find_tables()
            boxes = [t.bbox for t in found]

            def outside_tables(obj):
                cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

            pages.append({
                "text": page.extract_text() or "",
                "outside": page.filter(outside_tables).extract_text() or "",
                "tables": [[[compact(cell or "") for cell in row] for row in t.extract()] for t in found],
                "boxes": boxes,
                "chars": page.chars,
                "width": page.width,
                "height": page.height,
            })
        return pages


def main():
    expected = json.loads((HERE / "expectations.json").read_text())
    freeze = json.loads((HERE / "freeze.json").read_text())
    checks = []

    def check(name, passed):
        if not passed:
            raise AssertionError(name)
        checks.append(name)

    for filename, digest in freeze["artifacts_sha256"].items():
        check(f"frozen_{filename}_unchanged", sha(HERE / filename) == digest)
    baseline = ROOT / expected["cases"][0]["planned_pdf"]
    check("original_pdf_unchanged", sha(baseline) == expected["derived_from"]["baseline_pdf_sha256"])
    check("original_golden_unchanged", sha(ROOT / expected["derived_from"]["golden_path"]) == expected["derived_from"]["golden_sha256"])
    before = snapshot(baseline)
    artifacts = []
    for case in expected["cases"][1:]:
        cid = case["case_id"]
        path = ROOT / case["planned_pdf"]
        pages = snapshot(path)
        check(f"{cid}:two_pages", len(pages) == 2)
        wanted_tables = copy.deepcopy([p["tables"] for p in before])
        wanted_outside = [compact(p["outside"]) for p in before]
        for delta in case["changes"]:
            idx = delta["page_no"] - 1
            op = delta["operation"]
            if op == "replace_paragraph":
                old, new = compact(delta["old_text"]), compact(delta["new_text"])
                check(f"{cid}:one_original_paragraph", wanted_outside[idx].count(old) == 1)
                wanted_outside[idx] = wanted_outside[idx].replace(old, new)
            elif op == "insert_paragraph":
                after = compact(delta["after_text"])
                check(f"{cid}:one_insertion_anchor", wanted_outside[idx].count(after) == 1)
                wanted_outside[idx] = wanted_outside[idx].replace(after, after + compact(delta["new_text"]))
            elif op == "replace_table_cell":
                table = wanted_tables[idx][-1]
                column = table[0].index(compact(delta["column_header"]))
                rows = [r for r in table[1:] if delta["row_identity"]["observation_code"] in r]
                check(f"{cid}:one_target_row", len(rows) == 1 and rows[0][column] == compact(delta["old_text"]))
                rows[0][column] = compact(delta["new_text"])
            elif op == "duplicate_table_row_once":
                wanted_tables[idx][-1].append([compact(c) for c in delta["row_cells"]])
            elif op == "reorder_columns":
                wanted_tables[idx][-1] = [[compact(c) for c in delta["new_column_headers"]]] + [[compact(c) for c in r] for r in delta["new_rows"]]
            else:
                raise ValueError(op)
        all_text = "\n".join(p["text"] for p in pages)
        for i, page in enumerate(pages):
            check(f"{cid}:page{i+1}_only_declared_nontable_change", compact(page["outside"]) == wanted_outside[i])
            check(f"{cid}:page{i+1}_only_declared_table_change", page["tables"] == wanted_tables[i])
            check(f"{cid}:page{i+1}_synthetic_and_number", "SYNTHETIC / 演示样本" in page["text"] and f"第 {i+1} 页 / 共 2 页" in page["text"])
            check(f"{cid}:page{i+1}_valid_glyphs", "\ufffd" not in page["text"] and "\x00" not in page["text"])
            check(f"{cid}:page{i+1}_chars_inside_page", all(0 <= c["x0"] < c["x1"] <= page["width"] and 0 <= c["top"] < c["bottom"] <= page["height"] for c in page["chars"]))
            check(f"{cid}:page{i+1}_tables_clear_footer", all(b[3] < page["height"] - 60 for b in page["boxes"]))
        for fact in case["expected"]["positive_facts"]:
            check(f"{cid}:{fact['observation_code']}_paragraph", compact(fact["description_text"]) in compact(pages[fact["expected_page_no"]-1]["text"]))
        for j, fact in enumerate(case["expected"]["negative_statements"]):
            check(f"{cid}:negative{j+1}_preserved", compact(fact["text"]) in compact(pages[fact["expected_page_no"]-1]["text"]))
        for forbidden in case.get("must_not_retain_document_text", []):
            check(f"{cid}:forbidden_{forbidden}_absent", forbidden not in all_text)
        check(f"{cid}:only_expected_defect_ids", set(re.findall(r"D-\d{3}", all_text)) == {"D-001", "D-002", "D-003"})
        check(f"{cid}:only_expected_observation_ids", set(re.findall(r"O-\d{3}", all_text)) == {"O-001", "O-002", "O-003"})
        check(f"{cid}:only_fixed_compilation_date", set(re.findall(r"\d{4}-\d{2}-\d{2}", all_text)) == {"2026-10-10"})
        check(f"{cid}:only_expected_numeric_areas", set(re.findall(r"(\d+(?:\.\d+)?)\s*m²", all_text)) == {f["area_value"] for f in case["expected"]["positive_facts"] if f["area_value"] is not None})
        reader = PdfReader(path)
        embedded = []
        for page in reader.pages:
            for ref in page["/Resources"]["/Font"].values():
                font = ref.get_object()
                desc = font.get("/FontDescriptor")
                if desc and any(k in desc.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    embedded.append(str(font.get("/BaseFont")))
        check(f"{cid}:chinese_font_embedded", any("ArialUnicode" in f for f in embedded))
        check(f"{cid}:expectations_predate_pdf", all((HERE / f).stat().st_mtime_ns < path.stat().st_mtime_ns for f in freeze["artifacts_sha256"]))
        artifacts.append({"case_id": cid, "path": case["planned_pdf"], "sha256": sha(path), "bytes": path.stat().st_size, "page_count": 2})
    result = {"suite_id": expected["suite_id"], "expectations_version": expected["expectations_version"], "created_on_local_date": "2026-10-11", "pdfs": artifacts, "checks_passed": checks, "passed": len(checks), "failed": 0, "frozen_expectations_unchanged": True, "capability_boundary": "PDF artifact text/table/layout QA only; no Docling, model extraction, schema validation or database execution", "docling_runs": 0, "model_calls": 0, "visual_review": "see validation.md", "human_review": "awaiting_user"}
    (HERE / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"pdfs": artifacts, "passed": len(checks), "failed": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
