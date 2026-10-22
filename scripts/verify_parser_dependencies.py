"""T02 imports and real local model initialization; no PDF conversion."""
import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import distributions, version
import json
from pathlib import Path
import platform
import socket
import tempfile
import time
from unittest.mock import patch

from ontology_proj.adapters.docling_parser import EXPECTED_VERSIONS, REQUIRED_MODEL_FILES
from ontology_proj.bootstrap import build_parser
from ontology_proj.settings import Settings


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--layout-model-dir", type=Path, required=True)
    cli.add_argument("--table-model-dir", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    attempts = []
    def no_connect(sock, address):
        attempts.append("socket.connect")
        raise RuntimeError("Network disabled for dependency verification")
    def no_download(*a, **kw):
        attempts.append("model_download")
        raise RuntimeError("Model downloads disabled for dependency verification")
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ontology-parser-models-") as temporary:
        root = Path(temporary)
        (root / "docling-project--docling-layout-heron").symlink_to(args.layout_model_dir.resolve(), target_is_directory=True)
        (root / "docling-project--docling-models").symlink_to(args.table_model_dir.resolve(), target_is_directory=True)
        # Patch after imports needed for patch target resolution; initialization
        # itself runs with both network and SDK downloads forbidden.
        with patch("socket.socket.connect", no_connect), patch("docling.models.utils.hf_model_download.download_hf_model", no_download):
            parser = build_parser(Settings(_env_file=None, docling_artifacts_path=root))
            before = asdict(parser.check())
            diagnostic = parser.initialize()
            after = asdict(parser.check())
        files = {}
        for key in REQUIRED_MODEL_FILES:
            path = root / key
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files[key] = {"sha256": digest, "bytes": path.stat().st_size}
        pipeline = {}
        if diagnostic is None:
            from docling.datamodel.base_models import InputFormat
            options = parser._converter.format_to_options[InputFormat.PDF].pipeline_options
            pipeline = {"do_ocr": options.do_ocr, "do_table_structure": options.do_table_structure,
                        "device": options.accelerator_options.device.value, "num_threads": options.accelerator_options.num_threads,
                        "enable_remote_services": options.enable_remote_services, "allow_external_plugins": options.allow_external_plugins,
                        "artifacts_path_explicit": options.artifacts_path is not None}
        result = {
            "task": "S2.1-T02", "python": platform.python_version(), "platform": platform.system() + "-" + platform.machine(),
            "versions": parser.installed_versions(), "expected_versions": EXPECTED_VERSIONS,
            "other_versions": {name: version(name) for name in ["numpy", "fastapi", "pydantic", "torchvision"]},
            "installed_distributions": len(list(distributions())),
            "before_initialization": before, "after_initialization": after,
            "diagnostic": asdict(diagnostic) if diagnostic else None,
            "network_or_download_attempts": attempts, "model_files": files, "pipeline": pipeline,
            "elapsed_seconds": round(time.perf_counter()-start, 3),
            "passed": diagnostic is None and after["state"] == "ready" and not attempts,
            "boundary": "Actual SDK import and local layout/table model initialization; no PDF conversion, normalized output, model API, graph, or end-to-end acceptance. Temporary artifact links removed.",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["task", "passed", "diagnostic", "network_or_download_attempts", "elapsed_seconds"]}))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
