"""Use real published records; corrupt in-memory copies only for negative tests."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from reproduction.common import ROOT,read
from reproduction.verify_research import verify,experiment,processor,unseen,author_spot_checks

ART=ROOT/'research_artifacts'


class ResearchArtifactTests(unittest.TestCase):
    def altered(self,target,change):
        def loader(path):
            value=read(path)
            if Path(path)==target:
                value=copy.deepcopy(value);change(value)
            return value
        return loader

    def test_real_artifact_groups_verify_with_limits_and_negative_outcomes(self):
        r=verify(ART)
        self.assertEqual(r['unseen_ten']['native_acceptance'],6)
        self.assertEqual(r['unseen_ten']['after_recorded_wrapper_extraction'],10)
        self.assertTrue(r['author_spot_checks']['known_count_error_retained'])
        self.assertEqual(r['processor_validation']['historical_traces'],164)
        self.assertEqual(r['processor_validation']['retained'],145)
        self.assertEqual(r['processor_validation']['excluded'],19)
        self.assertFalse(r['processor_validation']['full_corpus_source_audit_rerun'])
        self.assertEqual(r['diagnosis_controls']['candidate_accepted'],0)
        self.assertEqual(r['diagnosis_controls']['candidate_cases'],2)
        self.assertIsNone(r['root_cause_accuracy'])
        self.assertEqual(r['new_model_calls'],0)
        self.assertTrue(r['network_disabled'])

    def test_corrupt_message_prefix_is_detected_against_saved_native_response(self):
        target=ART/'unseen_ten/recorded_run/call_index.json'
        def corrupt(rows):rows[1]['message_sha256'][0]='0'*64
        with patch('reproduction.verify_research.read',side_effect=self.altered(target,corrupt)):
            with self.assertRaisesRegex(ValueError,'message signature prefix'):
                experiment(ART,'unseen_ten/recorded_run')

    def test_formatting_revalidation_cannot_change_payload(self):
        rid='rawio-c05784a0b4925c35b147'
        target=ART/'unseen_ten/format_revalidation/results'/rid/'extraction.json'
        def corrupt(value):value['retained_char_range'][0]+=1
        with patch('reproduction.verify_research.read',side_effect=self.altered(target,corrupt)):
            with self.assertRaisesRegex(ValueError,'exact original substring'):
                unseen(ART)

    def test_duplicate_corpus_identity_cannot_count_as_164_traces(self):
        target=ART/'processor_validation/v5/trace_inventory.json'
        def corrupt(rows):rows[1]['record_id']=rows[0]['record_id']
        with patch('reproduction.verify_research.read',side_effect=self.altered(target,corrupt)):
            with self.assertRaisesRegex(ValueError,'164 trace inventory'):
                processor(ART)

    def test_recorded_semantic_count_error_is_not_silently_repaired(self):
        target=ART/'unseen_ten/author_review/checks.json'
        def corrupt(value):value['1753_immediate_count']['actual_ids'].pop()
        with patch('reproduction.verify_research.read',side_effect=self.altered(target,corrupt)):
            with self.assertRaisesRegex(ValueError,'immediate-rule IDs'):
                author_spot_checks(ART)

if __name__=='__main__':unittest.main()
