"""Compare the authored PDF against the separately fixed fixture expectations.

This is artifact QA, not a Docling/business-extraction or Evidence capability test.
"""
import hashlib
import json
from pathlib import Path
import re
import pdfplumber
from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
golden_path = HERE / "golden.json"
golden = json.loads(golden_path.read_text())
pdf_path = ROOT / golden["target_pdf"]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def compact(text):
    # Only for artifact comparisons, never for Evidence quote/charspan storage.
    return re.sub(r"\s+", "", text)

checks = []

def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)

with pdfplumber.open(pdf_path) as pdf:
    check("two_pages", len(pdf.pages) == golden["expected_page_count"])
    texts = [page.extract_text() or "" for page in pdf.pages]
    tables = [page.extract_tables() for page in pdf.pages]
    all_text = "\n".join(texts)
    for forbidden in golden["forbidden_document_text"]:
        check(f"excluded_document_text_{forbidden}", compact(forbidden) not in compact(all_text))
    for n, text in enumerate(texts, 1):
        check(f"page_{n}_synthetic_header", "SYNTHETIC / 演示样本" in text)
        check(f"page_{n}_fiction_footer", "虚构桥梁与记录，仅用于学习演示" in text)
        check(f"page_{n}_number", f"第 {n} 页 / 共 2 页" in text)
        check(f"page_{n}_no_replacement_character", "\ufffd" not in text and "\x00" not in text)
        check(f"page_{n}_characters_within_page", all(0 <= c["x0"] < c["x1"] <= pdf.pages[n-1].width and 0 <= c["top"] < c["bottom"] <= pdf.pages[n-1].height for c in pdf.pages[n-1].chars))
    check("bridge_name_and_id", golden["bridge"]["name"] in texts[0] and golden["bridge"]["bridge_id"] in texts[0])
    component_table = tables[0][2]
    for comp in golden["components"]:
        check(f"component_{comp['component_id']}", any(row[0] == comp["component_id"] and row[1] == comp["name"] for row in component_table[1:]))
    fact_rows = {1: tables[0][3][1:], 2: tables[1][0][1:]}
    check("exactly_three_positive_table_rows", sum(map(len, fact_rows.values())) == 3)
    for fact in golden["positive_facts"]:
        page = fact["page_no"]
        check(f"{fact['defect_id']}_paragraph", fact["target_text"] in texts[page-1])
        expected = [fact["defect_id"], fact["observation_id"], fact["location_text"], f"{fact['count']} 处", f"{fact['area_value']} {fact['original_unit']}"]
        check(f"{fact['defect_id']}_table_row", expected in fact_rows[page])
        check(f"{fact['observation_id']}_date_unknown_in_expectations", fact["observed_at"] is None and fact["missing_fields"] == ["observed_at"])
    for fact in golden["negative_facts"]:
        check("negative_fact_preserved", fact["target_text"] in texts[fact["page_no"]-1])
    check("report_date_is_labelled", ["报告编制日期", "2026-10-10（演示样本编制日期）"] in tables[0][1])
    check("observation_date_unknown_cell", ["观测日期", "未知"] in tables[0][1])
    check("all_dates_unknown_text", "所有观测日期均未知。" in texts[0] and "表中两条观测的日期均未知。" in texts[1])
    check("report_date_not_observation_date_text", "报告编制日期不作为观测日期" in texts[0])
    check("only_expected_defect_ids", set(re.findall(r"D-\d{3}", all_text)) == {f["defect_id"] for f in golden["positive_facts"]})
    check("only_expected_observation_ids", set(re.findall(r"O-\d{3}", all_text)) == {f["observation_id"] for f in golden["positive_facts"]})
    check("only_compilation_date", set(re.findall(r"\d{4}-\d{2}-\d{2}", all_text)) == {golden["report_date"]})
    check("no_extra_area_values", set(re.findall(r"(\d+(?:\.\d+)?)\s*m²", all_text)) == {f["area_value"] for f in golden["positive_facts"]})

reader = PdfReader(pdf_path)
embedded = []
for page in reader.pages:
    for font_ref in page["/Resources"]["/Font"].values():
        font = font_ref.get_object()
        desc = font.get("/FontDescriptor")
        if desc and any(k in desc.get_object() for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            embedded.append(str(font.get("/BaseFont")))
check("chinese_font_embedded", any("ArialUnicode" in name for name in embedded))
check("expectations_written_before_pdf", golden_path.stat().st_mtime_ns < pdf_path.stat().st_mtime_ns)
check("readable_facts_written_before_pdf", (HERE / "expected-facts.md").stat().st_mtime_ns < pdf_path.stat().st_mtime_ns)

manifest = {
    "fixture_id": golden["fixture_id"],
    "source_kind": "synthetic",
    "pdf": {"path": golden["target_pdf"], "sha256": sha(pdf_path), "bytes": pdf_path.stat().st_size, "page_count": len(reader.pages)},
    "expected_facts_sha256": sha(HERE / "expected-facts.md"),
    "golden_sha256": sha(golden_path),
    "source_adaptation_sha256": sha(HERE / "source-adaptation.md"),
    "checks_passed": checks,
    "check_count": len(checks),
    "embedded_fonts": sorted(set(embedded)),
    "human_review": "awaiting_user",
    "capability_boundary": "PDF authoring/text/table consistency only; no Docling, OCR, business extraction, database or production validation"
}
(HERE / "validation.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(manifest["pdf"], ensure_ascii=False))
print(f"{len(checks)} artifact checks passed; human review awaiting user")
