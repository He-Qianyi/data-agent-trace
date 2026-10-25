"""Synthetic positional database arguments and conservative unknowns."""
import unittest
from data_agent_trace.fact_layer.extract import python_facts


def connections(code):
    return [x for x in python_facts(code)[0] if x['operation']=='connection_api']


class PositionalConnectionTests(unittest.TestCase):
    def test_constant_uri_retains_prefix_query_and_exact_expression(self):
        row=connections("import sqlite3\np='example.db'\nc=sqlite3.connect(f'file:{p}?mode=ro&cache=shared', uri=True)")[0]
        self.assertEqual(row['path_literal'],'file:example.db?mode=ro&cache=shared')
        self.assertEqual(row['constant_resolution']['path_literal']['expression'],"f'file:{p}?mode=ro&cache=shared'")
        self.assertEqual(row['connection_parameters_as_written'],{'uri':'True'})
        self.assertEqual(row['options_literal'],{'uri':True})
        self.assertFalse(row['engine_verified']);self.assertFalse(row['statement_execution_verified'])

    def test_imported_alias_and_path_join_use_generic_constant_rules(self):
        code="import os as fs\nimport duckdb as db\nroot='/mnt/Example Space'\np=fs.path.join(root, 'store.db')\nc=db.connect(p,read_only=True)"
        row=connections(code)[0];self.assertEqual(row['path_literal'],'/mnt/Example Space/store.db')
        self.assertEqual(row['options_literal'],{'read_only':True})

    def test_reassignment_resolves_each_connection_at_its_source_position(self):
        rows=connections("import sqlite3\np='one.db'\na=sqlite3.connect(f'file:{p}?mode=ro',uri=True)\np='two.db'\nb=sqlite3.connect(f'file:{p}?mode=ro',uri=True)")
        self.assertEqual([r['path_literal'] for r in rows],['file:one.db?mode=ro','file:two.db?mode=ro'])

    def test_postgresql_dsn_uses_same_string_rule_without_execution(self):
        row=connections("import psycopg2\nname='sample'\nc=psycopg2.connect(f'dbname={name} connect_timeout=3')")[0]
        self.assertEqual(row['path_literal'],'dbname=sample connect_timeout=3');self.assertFalse(row['engine_verified'])

    def test_dynamic_positional_argument_does_not_fall_back_to_keyword(self):
        row=connections("import sqlite3\nc=sqlite3.connect(runtime_path,database='fallback.db')")[0]
        self.assertIsNone(row['path_literal']);self.assertEqual(row['options_literal']['database'],'fallback.db')

    def test_local_branch_loop_and_unknown_reassignment_do_not_guess_uri(self):
        for code in ["import sqlite3\np='old'\np=lookup()\nc=sqlite3.connect(f'file:{p}',uri=True)",
                     "import sqlite3\np='old'\nif condition:\n p='new'\nc=sqlite3.connect(f'file:{p}',uri=True)",
                     "import sqlite3\nfor p in paths:\n c=sqlite3.connect(f'file:{p}',uri=True)",
                     "import sqlite3\np='module'\ndef f(p):\n return sqlite3.connect(f'file:{p}',uri=True)"]:
            self.assertIsNone(connections(code)[0]['path_literal'])

    def test_format_specs_and_conversions_remain_unknown(self):
        for expression in ["f'file:{p!r}'", "f'file:{p:>16}'"]:
            self.assertIsNone(connections("import sqlite3\np='known'\nc=sqlite3.connect("+expression+",uri=True)")[0]['path_literal'])

    def test_keyword_only_dbname_and_literal_database_behavior_unchanged(self):
        self.assertIsNone(connections("import psycopg2\nc=psycopg2.connect(dbname='sample')")[0]['path_literal'])
        self.assertEqual(connections("import sqlite3\nc=sqlite3.connect(database='sample.db')")[0]['path_literal'],'sample.db')

    def test_uri_percent_encoding_unicode_and_parameters_are_not_rewritten(self):
        row=connections("import sqlite3\np='資料%20folder/db.sqlite'\nc=sqlite3.connect(f'file:{p}?mode=ro&immutable=1',uri=True)")[0]
        self.assertEqual(row['path_literal'],'file:資料%20folder/db.sqlite?mode=ro&immutable=1')


if __name__=='__main__':unittest.main()
