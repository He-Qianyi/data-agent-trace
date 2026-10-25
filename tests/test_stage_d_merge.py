"""Regression checks for evidence qualification and portable immutable sources."""
import copy
import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

from pipeline.stages.trace_processor.merge_layer.citations import locate, operation_issues
from pipeline.stages.trace_processor.merge_layer.build import Bundle, bundled_sources, validated_task
from pipeline.stages.trace_processor.fact_layer.sources import Sources


class StageDTests(unittest.TestCase):
    def test_cosmetic_mismatch_is_retained_but_not_bound(self):
        citation = {"evidence_id": "E1", "quote": "Crop   42"}
        before = copy.deepcopy(citation)
        result = locate(citation, {"E1": {"text": "Crop    42"}}, {"E1"})
        self.assertEqual(citation, before)
        self.assertEqual(result["quote"], before["quote"])
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["block_char_spans"], [])

    def test_exact_quote_in_other_evidence_is_not_moved(self):
        result = locate({"evidence_id": "E1", "quote": "measured cost"},
                        {"E1": {"text": "planned cost"}, "E2": {"text": "measured cost"}}, {"E1"})
        self.assertFalse(result["eligible_as_literal_evidence"])
        self.assertEqual(result["evidence_id"], "E1")

    def test_exact_out_of_scope_quote_is_quarantined(self):
        result = locate({"evidence_id": "E2", "quote": "value"}, {"E2": {"text": "value"}}, {"E1"})
        self.assertIn("out_of_unit_scope", result["issues"])
        self.assertFalse(result["eligible_as_literal_evidence"])

    def test_repeated_quote_keeps_every_candidate_and_unicode_offsets(self):
        result = locate({"evidence_id": "E1", "quote": "值"},
                        {"E1": {"text": "值 = 值", "char_start": 30}}, {"E1"})
        self.assertEqual(result["status"], "exact_multiple")
        self.assertEqual(result["field_char_spans"], [{"start": 30, "end": 31}, {"start": 34, "end": 35}])
        self.assertFalse(result["semantic_entailment_verified"])

    def test_defined_code_cannot_supply_observation_and_unknown_requirement(self):
        op = {"phase": "observed_in_result", "evidence": [{"evidence_id": "E1"}],
              "requirement_links": [{"requirement_id": "R2"}]}
        issues = operation_issues(op, {"evidence_ids": ["E1"]}, {"E1": {"role": "argument:code"}}, {"R1"})
        self.assertIn("observed_phase_without_local_observation", issues)
        self.assertIn("unknown_requirement_id:R2", issues)

    def test_unknown_evidence_does_not_raise_or_become_a_fact(self):
        result = locate({"evidence_id": "invented", "quote": "claim"}, {}, {"E1"})
        self.assertEqual(result["status"], "unresolved")
        self.assertIn("unknown_evidence_id", result["issues"])

    def test_portable_source_works_after_original_is_removed_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "source.jsonl"
            original.write_text('{"request":"complete input", "response":"complete output"}\n')
            bundle = Bundle(root / "bundle")
            record = bundle.add(original)
            original.unlink()
            source = root / "bundle" / record["bundle_path"]
            entries = [{"source_id": "S1", "path": str(source), "roles": ["model_io"], "exists": True, "sha256": record["sha256"]}]
            registry = Sources.from_entries(entries)
            self.assertEqual(registry.resolve({"source_id": "S1", "line": 1, "json_pointer": "/request"}), "complete input")
            source.write_text('{"request":"altered"}\n')
            with self.assertRaises(ValueError):
                Sources.from_entries(entries)

    def test_bundle_root_may_be_relative_without_changing_source_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "io.jsonl"
            original.write_text('{"response":"recorded"}\n')
            record = Bundle(root / "delivery").add(original)
            trace = {"facts": {"sources": [{"source_id": "S7", "path": str(original), "roles": ["model_io"],
                        "exists": True, "sha256": record["sha256"]}]},
                     "provenance": {"source_files": [{"source_id": "S7", **record}]}}
            original.unlink()
            registry = bundled_sources(trace, os.path.relpath(root / "delivery"))
            self.assertEqual(registry.resolve({"source_id": "S7", "line": 1, "json_pointer": "/response"}), "recorded")

    def test_existing_review_requires_matching_before_value_and_preserves_native(self):
        native={"original":"immutable"};before=copy.deepcopy(native)
        accepted={"requirements":[{"requirement_id":"R0001","description":"before"}]}
        review={"authorization":"Previously requested correction", "requirement_id":"R0001", "before":"before", "after":"after",
                "annotation_patch":[{"op":"replace","path":"/requirements/0/description","value":"after"}]}
        with patch('pipeline.stages.trace_processor.merge_layer.build.validate_requirements',return_value=copy.deepcopy(accepted)):
            self.assertEqual(validated_task(native,{},review)['requirements'][0]['description'],'after')
        self.assertEqual(native,before)
        review['before']='different'
        with patch('pipeline.stages.trace_processor.merge_layer.build.validate_requirements',return_value=copy.deepcopy(accepted)):
            with self.assertRaises(ValueError):validated_task(native,{},review)

    def test_existing_review_cannot_change_quotes_or_ids(self):
        review={"authorization":"Historical record", "annotation_patch":[{"op":"replace","path":"/requirements/0/evidence/0/quote","value":"invented"}]}
        with patch('pipeline.stages.trace_processor.merge_layer.build.validate_requirements',return_value={}):
            with self.assertRaises(ValueError):validated_task({}, {}, review)


if __name__ == "__main__":
    unittest.main()
