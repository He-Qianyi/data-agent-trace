import copy
import json
import unittest

from pipeline.stages.diagnosis.validation import validate_diagnosis, validate_diagnosis_text


class DiagnosisAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.output = {
            "root_cause_chain": [],
            "critical_decision_points": [{"step_id": "S0001", "decision": "observed"}],
            "correct_strategy": "",
            "stage_labels": [],
            "summary": "证据不足，不能确定根因。",
        }

    def test_extra_fields_preserved_and_ignored(self):
        self.output["unknown_note"] = {"any": [1, None, "value"]}
        self.output["critical_decision_points"][0]["extra"] = True
        before = copy.deepcopy(self.output)
        self.assertTrue(validate_diagnosis(self.output, {"S0001"})["passed"])
        self.assertEqual(self.output, before)

    def test_missing_core_field_still_rejected(self):
        del self.output["summary"]
        self.assertFalse(validate_diagnosis(self.output, {"S0001"})["passed"])

    def test_unknown_step_id_still_rejected(self):
        self.assertFalse(validate_diagnosis(self.output, {"S0002"})["passed"])

    def test_step_ids_not_coerced(self):
        self.assertFalse(validate_diagnosis(self.output, {1})["passed"])

    def test_invalid_json_and_nonobjects_rejected(self):
        for text in ["", "<DSML>read_steps</DSML>", "[]", "null"]:
            with self.subTest(text=text):
                self.assertFalse(validate_diagnosis_text(text, {"S0001"})["passed"])

    def test_empty_findings_allowed(self):
        self.output["critical_decision_points"] = []
        self.assertTrue(validate_diagnosis_text(json.dumps(self.output), set())["passed"])

    def test_unread_evidence_is_separate_from_acceptance(self):
        self.output["critical_decision_points"][0]["evidence_ids"] = ["unread"]
        result = validate_diagnosis(self.output, {"S0001"})
        self.assertTrue(result["passed"])
        self.assertFalse(result["evidence_audit_affects_acceptance"])
        self.assertFalse(result["semantic_correctness_evaluated"])

    def test_upstream_optional_step_id_remains_optional(self):
        del self.output["critical_decision_points"][0]["step_id"]
        self.assertTrue(validate_diagnosis(self.output, {"S0001"})["passed"])

    def test_malformed_container_fails_without_crashing(self):
        self.output["critical_decision_points"] = None
        self.assertFalse(validate_diagnosis(self.output, set())["passed"])


if __name__ == "__main__":
    unittest.main()
