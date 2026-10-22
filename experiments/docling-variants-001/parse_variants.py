"""Run the unchanged baseline Docling conversion configuration on four PDFs.

Does not import expectations, golden, or business/model extraction code.
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASES = ("missing-area", "additional-negative", "duplicate-row", "reordered-columns")


def main():
    spec = importlib.util.spec_from_file_location("baseline_parser", ROOT / "experiments/docling-parse-001/parse_pdf.py")
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    for case in CASES:
        print(f"Parsing {case}", flush=True)
        parser.INPUT = ROOT / "samples/synthetic/bridge-variants-001/input" / f"{case}.pdf"
        parser.OUT = HERE / "results" / case
        parser.OUT.mkdir(parents=True, exist_ok=False)
        parser.main()


if __name__ == "__main__":
    main()
