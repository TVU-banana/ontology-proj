"""Prepare requests offline using frozen extraction code; never load credentials/call API."""
import importlib.util
import json
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    config = json.loads((HERE / "baseline.json").read_text())
    for path, digest in config["frozen_artifacts_sha256"].items():
        if sha(HERE / path) != digest:
            raise ValueError(f"Frozen artifact changed: {path}")
    frozen = HERE / "frozen"
    sys.path.insert(0, str(frozen))
    spec = importlib.util.spec_from_file_location("frozen_extractor", frozen / "extract.py")
    extractor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extractor)
    extractor.ROOT = ROOT
    extractor.SCHEMA_PATH = frozen / "business.schema.json"
    successful = json.loads((ROOT / config["reference_successful_request"]).read_text())
    assert sha(ROOT / config["reference_successful_request"]) == config["reference_request_sha256"]
    folder = HERE / "prepared"
    folder.mkdir(exist_ok=False)
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    for case in config["cases"]:
        cid = case["case_id"]
        for field in ("pdf", "parsed_source", "native_docling"):
            check(f"{cid}:{field}_unchanged", sha(ROOT / case[field]) == case[field + "_sha256"])
        extractor.NORMALIZED_PATH = HERE / case["parsed_snapshot"]
        extractor.PDF_PATH = ROOT / case["pdf"]
        original = json.loads(extractor.NORMALIZED_PATH.read_text())
        document, mapping = extractor.adapt_document()
        prompt = extractor.messages(document)
        check(f"{cid}:frozen_system_prompt", prompt[0]["content"] == (frozen / "system-prompt.txt").read_text())
        check(f"{cid}:two_fresh_messages", [m["role"] for m in prompt] == ["system", "user"])
        payload = json.loads(prompt[1]["content"])
        check(f"{cid}:same_candidate_schema_as_successful", payload["candidate_schema"] == json.loads(successful["messages"][1]["content"])["candidate_schema"])
        check(f"{cid}:only_allowed_payload_keys", set(payload) == set(config["input_policy"]["payload_keys"]))
        check(f"{cid}:raw_text_and_order_preserved", [b["text"] for b in payload["blocks_in_document_order"]] == [b["text"] for b in original["blocks"]])
        check(f"{cid}:source_refs_preserved", [b["source_ref"] for b in payload["blocks_in_document_order"]] == [b["source_ref"] for b in original["blocks"]])
        check(f"{cid}:block_ids_have_mapping", len(mapping) == len(document["blocks"]) and len({b["block_id"] for b in document["blocks"]}) == len(mapping))
        check(f"{cid}:parser_metadata_not_invented", payload["tables"] == original["tables"] and payload["parser_warnings"] == original["warnings"])
        check(f"{cid}:no_position_offsets_sent", all(not set(config["input_policy"]["excluded_block_fields"]) & set(b) for b in payload["blocks_in_document_order"]))
        check(f"{cid}:raw_input_document_text_unchanged", [b["text"] for b in document["blocks"]] == [b["text"] for b in original["blocks"]])
        case_folder = folder / cid
        case_folder.mkdir()
        save(case_folder / "input-document.json", document)
        save(case_folder / "block-id-map.json", mapping)
        request = {**config["request_parameters"], "messages": prompt}
        if cid == "baseline":
            check("baseline:entire_request_reproduces_successful_request", request == successful)
        for run_id in case["planned_run_ids"]:
            save(case_folder / f"{run_id}-request.json", request)
        check(f"{cid}:two_requests_identical_before_calls", sha(case_folder / f"{cid}-01-request.json") == sha(case_folder / f"{cid}-02-request.json"))
    files = list(folder.rglob("*-request.json"))
    check("exactly_ten_prepared_requests", len(files) == config["execution_policy"]["planned_total_calls"] == 10)
    report = {"status": "offline_preparation_passed", "checks_passed": checks, "passed": len(checks), "failed": 0, "prepared_requests": 10, "private_config_read": False, "model_calls": 0, "scope": "Configuration/request preparation only, not API or extraction acceptance"}
    save(HERE / "preparation-checks.json", report)
    print(json.dumps({k: report[k] for k in ("passed", "failed", "prepared_requests", "model_calls", "private_config_read")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
