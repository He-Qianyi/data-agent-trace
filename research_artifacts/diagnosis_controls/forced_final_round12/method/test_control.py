"""Boundary and evidence-leakage checks before the paired API request."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('control', Path(__file__).with_name('run.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TerminationControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = m.read(m.ROOT / 'plan.json')
        cls.job = next(j for j in cls.plan['jobs'] if j['treatment'] == 'paired_final_round_tool_choice_none')
        cls.path = m.ROOT / 'results' / cls.job['record_id']
        cls.control = m.read(cls.path / 'control_request.json')

    def test_before_deadline_is_unchanged(self):
        for round_no in [1, 10, 11]:
            self.assertEqual(m.termination_payload(self.control, round_no), self.control)

    def test_final_round_changes_only_tool_choice(self):
        treated = m.termination_payload(self.control, 12)
        self.assertEqual(treated['tool_choice'], 'none')
        self.assertEqual([k for k in treated if treated[k] != self.control[k]], ['tool_choice'])
        self.assertEqual(treated['messages'], self.control['messages'])
        self.assertEqual(treated['tools'], self.control['tools'])
        self.assertEqual(treated['max_tokens'], 48000)
        self.assertEqual(treated['thinking_token_budget'], 16000)

    def test_no_extra_round(self):
        for round_no in [0, 13, True]:
            with self.assertRaises(ValueError):
                m.termination_payload(self.control, round_no)

    def test_final_round_observations_do_not_leak(self):
        index = m.baseline.EvidenceIndex(m.BUNDLE)
        safe, reads = m.restore_prefix(index, self.job['record_id'], 12)
        self.assertEqual(len(reads), 24)
        self.assertTrue(all(r['round'] < 12 for r in reads))
        later = [m.read(p) for p in (m.OLD / 'results' / self.job['record_id'] / 'tool_reads').glob('*.json')
                 if m.read(p)['round'] == 12]
        later_id = later[0]['arguments']['evidence_id']
        self.assertNotIn(later_id, safe.read_ranges)
        sid = index.catalogs[self.job['record_id']][later_id]['step_id']
        candidate = {'root_cause_chain': [], 'critical_decision_points': [
            {'step_id': sid, 'decision': 'test', 'reasoning': 'test', 'should_have': 'test',
             'evidence_ids': [later_id]}], 'correct_strategy': '', 'stage_labels': [], 'summary': 'test'}
        checked = m.baseline.validate_diagnosis(candidate, safe)
        self.assertFalse(checked['passed'])
        self.assertEqual(checked['errors'][0]['kind'], 'evidence_not_actually_read')

    def test_early_terminal_outputs_are_identical(self):
        count = 0
        for job in self.plan['jobs']:
            if job['treatment'] != 'unchanged_early_terminal_reuse':
                continue
            rid = job['record_id']
            self.assertEqual(m.sha(m.ROOT / 'results' / rid / 'diagnosis.json'),
                             m.sha(m.OLD / 'results' / rid / 'diagnosis.json'))
            count += 1
        self.assertEqual(count, 5)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TerminationControlTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    m.save(m.ROOT / 'tests.json', {'tests': result.testsRun, 'failures': len(result.failures),
                                 'errors': len(result.errors), 'passed': result.wasSuccessful()})
    raise SystemExit(0 if result.wasSuccessful() else 1)
