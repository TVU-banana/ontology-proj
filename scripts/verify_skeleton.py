"""Real HTTP partial availability: unavailable probe targets, no generation."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler

from ontology_proj.settings import Settings


def verify_case(configured: bool) -> dict:
    with tempfile.TemporaryDirectory(prefix="ontology-s1-2-") as directory:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        fields = set(Settings.model_fields)
        environment = {k: v for k, v in os.environ.items() if k.lower() not in fields}
        environment.update(APP_HOST="127.0.0.1", APP_PORT=str(port), DATA_ROOT=str(Path(directory) / "data"))
        if configured:
            environment.update(
                NEO4J_URI="bolt://127.0.0.1:7687", NEO4J_USER="neo4j", NEO4J_PASSWORD="verification-only-graph",
                LLM_BASE_URL="http://127.0.0.1:7863/v1", LLM_MODEL="verification-only-model", LLM_API_KEY="verification-only-key",
            )
        opener = build_opener(ProxyHandler({}))

        def read(path):
            try:
                response = opener.open(f"http://127.0.0.1:{port}{path}", timeout=10)
            except HTTPError as error:
                response = error
            with response:
                return {"http_status": response.code, "body": json.loads(response.read())}

        log_path = Path(directory) / "server.log"
        with log_path.open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "ontology_proj"], env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Application exited before serving HTTP")
                    try:
                        health = read("/health")
                        break
                    except (URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Application did not start in time") from None
                        time.sleep(0.1)
                ready = read("/ready")
                assert health["http_status"] == 200
                assert ready["http_status"] == 503 and ready["body"]["ready"] is False
                capabilities = {c["name"]: c["status"] for c in ready["body"]["capabilities"]}
                assert capabilities["data_root"]["state"] == "ready"
                assert capabilities["graph"]["state"] == ("unavailable" if configured else "not_configured")
                assert capabilities["model"]["state"] == ("unavailable" if configured else "not_configured")
                for value in ("verification-only-graph", "verification-only-key", directory):
                    assert value not in json.dumps(ready)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        log_text = log_path.read_text()
        assert "verification-only-key" not in log_text and "verification-only-graph" not in log_text
        return {"case": "configured_unavailable" if configured else "unconfigured", "health": health, "ready": ready, "server_log": log_text, "server_stopped": process.poll() is not None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file; preserve previous evidence')
    cases = [verify_case(False), verify_case(True)]
    result = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "S2.3 HTTP compatibility", "acceptance": "A01 regression", "status": "passed",
        "python": sys.version.split()[0],
        "packages": {name: importlib.metadata.version(name) for name in ("ontology-proj", "fastapi", "pydantic", "pydantic-settings", "uvicorn", "pytest", "httpx")},
        "real_localhost_http": True, "model_generation_calls": 0, "successful_database_connections": 0, "unavailable_probes_attempted": True,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "passed", "cases": len(cases), "output": str(args.output)}))


if __name__ == "__main__":
    main()
