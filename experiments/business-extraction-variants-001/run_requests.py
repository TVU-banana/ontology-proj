"""Send exactly the ten frozen first-pass requests; no probe/repair/retries."""
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / "frozen"))
from provider import Relay, ProviderError, write_json
from extract import decode_output
from verify_baseline import main as preflight


def main():
    preflight()
    config = json.loads((HERE / "baseline.json").read_text())
    relay = Relay(config=ROOT / config["provider"]["credential_source"])
    if relay.base != config["provider"]["base_url"] or relay.model != config["request_parameters"]["model"]:
        raise ValueError("Private provider configuration differs from frozen baseline")
    out = HERE / "results"
    out.mkdir(exist_ok=False)
    outcomes = []
    for case in config["cases"]:
        prepared = HERE / "prepared" / case["case_id"]
        for run_id in case["planned_run_ids"]:
            folder = out / run_id
            folder.mkdir(exist_ok=False)
            for name in ("input-document.json", "block-id-map.json"):
                shutil.copyfile(prepared / name, folder / name)
            shutil.copyfile(prepared / f"{run_id}-request.json", folder / "request.json")
            request = json.loads((folder / "request.json").read_text())
            metadata = {"run_id": run_id, "case_id": case["case_id"], "baseline_id": config["baseline_id"], "status": "started", "started_at": datetime.now(timezone.utc).isoformat(), "requested_model": relay.model, "base_url": relay.base, "pdf_sha256": case["pdf_sha256"], "golden_provided_to_model": False, "prior_output_provided_to_model": False, "repair_from": None, "attempt": 1}
            write_json(folder / "run.json", metadata)
            print(f"Sending {run_id}", flush=True)
            try:
                response, elapsed = relay.request(config["provider"]["endpoint"], request, timeout=config["execution_policy"]["request_timeout_seconds"])
                write_json(folder / "response.json", response)
                choice = response.get("choices", [{}])[0]
                metadata.update(**elapsed, returned_model=response.get("model"), usage=response.get("usage"), finish_reason=choice.get("finish_reason"))
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Output did not finish normally, possibly truncated")
                candidate, fmt = decode_output(choice.get("message", {}).get("content", ""))
                write_json(folder / "candidate.json", candidate)
                metadata.update(status="candidate_received", output_format=fmt)
            except (ProviderError, ValueError, TypeError, IndexError) as error:
                metadata.update(status="failed", error=relay.scrub(str(error)))
            metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
            write_json(folder / "run.json", metadata)
            outcomes.append(metadata)
            write_json(out / "calls.json", {"planned": 10, "attempted": len(outcomes), "completed": len(outcomes), "automatic_retries": 0, "repair_calls": 0, "calls": outcomes})
            print(json.dumps({k: metadata.get(k) for k in ("run_id", "status", "elapsed_seconds", "finish_reason", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
