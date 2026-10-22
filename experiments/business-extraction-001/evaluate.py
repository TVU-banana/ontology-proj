"""Independent fact comparison after extraction; never provides facts to model."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from provider import HERE, write_json
from validate import ROOT, validate_records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder", type=Path)
    args = parser.parse_args()
    folder = args.run_folder
    records = json.loads((folder / "records.json").read_text())
    document = json.loads((folder / "input-document.json").read_text())
    golden_path = ROOT / "samples/synthetic/bridge-demo-001/golden.json"
    golden = json.loads(golden_path.read_text())
    approval = json.loads((HERE / "results/baseline-approval.json").read_text())
    checks = []
    def check(name, passed, actual=None, expected=None):
        checks.append({"name": name, "passed": bool(passed), "actual": actual, "expected": expected})
    counts = {k: len(records[k]) for k in ("bridges", "components", "defects", "observations")}
    expected_counts = {"bridges": golden["expected_counts"]["bridges"], "components": golden["expected_counts"]["components"], "defects": golden["expected_counts"]["defects"], "observations": golden["expected_counts"]["positive_observations"]}
    check("object_counts", counts == expected_counts, counts, expected_counts)
    check("bridge_identity_and_name", [(b["bridge_code"], b["name"]) for b in records["bridges"]] == [(golden["bridge"]["bridge_id"], golden["bridge"]["name"])])
    bridge_codes = {b["bridge_id"]: b["bridge_code"] for b in records["bridges"]}
    actual_components = sorted((c["component_code"], c["name"], bridge_codes[c["bridge_id"]]) for c in records["components"])
    expected_components = sorted((c["component_id"], c["name"], c["bridge_id"]) for c in golden["components"])
    check("component_identity_name_and_parent", actual_components == expected_components, actual_components, expected_components)
    component_codes = {c["component_id"]: c["component_code"] for c in records["components"]}
    defects = {d["defect_id"]: d for d in records["defects"]}
    actual_facts = []
    for o in records["observations"]:
        d = defects[o["defect_id"]]
        actual_facts.append({"defect_id": d["defect_code"], "observation_id": o["observation_code"], "component_id": component_codes[o["component_id"]], "defect_type": d["defect_type_raw"], "location_text": d["location_text"], "count": o["reported_count"], "area_value": o["area_value"], "original_unit": o["area_original_unit"], "normalized_unit": o["area_unit"], "unit_conversion": o["area_conversion"], "observed_at": o["observed_at"], "source_kind": o["source_kind"], "polarity": o["assertion"], "approximate": o["area_is_approximate"], "target_text": o["description"]})
    keys = list(actual_facts[0]) if actual_facts else []
    expected_facts = [{k: f[k] for k in keys} for f in golden["positive_facts"]]
    check("all_positive_fact_fields", sorted(actual_facts, key=lambda f: f["observation_id"]) == sorted(expected_facts, key=lambda f: f["observation_id"]), actual_facts, expected_facts)
    check("unknown_dates_preserved", all(o["observed_at"] is None and "observed_at" in o["missing_fields"] for o in records["observations"]))
    check("report_date_not_used_as_observation_date", not any(o["observed_at"] == golden["report_date"] for o in records["observations"]))
    negatives = [s for s in records["statements"] if s["kind"] == "negative"]
    check("negative_statement_retained_with_correct_component", len(negatives) == len(golden["negative_facts"]) and all(any(s["text"] == f["target_text"] and component_codes.get(s["component_ref"]) == f["component_id"] for s in negatives) for f in golden["negative_facts"]))
    check("no_negative_or_duplicate_defect", {d["defect_code"] for d in records["defects"]} == {f["defect_id"] for f in golden["positive_facts"]} and len({o["observation_code"] for o in records["observations"]}) == len(records["observations"]))
    check("generated_records_pass_program_checks", not validate_records(records, document))
    check("baseline_confirmed_by_user", approval["authority"] == "user_reply_in_current_chat" and approval["answer"] == "确认，按此验收")
    warnings = []
    for statement in records["statements"]:
        if statement["kind"] == "aggregate" and "不表示新增" in statement["text"]:
            warnings.append({"code": "STATEMENT_CATEGORY_REVIEW", "message": "模型将记录说明段落标为 aggregate；应更适合 unmapped。原文保留，未产生汇总病害/观测。", "text": statement["text"]})
    run = json.loads((folder / "run.json").read_text())
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "core_baseline_passed_with_advisory" if all(c["passed"] for c in checks) and warnings else "core_baseline_passed" if all(c["passed"] for c in checks) else "failed", "passed": sum(c["passed"] for c in checks), "failed": sum(not c["passed"] for c in checks), "checks": checks, "warnings": warnings, "golden_sha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(), "golden_original_status": golden["status"], "human_baseline_confirmation": "baseline-approval.json", "golden_sent_to_model": run["golden_provided_to_model"], "repair_from": run.get("repair_from"), "limits": ["Single synthetic selectable-text PDF", "Validator-feedback correction" if run.get("repair_from") else "Fresh invocation without prior output or diagnostics", "No independent original-report or OCR validation", "No database or web application executed"]}
    write_json(folder / "evaluation.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("checks", "limits")}, ensure_ascii=False))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
