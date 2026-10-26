"""Regression tests use complete REAL saved records; no fabricated research output."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch
from reproduction.common import ROOT,read,save,digest
from reproduction.replay import replay,verify_sample,DEFAULT_SAMPLE,diagnosis_replay


class CompleteRealReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def copy_sample(self):
        dest=self.root/'sample';shutil.copytree(DEFAULT_SAMPLE,dest,ignore=shutil.ignore_patterns('._*'))
        return dest

    def rehash(self,sample):
        manifest=read(sample/'manifest.json')
        for row in manifest['files']:row['sha256']=digest(sample/row['path'])
        save(sample/'manifest.json',manifest)

    def test_complete_real_trace_replays_and_preserves_semantic_limitation(self):
        from reproduction.common import load_native
        native=load_native()
        native.validate_diagnosis=Mock(side_effect=AssertionError('Legacy strict audit must not run'))
        with patch('reproduction.replay.load_native',return_value=native):
            result=replay(DEFAULT_SAMPLE,self.root/'result')
        native.validate_diagnosis.assert_not_called()
        self.assertEqual(result['processor']['model_exchanges'],9)
        self.assertEqual(result['processor']['tool_steps'],8)
        self.assertEqual(result['processor']['semantic_units'],9)
        self.assertEqual(result['processor']['recorded_annotation_responses_replayed'],4)
        self.assertEqual(result['diagnosis']['recorded_model_rounds_replayed'],4)
        self.assertEqual(result['diagnosis']['native_tool_calls_reexecuted'],5)
        self.assertFalse(result['diagnosis']['legacy_strict_audit_applied'])
        self.assertNotIn('strict_schema_and_read_audit',result['diagnosis'])
        self.assertTrue(result['diagnosis']['current_structural_acceptance']['passed'])
        self.assertTrue(all(c['exists'] and c['actually_read'] for c in result['diagnosis']['citation_read_audit']))
        self.assertEqual(result['semantic_evaluation']['author_review_status'],'needs_revision')
        self.assertIsNone(result['semantic_evaluation']['root_cause_accuracy'])
        self.assertEqual(result['new_model_calls'],0)
        self.assertTrue(result['network_disabled'])
        self.assertEqual(digest(self.root/'result/diagnosis/diagnosis.json'),digest(DEFAULT_SAMPLE/'recorded_diagnosis/diagnosis.json'))

    def test_tampered_raw_recording_fails_before_output_is_created(self):
        sample=self.copy_sample();p=sample/'raw/attempt-001/turn_model_io.jsonl'
        p.write_text(p.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'Sample hash mismatch'):
            replay(sample,self.root/'result')
        self.assertFalse((self.root/'result').exists())

    def test_rehashed_but_changed_model_context_is_rejected(self):
        sample=self.copy_sample();p=sample/'recorded_diagnosis/calls/02/request.json'
        request=read(p);request['messages'][1]['content']+=' changed evidence'
        save(p,request)
        provenance=read(sample/'provenance.json')
        next(r for r in provenance['source_files'] if r['path']==str(p.relative_to(sample)))['published_sha256']=digest(p)
        save(sample/'provenance.json',provenance);self.rehash(sample)
        with self.assertRaisesRegex(ValueError,'diagnosis round 2 entire message chain'):
            replay(sample,self.root/'result')
        self.assertFalse((self.root/'result/diagnosis/diagnosis.json').exists())

    def test_changed_historical_search_statistics_cannot_silently_change_context(self):
        sample=self.copy_sample();p=sample/'recorded_diagnosis/search_statistics.json'
        stats=read(p);stats['document_count']+=10;save(p,stats);self.rehash(sample)
        with self.assertRaisesRegex(ValueError,'complete native tool result 4'):
            replay(sample,self.root/'result')
        self.assertFalse((self.root/'result/diagnosis/diagnosis.json').exists())

if __name__=='__main__':unittest.main()
