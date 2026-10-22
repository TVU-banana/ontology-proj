"""Offline comparison of existing PDF text-layer/native/normalized snapshots.

No parser, provider, graph, application or old experiment is invoked.
"""
import json
from pathlib import Path
import re
import unicodedata
from decimal import Decimal, InvalidOperation

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "data/s52-local-shengli-20261012-01"
DATA = ROOT / "data/s52-proofread-20261012-01"
OUT = ROOT / "specs/008-system-acceptance/results/proofread-20261012-01"


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    document = json.loads((SOURCE / "parsed/normalized.json").read_text())
    probes = json.loads((SOURCE / "review/parse-probes.json").read_text())
    golden = json.loads((SOURCE / "review/confirmed-20261012-01/golden-facts.json").read_text())
    assert golden["human_confirmed"]
    fields, rows = [], []
    names = ["平均值", "最小值", "平均碳化深度", "标准差", "强度推定值", "设计值"]
    for page in (34, 35):
        table = next(t for t in document["tables"] if t["page_no"] == page)
        source_rows = []
        for line in (SOURCE / "inspection" / f"page-{page:02d}.txt").read_text().splitlines():
            match = re.match(r"^\s*(\d+-\d+#板)\s+(.*?)\s*$", line)
            if match:
                values = match[2].split()
                assert len(values) == 6
                source_rows.append((match[1], values))
        cells = table["native_data"]["table_cells"]
        for label, values in source_rows:
            owner = [c for c in cells if c["start_col_offset_idx"] == 0 and compact(c["text"]) == label]
            assert len(owner) == 1
            row = owner[0]["start_row_offset_idx"]
            row_fields = []
            for column, (name, expected) in enumerate(zip(names, values), 1):
                # Preserve original row/column indexes, including missing cells.
                # v1's compressed nonempty list overreported a subsequent shift.
                found = [c for c in cells if c["start_row_offset_idx"] <= row < c["end_row_offset_idx"]
                         and c["start_col_offset_idx"] <= column < c["end_col_offset_idx"]]
                actual = compact(found[0]["text"]) if len(found) == 1 else None
                same = expected == actual
                if actual is not None:
                    try:
                        same = Decimal(expected) == Decimal(actual)
                    except InvalidOperation:
                        pass
                item = {"physical_page": page, "row_label": label, "column": column,
                        "field": name, "pdf_value": expected, "normalized_value": actual,
                        "equal_value": same, "native_cells": found}
                row_fields.append(item)
                fields.append(item)
            rows.append({"physical_page": page, "row_label": label,
                         "all_values_match": all(f["equal_value"] for f in row_fields),
                         "field_comparison": row_fields})
    write(DATA / "table-field-comparison-v2.json", rows)
    evidence = {p["id"]: p for p in probes}
    assert all(p["found"] for p in evidence.values())  # Reuse saved checks only.
    captions = []
    for block in document["blocks"]:
        if any(l["page_no"] == 20 for l in block["locations"]) and "3-1#" in compact(block["text"]):
            captions.append({k: block[k] for k in ("block_id", "text", "locations")})
    # Explicitly separate preserved text from unexecuted business semantics.
    items = [
        ("F01", "文本符合", "桥梁名称在原文与解析文本保留；未复用synthetic桥梁ID。", [1, 14, 18]),
        ("F02", "文本符合；业务日期未运行", "报告日期保留；实际Observation尚未生成，不能声称已验证日期不补齐。", [1]),
        ("F03", "文本符合；业务去重未运行", "0.04面积/单位与3跨位置保留，已有正文/表/照片多处证据；未生成业务对象，不能把多处描述当多条观测。", [14]),
        ("F04", "文本符合；类型/独立对象未运行", "左侧腹板2.6㎡与照片说明保留；同一表单元格，未知类型null是人工基线，尚非模型实际输出。", [18, 20]),
        ("F05", "文本符合；类型/独立对象未运行", "挑臂0.9㎡与腹板位置文字分别保留；同一单元格，未实际生成独立Defect/Observation。", [18, 20]),
        ("F06", "文本符合；实体分流未运行", "未见明显病害保留；是否误生成肯定实体待实际抽取验证。", [14]),
        ("F07", "存在结构问题", "8#跨/8-4#续行原文字保留，但页18表与页19段落未形成跨页表关联。", [18, 19]),
        ("F08", "存在数值归属和表头问题", "67行402字段按源行列比较，2行标准差未正确归属；另有续表首行表头误标与空备注列丢失。", [34, 35]),
        ("F09", "原文冲突保留；专业业务未运行", "74.11和71.11分别保留，未选择正确值或生成专业评级。", [44, 45]),
        ("F10", "单位文本符合；业务测量分流未运行", "原文L=4m和面积单位仍有区别；是否错误入面积字段只能由未来抽取/校验判定。", [18]),
        ("F11", "未覆盖OCR", "旧文字层盘点及无解析文字结果复用；补看57–59页，确认全部六页为扫描图纸，图中尺寸/文字未提取。", [55, 56, 57, 58, 59, 60]),
        ("F12", "核查范围明确；整体业务未运行", "三条是第一轮核查子集，解析中仍有大量其他原文，不能声称整份报告只有三条或全部准确。", [14, 18]),
    ]
    detail = {
        "items": [{"id": i, "status": s, "finding": f, "pages": p} for i, s, f, p in items],
        "existing_probe_ids_reused": list(evidence), "existing_probes_rerun": False,
        "photo_caption_evidence": captions,
        "table_rows_checked": len(rows), "table_fields_checked": len(fields),
        "table_rows_with_all_values_matching": sum(r["all_values_match"] for r in rows),
        "table_fields_matching": sum(f["equal_value"] for f in fields),
        "field_differences": [f for f in fields if not f["equal_value"]],
        "business_extraction_answers_reports": "not_run", "professional_system_acceptance": "not_run",
    }
    write(DATA / "proofreading-details.json", detail)
    summary = {k: v for k, v in detail.items() if k not in ("photo_caption_evidence", "field_differences")}
    summary["field_differences"] = [{k: f[k] for k in ("physical_page", "row_label", "column", "field", "pdf_value", "normalized_value")}
                                    for f in detail["field_differences"]]
    summary.update({"new_original_pages_viewed": [8, 9, 10, 11, 57, 58, 59],
                    "evidence_level": "Agent visual plus offline source-to-snapshot comparison, not professional acceptance",
                    "first_auxiliary_result_retained": "table-row-comparison.json v1 retained; v2 uses fixed column indexes to isolate two true errors",
                    "generation_embedding_parser_graph_app_calls": 0, "full_s52_complete": False})
    write(OUT / "comparison.json", summary)
    print(json.dumps({k: summary[k] for k in ("table_rows_checked", "table_fields_checked", "table_fields_matching", "field_differences")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
