"""General synthetic cases; no cohort task names, values, services or execution."""
import unittest
from pipeline.stages.trace_processor.fact_layer_v4.extract import python_facts


def ops(code, kind):
    return [o for o in python_facts(code)[0] if o['operation'] == kind]


class ConstantAndConnectionTests(unittest.TestCase):
    def test_reassignment_resolves_each_read_in_order(self):
        rows = ops("p='alpha.csv'\na= pd.read_csv(p)\np='beta.csv'\nb=pd.read_csv(p)", 'data_read_api')
        self.assertEqual([r['path_literal'] for r in rows], ['alpha.csv', 'beta.csv'])

    def test_constant_subquery_and_string_interpolation(self):
        row = ops("part='SELECT k FROM source'\nc.execute(f'SELECT * FROM target WHERE k IN ({part})')", 'sql_api')[0]
        self.assertEqual(row['sql_text'], 'SELECT * FROM target WHERE k IN (SELECT k FROM source)')
        self.assertEqual(row['sql_expression'], "f'SELECT * FROM target WHERE k IN ({part})'")
        self.assertFalse(row['statement_execution_verified'])

    def test_imported_join_and_alias_and_export(self):
        for prefix, expr in [('import os', 'os.path.join'), ('import os as fs', 'fs.path.join'),
                             ('from os.path import join as assemble', 'assemble'), ('import posixpath as pp', 'pp.join')]:
            row = ops(prefix+"\nbase='/mnt/Example Folder'\np="+expr+"(base, 'result.csv')\ndf.to_csv(p,index=False)", 'data_write_api')[0]
            self.assertEqual(row['path_literal'], '/mnt/Example Folder/result.csv')
            self.assertFalse(row['statement_execution_verified'])

    def test_unknown_reassignment_stays_unknown(self):
        r=ops("p='old.csv'\np=choose_runtime_path()\npd.read_csv(p)", 'data_read_api')[0]
        self.assertIsNone(r['path_literal'])

    def test_branch_and_loop_assignments_do_not_supply_values(self):
        for code in ["p='old.csv'\nif flag:\n p='new.csv'\npd.read_csv(p)",
                     "for p in paths:\n pd.read_csv(f'{p}/file.csv')"]:
            self.assertIsNone(ops(code,'data_read_api')[0]['path_literal'])

    def test_local_and_deferred_scope_is_not_module_constant_expansion(self):
        for code in ["p='module'\ndef f(p):\n return pd.read_csv(f'{p}/file.csv')",
                     "p='module'\nxs=[pd.read_csv(f'{p}/file.csv') for p in paths]",
                     "p='module'\nf=lambda p: pd.read_csv(f'{p}/file.csv')"]:
            self.assertIsNone(ops(code,'data_read_api')[0]['path_literal'])

    def test_format_specs_conversions_and_runtime_values_remain_unknown(self):
        for expr in ["f'{base!r}/file.csv'", "f'{base:>12}/file.csv'", "f'{runtime}/file.csv'", "f'{getpath()}/file.csv'"]:
            row=ops("base='x'\npd.read_csv("+expr+")",'data_read_api')[0]
            self.assertIsNone(row['path_literal'])

    def test_unimported_or_shadowed_join_is_not_executed_or_guessed(self):
        for prefix in ['', 'import os\nos=custom_object()\n']:
            row=ops(prefix+"pd.read_csv(os.path.join('/root', 'x.csv'))",'data_read_api')[0]
            self.assertIsNone(row['path_literal'])

    def test_reflective_mutation_and_augmented_assignment_invalidate(self):
        for mid in ["exec(source)","p += suffix", "del p"]:
            row=ops("p='x'\n"+mid+"\npd.read_csv(f'{p}/a.csv')",'data_read_api')[0]
            self.assertIsNone(row['path_literal'])

    def test_path_and_sql_literal_parameters_are_not_interpolated_at_runtime(self):
        row=ops("table='measurements'\nc.execute(f'SELECT * FROM {table} WHERE key = %s', values)",'sql_api')[0]
        self.assertEqual(row['sql_text'],'SELECT * FROM measurements WHERE key = %s')
        self.assertEqual(row['parameters_expression'],'values')
        self.assertFalse(row['parameter_values_evaluated'])

    def test_keyword_database_connection_and_exact_unknown_expression(self):
        row=ops("import psycopg2 as db\nconn=db.connect(host='localhost', port=port, dbname=database, user='reader')",'connection_api')[0]
        self.assertIsNone(row['path_literal']);self.assertEqual(row['connection_driver_as_written'],'psycopg2')
        self.assertEqual(row['options_literal']['host'],'localhost');self.assertIsNone(row['options_literal']['port'])
        self.assertEqual(row['connection_parameters_as_written']['dbname'],'database');self.assertFalse(row['engine_verified'])

    def test_database_call_inside_function_is_syntax_not_execution(self):
        row=ops("import psycopg2\ndef query(dbname):\n return psycopg2.connect(dbname=dbname)",'connection_api')[0]
        self.assertIsNone(row['options_literal']['dbname']);self.assertFalse(row['statement_execution_verified'])

    def test_database_driver_alias_shadowing_does_not_create_connection(self):
        for code in ["import psycopg2\ndef f(psycopg2):\n return psycopg2.connect(dbname='x')",
                     "import psycopg2\npsycopg2=custom()\npsycopg2.connect(dbname='x')",
                     "import socket\nsocket.connect(host='localhost')"]:
            self.assertEqual(ops(code,'connection_api'),[])

    def test_keyword_sqlite_database_can_be_a_recorded_path(self):
        row=ops("import sqlite3\nc=sqlite3.connect(database='/tmp/example.db')",'connection_api')[0]
        self.assertEqual(row['path_literal'],'/tmp/example.db')

    def test_imported_connect_and_literal_import_expression(self):
        for code in ["from psycopg2 import connect as attach\nattach(dbname='example')",
                     "__import__('psycopg2').connect(dbname='example')"]:
            rows=ops(code,'connection_api')
            # Imported functions may be aliased, their source spelling is retained.
            self.assertEqual(len(rows),1)

    def test_unimported_canonical_driver_is_only_recorded_syntax(self):
        row=ops("psycopg2.connect(dbname='example')",'connection_api')[0]
        self.assertEqual(row['connection_identity_scope'],'lexical_driver_spelling_not_runtime_binding')
        self.assertFalse(row['engine_verified']);self.assertFalse(row['statement_execution_verified'])

    def test_expanded_keywords_and_absent_dsn_are_not_invented(self):
        row=ops("import psycopg2\npsycopg2.connect(**config)",'connection_api')[0]
        self.assertEqual(row['expanded_keyword_arguments_as_written'],['config']);self.assertIsNone(row['path_literal'])
        self.assertEqual(row['options_literal'],{})


if __name__=='__main__': unittest.main()
