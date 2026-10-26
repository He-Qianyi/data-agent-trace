"""Guide checks use only the actual public sample; corrupt copies for negative tests."""
import copy
from unittest.mock import patch
import unittest
from reproduction.common import ROOT,read
from reproduction.verify_presentation import verify


class PresentationTests(unittest.TestCase):
    def test_current_walkthrough_quotes_resolve_to_real_sources(self):
        report=verify()
        self.assertEqual(report['status'],'passed')
        self.assertGreater(report['existing_source_excerpts_checked'],0)
        self.assertEqual(report['new_model_calls'],0)

    def test_unrecorded_walkthrough_quote_is_rejected(self):
        def altered(path):
            value=read(path)
            if str(path).endswith('case-study-sources.json'):
                value=copy.deepcopy(value)
                value['bindings'][0]['excerpt']='test-only invented quote, never a research result'
            return value
        with patch('reproduction.verify_presentation.read',side_effect=altered):
            with self.assertRaisesRegex(ValueError,'Guide excerpt mismatch'):
                verify()

if __name__=='__main__':unittest.main()
