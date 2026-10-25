import json
from pathlib import Path
import tempfile
import unittest

from data_agent_trace.demo import build_demo, run
from data_agent_trace.fact_layer.extract import python_facts
from data_agent_trace.semantic.validate import bind_citation


class EvidenceAndDemoTests(unittest.TestCase):
    def test_repeated_quote_retains_all_candidate_locations(self):
        by_id = {'E1': {'text': 'value value', 'char_start': 7}}
        result = bind_citation({'evidence_id': 'E1', 'quote': 'value'}, by_id)
        self.assertEqual(result['match_count'], 2)
        self.assertEqual([r['char_start'] for r in result['candidate_char_spans']], [7, 13])

    def test_changed_quote_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'quote_not_exact_substring'):
            bind_citation({'evidence_id': 'E1', 'quote': 'VALUE'}, {'E1': {'text': 'value'}})

    def test_static_parser_does_not_execute_file_write(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'never_created.txt'
            code = f"f = open({str(marker)!r}, 'w')\nf.write('do not execute')"
            operations, status = python_facts(code)
            self.assertEqual(status['status'], 'parsed')
            self.assertFalse(marker.exists())
            self.assertTrue(any(op['operation'] == 'data_write_api' for op in operations))
            self.assertTrue(all(op['statement_execution_verified'] is False for op in operations))

    def test_sql_text_is_preserved_without_result_binding(self):
        rows, _ = python_facts("con.execute('SELECT SUM(revenue) FROM orders')")
        self.assertEqual(rows[0]['sql_text'], 'SELECT SUM(revenue) FROM orders')
        self.assertEqual(rows[0]['individual_result_binding'], 'not_recorded_separately')

    def test_illustration_is_explicit_and_citations_are_bound(self):
        processor, result, fixture = build_demo()
        self.assertTrue(fixture['synthetic'])
        self.assertFalse(processor['recorded_code_executed_by_demo'])
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['diagnosis_origin'], 'human_authored_illustration')
        self.assertFalse(result['validation']['diagnostic_accuracy_measured'])
        self.assertEqual(len(result['literal_citations']), len(fixture['evidence']))
        self.assertTrue(all(c['match_count'] == 1 for c in result['literal_citations']))

    def test_demo_writes_reviewable_outputs_and_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'result'
            run(target)
            self.assertEqual({p.name for p in target.iterdir() if not p.name.startswith('._')},
                             {'processor_output.json', 'diagnosis_output.json', 'report.md'})
            with self.assertRaises(FileExistsError):
                run(target)
            result = json.loads((target / 'diagnosis_output.json').read_text())
            self.assertEqual(result['validation']['schema'], 'passed')


if __name__ == '__main__':
    unittest.main()
