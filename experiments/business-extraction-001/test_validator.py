"""Offline adversarial tests against real source blocks and existing fixtures."""
import copy
import json
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path

from extract import decode_output
from provider import HERE, write_json
from validate import ROOT, validate_candidate, validate_records


class ValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = json.loads((ROOT / "specs/001-business-extraction/examples/candidate.json").read_text())
        cls.records = json.loads((ROOT / "specs/001-business-extraction/examples/records.json").read_text())
        cls.document = json.loads((ROOT / "experiments/docling-parse-001/results/normalized.json").read_text())
        cls.document["document_id"] = "doc_" + cls.document["document_id"]

    def test_positive_candidate_and_records(self):
        result = validate_candidate(self.candidate, self.document)
        self.assertIsNotNone(result["records"], result["diagnostics"])
        self.assertEqual(validate_records(self.records, self.document), [])

    def test_all_existing_counterexamples_are_actionably_rejected(self):
        cases = json.loads((ROOT / "specs/001-business-extraction/examples/counterexamples.json").read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                base = copy.deepcopy(self.candidate if case["base"] == "candidate.json" else self.records)
                parts = case["path"].strip("/").split("/")
                parent = base
                for part in parts[:-1]:
                    parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                parent[int(parts[-1]) if isinstance(parent, list) else parts[-1]] = case["value"]
                if case["base"] == "candidate.json":
                    result = validate_candidate(base, self.document)
                    self.assertIsNone(result["records"])
                    self.assertTrue(result["diagnostics"])
                else:
                    self.assertTrue(validate_records(base, self.document))

    def test_same_explicit_code_merges_without_duplicate_observation(self):
        c = copy.deepcopy(self.candidate)
        original = next(e for e in c["entities"] if e["entity_type"] == "Observation")
        duplicate = copy.deepcopy(original)
        duplicate["local_id"] = "second-mention"
        c["entities"].append(duplicate)
        relation = copy.deepcopy(next(r for r in c["relations"] if r["to_ref"] == original["local_id"]))
        relation["to_ref"] = duplicate["local_id"]
        c["relations"].append(relation)
        result = validate_candidate(c, self.document)
        self.assertIsNotNone(result["records"], result["diagnostics"])
        self.assertEqual(len(result["records"]["observations"]), 3)

    def test_duplicate_code_conflict_is_not_silently_merged(self):
        c = copy.deepcopy(self.candidate)
        duplicate = copy.deepcopy(next(e for e in c["entities"] if e["entity_type"] == "Observation"))
        duplicate["local_id"] = "conflict"
        duplicate["data"]["area_original_value"] = "99"
        c["entities"].append(duplicate)
        self.assertIsNone(validate_candidate(c, self.document)["records"])

    def test_two_parents_rejected(self):
        c = copy.deepcopy(self.candidate)
        relation = copy.deepcopy(next(r for r in c["relations"] if r["relation_type"] == "HAS_DEFECT"))
        relation["from_ref"] = "c2"
        c["relations"].append(relation)
        self.assertIsNone(validate_candidate(c, self.document)["records"])

    def test_wrong_component_relation_with_real_quotes_rejected(self):
        c = copy.deepcopy(self.candidate)
        next(r for r in c["relations"] if r["relation_type"] == "HAS_DEFECT")["from_ref"] = "c2"
        result = validate_candidate(c, self.document)
        self.assertIsNone(result["records"])
        self.assertIn("RELATION_UNSUPPORTED", [d["code"] for d in result["diagnostics"]])

    def test_borrowing_area_from_another_real_row_rejected(self):
        c = copy.deepcopy(self.candidate)
        obs = [e for e in c["entities"] if e["entity_type"] == "Observation"]
        obs[1]["data"]["area_original_value"] = "0.9"
        obs[1]["sources"] = [s for s in obs[1]["sources"] if s["target_field"] != "area_original_value"]
        obs[1]["sources"] += copy.deepcopy([s for s in obs[2]["sources"] if s["target_field"] == "area_original_value"])
        result = validate_candidate(c, self.document)
        self.assertIsNone(result["records"])
        self.assertTrue({"FIELD_ROW_MISMATCH", "FIELD_PARAGRAPH_MISMATCH"} & {d["code"] for d in result["diagnostics"]})

    def test_fabricated_quote_rejected(self):
        c = copy.deepcopy(self.candidate)
        c["entities"][0]["sources"][0]["exact_quote"] = "不存在的桥梁引文"
        self.assertIsNone(validate_candidate(c, self.document)["records"])

    def test_mutated_area_original_and_normalized_still_needs_evidence(self):
        r = copy.deepcopy(self.records)
        r["observations"][0]["area_original_value"] = "999"
        r["observations"][0]["area_value"] = "999"
        self.assertIn("RECORD_FIELD_UNSUPPORTED", [d["code"] for d in validate_records(r, self.document)])

    def test_missing_area_routes_to_pending_not_zero(self):
        c = copy.deepcopy(self.candidate)
        obs = next(e for e in c["entities"] if e["entity_type"] == "Observation")
        obs["data"]["area_original_value"] = None
        result = validate_candidate(c, self.document)
        self.assertIsNotNone(result["records"], result["diagnostics"])
        r = result["records"]["observations"][0]
        self.assertEqual(r["acceptance_status"], "pending_review")
        self.assertIsNone(r["area_value"])

    def test_numeric_substring_area_proof_excludes_count_number(self):
        c = copy.deepcopy(self.candidate)
        obs = next(e for e in c["entities"] if e["entity_type"] == "Observation")
        source = copy.deepcopy(next(s for s in obs["sources"] if s["target_field"] == "description" and "面积" in s["exact_quote"]))
        source["target_field"] = "area_original_value"
        source["exact_quote"] = "0.04"
        obs["sources"] = [s for s in obs["sources"] if s["target_field"] != "area_original_value"] + [source]
        self.assertIsNotNone(validate_candidate(c, self.document)["records"])
        source["exact_quote"] = "1"
        obs["data"]["area_original_value"] = "1"
        self.assertIsNone(validate_candidate(c, self.document)["records"])

    def test_canonical_id_collision_rejected(self):
        c = copy.deepcopy(self.candidate)
        bridge = next(e for e in c["entities"] if e["entity_type"] == "Bridge")
        original = bridge["local_id"]
        bridge["local_id"] = "Observation:O-001"
        for relation in c["relations"]:
            if relation["from_ref"] == original:
                relation["from_ref"] = bridge["local_id"]
        result = validate_candidate(c, self.document)
        self.assertIsNone(result["records"])
        self.assertIn("CANONICAL_ID_COLLISION", [d["code"] for d in result["diagnostics"]])

    def test_negative_statement_cannot_move_to_girder(self):
        c = copy.deepcopy(self.candidate)
        c["statements"][0]["component_ref"] = "c2"
        self.assertIsNone(validate_candidate(c, self.document)["records"])

    def test_fence_decoder_does_not_repair_content(self):
        self.assertEqual(decode_output('```json\n{"a":1}\n```'), ({"a": 1}, "single_json_fence_removed"))
        for text in ['Explanation\n```json\n{"a":1}\n```', '{"a":1,"a":2}', '{"a":NaN}', '```json\n{"a":\n```']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                decode_output(text)

    def test_record_generation_deterministic(self):
        first = validate_candidate(self.candidate, self.document)["records"]
        second = validate_candidate(copy.deepcopy(self.candidate), self.document)["records"]
        self.assertEqual(first, second)

    def test_failed_revalidation_removes_stale_record_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            candidate = copy.deepcopy(self.candidate)
            candidate["source_kind"] = "real"
            write_json(folder / "candidate.json", candidate)
            write_json(folder / "input-document.json", self.document)
            write_json(folder / "records.json", self.records)
            result = subprocess.run([sys.executable, str(HERE / "validate.py"), str(folder)], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse((folder / "records.json").exists())
            self.assertTrue((folder / "candidate.json").exists())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ValidatorTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(HERE / "results/validator-tests.json", {"tests_run": result.testsRun, "existing_counterexamples": 16, "failures": len(result.failures), "errors": len(result.errors), "passed": result.wasSuccessful()})
    raise SystemExit(0 if result.wasSuccessful() else 1)
