"""Offline preflight for frozen configuration/inputs/requests; no API or credentials."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    freeze = json.loads((HERE / "freeze.json").read_text())
    for path, digest in freeze["artifacts_sha256"].items():
        if sha(HERE / path) != digest:
            raise ValueError(f"Frozen baseline changed: {path}; create a new version")
    config = json.loads((HERE / "baseline.json").read_text())
    for case in config["cases"]:
        for field in ("pdf", "parsed_source", "native_docling"):
            if sha(ROOT / case[field]) != case[field + "_sha256"]:
                raise ValueError(f"Source changed: {case[field]}; create a new version")
        for run_id in case["planned_run_ids"]:
            request = json.loads((HERE / "prepared" / case["case_id"] / f"{run_id}-request.json").read_text())
            assert {k: v for k, v in request.items() if k != "messages"} == config["request_parameters"]
            assert [m["role"] for m in request["messages"]] == ["system", "user"]
            assert request["messages"][0]["content"] == (HERE / "frozen/system-prompt.txt").read_text()
    ref = config["acceptance_reference"]
    assert sha(ROOT / ref["expected_path"]) == ref["expected_sha256"]
    print(json.dumps({"status": "frozen_baseline_preflight_passed", "frozen_files": len(freeze["artifacts_sha256"]), "cases": 5, "prepared_requests": 10, "model_calls": 0}))


if __name__ == "__main__":
    main()
