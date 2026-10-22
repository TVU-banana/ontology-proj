"""Inspect the draft and hand-authored examples; not a production validator.

Program-layer counterexamples intentionally pass JSON Schema. This script
documents that boundary instead of claiming their business validation ran.
"""
import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def mutate(base, case):
    value = copy.deepcopy(base)
    parts = case["path"].strip("/").split("/")
    target = value
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    key = int(parts[-1]) if isinstance(target, list) else parts[-1]
    target[key] = case["value"]
    return value


def main():
    schema = read(HERE / "business.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    bases = {name: read(HERE / "examples" / name) for name in ["candidate.json", "records.json"]}
    checks = []

    def check(name, condition, detail=None):
        checks.append({"name": name, "passed": bool(condition), "detail": detail})

    check("draft_2020_12_meta_schema", True)
    for name, value in bases.items():
        errors = list(validator.iter_errors(value))
        check("positive_" + name, not errors, [error.message for error in errors])
    for case in read(HERE / "examples/counterexamples.json"):
        errors = list(validator.iter_errors(mutate(bases[case["base"]], case)))
        if case["expected_layer"] == "schema":
            check(case["name"], bool(errors), {"expected": "schema_reject", "actual": "rejected" if errors else "passed"})
        else:
            check(case["name"], not errors, {"expected": "schema_pass_requires_program", "actual": "passed" if not errors else "rejected", "program_validation": "not_implemented", "reason": case["reason"]})

    normalized = read(ROOT / "experiments/docling-parse-001/results/normalized.json")
    blocks = {b["block_id"]: b for b in normalized["blocks"]}
    candidate, records = bases["candidate.json"], bases["records.json"]
    check("document_namespace_mapping", candidate["document_id"] == records["document_id"] == "doc_" + normalized["document_id"])
    proposals = [s for e in candidate["entities"] for s in e["sources"]]
    proposals += [s for r in candidate["relations"] for s in r["sources"]]
    proposals += [s for st in candidate["statements"] for s in st["sources"]]
    check("candidate_quotes_really_exist", all(s["block_id"] in blocks and blocks[s["block_id"]]["text"].count(s["exact_quote"]) == 1 for s in proposals), len(proposals))
    correct = True
    for e in records["evidence"]:
        b = blocks.get(e["block_id"])
        if b is None:
            correct = False
            continue
        a, z = e["charspan"]
        x0, y0, x1, y1 = e["bbox"]
        locs = [l for l in b["locations"] if l["page_no"] == e["page_no"]]
        correct &= (e["document_id"] == records["document_id"] and 0 <= a < z <= len(b["text"]) and b["text"][a:z] == e["quote"] and 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and e["source_ref"] == b["source_ref"] and any(all(abs(v-w) <= 0.000001 for v,w in zip(e["bbox"],l["bbox"])) for l in locs))
    check("record_evidence_matches_actual_snapshot", correct, len(records["evidence"]))
    evidence_ids = {e["evidence_id"] for e in records["evidence"]}
    all_records = sum((records[key] for key in ["bridges", "components", "defects", "observations"]), [])
    check("example_evidence_references_resolve", all(set(r["evidence_ids"]) <= evidence_ids and r["source_document_id"] == records["document_id"] for r in all_records))
    bridge_ids = {r["bridge_id"] for r in records["bridges"]}
    component_map = {r["component_id"]: r for r in records["components"]}
    defect_map = {r["defect_id"]: r for r in records["defects"]}
    check("example_object_references_resolve", all(c["bridge_id"] in bridge_ids for c in component_map.values()) and all(d["component_id"] in component_map for d in defect_map.values()) and all(o["defect_id"] in defect_map and o["component_id"] == defect_map[o["defect_id"]]["component_id"] for o in records["observations"]))
    local = {e["local_id"]: e for e in candidate["entities"]}
    ends = {"HAS_COMPONENT": ("Bridge","Component"), "HAS_DEFECT": ("Component","Defect"), "HAS_OBSERVATION": ("Defect","Observation")}
    check("example_relation_end_types", len(local)==len(candidate["entities"]) and all(r["from_ref"] in local and r["to_ref"] in local and (local[r["from_ref"]]["entity_type"],local[r["to_ref"]]["entity_type"]) == ends[r["relation_type"]] for r in candidate["relations"]))
    golden_path = ROOT / "samples/synthetic/bridge-demo-001/golden.json"
    golden = read(golden_path)
    comp_codes = {c["component_id"]:c["component_code"] for c in records["components"]}
    actual=[]
    for o in records["observations"]:
        d=defect_map[o["defect_id"]]
        actual.append((d["defect_code"],o["observation_code"],comp_codes[o["component_id"]],d["location_text"],o["area_value"],o["area_unit"],o["observed_at"]))
    expected=[(f["defect_id"],f["observation_id"],f["component_id"],f["location_text"],f["area_value"],f["normalized_unit"],f["observed_at"]) for f in golden["positive_facts"]]
    counts=golden["expected_counts"]
    check("hand_authored_example_matches_frozen_facts", sorted(actual)==sorted(expected) and len(records["bridges"])==counts["bridges"] and len(records["components"])==counts["components"] and len(records["defects"])==counts["defects"] and len(records["observations"])==counts["positive_observations"] and len(records["statements"])==counts["negative_text_facts"] and records["statements"][0]["text"]==golden["negative_facts"][0]["target_text"] and all(o["source_kind"]==records["source_kind"]==golden["source_kind"] and o["missing_fields"]==["observed_at"] for o in records["observations"]))
    result={"status":"draft_example_checks_only", "passed":sum(c["passed"] for c in checks), "failed":sum(not c["passed"] for c in checks), "checks":checks, "schema_sha256":hashlib.sha256((HERE/"business.schema.json").read_bytes()).hexdigest(), "golden_sha256":hashlib.sha256(golden_path.read_bytes()).hexdigest(), "golden_status":golden["status"], "boundaries":{"model_extraction":"not_run","production_validator":"not_implemented","neo4j":"not_run","human_approval":"pending"}}
    (HERE/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":result["passed"],"failed":result["failed"],"boundaries":result["boundaries"]},ensure_ascii=False))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
