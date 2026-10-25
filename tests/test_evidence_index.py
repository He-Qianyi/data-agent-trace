import json
from pathlib import Path
import tempfile
import unittest

from pipeline.stages.evidence_builder.index import EvidenceIndex
from pipeline.stages.trace_processor.fact_layer.sources import digest
from pipeline.stages.trace_processor.semantic_layer.evidence import encode


def put(path,value):path.write_text(json.dumps(value,ensure_ascii=False))


class EvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        rows=[]
        for rid,raw in [('A','说明\n未执行的 SELECT sum(amount) FROM sales;\n'*900),('B','A different private trace with sum(amount)')]:
            unit={'unit_id':'U1','step_id':'S0001','validation':{'status':'with_warnings'},
                'operations':[{'type':'aggregation','phase':'defined_in_code','description':'计划聚合销售金额 sum amount',
                    'details':[],'requirement_links':[{'requirement_id':'R0001','relation':'targets'}]}]}
            evidence={'evidence_id':'E1','step_id':'S0001','role':'argument:code','text':raw,
                'fact_pointer':'/steps/0/action/arguments_raw','char_start':0,'char_end':len(raw),'original_source_ref':None}
            view={'units':[{'unit_id':'U1','evidence_ids':['E1'],'context_evidence_ids':[]}],'evidence':[evidence]}
            put(self.root/(rid+'view.json'),view)
            trace={'facts':{'task':{'instruction':'Compute sales sum'},'steps':[{'step_id':'S0001','model_exchange_ref':'X0001',
                'action':{'tool_name':'Python','arguments_raw':raw,'static_extraction':{'data_operations':[{'operation':'sql_api',
                    'sql_text':'SELECT sum(amount) FROM sales','result_scope':'enclosing_tool_result_only',
                    'individual_result_binding':'not_recorded_separately','statement_execution_verified':False}]}},
                'observation':{'text':'A tool observation, not an individual SQL result'}}],
                'final_response':{'text':'unfinished'},'deliverables':[{'snapshot':{'availability':'present'},'agent_creation_certified':False}],
                'evaluation':{'records':[{'payload':{'score':0},'available_to_agent':False}]}},
                'annotations':{'units':[unit],'task':{'requirements':[{'requirement_id':'R0001','kind':'goal','description':'Compute sales sum'}]}},
                'evidence_catalog':[evidence],'citation_index':[{'annotation_pointer':'/annotations/units/0/operations/0/evidence/0',
                    'unit_id':'U1','status':'unresolved','quote':'a rewritten quote','eligible_as_literal_evidence':False}],
                'provenance':{'annotation_inputs':{'operation_evidence.json':{'bundle_path':rid+'view.json'}}}}
            put(self.root/(rid+'.json'),trace)
            rows.append({'record_id':rid,'trace_path':rid+'.json','trace_sha256':digest(self.root/(rid+'.json'))})
        put(self.root/'validation.json',rows);self.index=EvidenceIndex(self.root)

    def tearDown(self):self.tmp.cleanup()

    def test_trace_scoping_and_unknown_scope(self):
        result=self.index.search('A','sum amount')
        self.assertTrue(result['hits'])
        self.assertTrue(all(h['record_id']=='A' for h in result['hits']))
        self.assertEqual(self.index.search('unknown','sum amount')['hits'],[])

    def test_unicode_pagination_and_no_silent_truncation(self):
        page=self.index.evidence_page('A','E1',2,13)
        self.assertEqual(page['text'],self.index.catalogs['A']['E1']['text'][2:15])
        self.assertEqual(page['field_char_start'],2)
        packet=self.index.package('A','sum amount',top_k=1,budget_bytes=4000,page_characters=100)
        self.assertLessEqual(len(encode(packet).encode()),4000)
        self.assertTrue(packet['deferred_pages'])
        self.assertFalse(packet['retrieval_complete_for_candidates'])
        for p in packet['raw_pages']:
            self.assertEqual(p['text'],self.index.catalogs['A']['E1']['text'][p['block_char_start']:p['block_char_end']])

    def test_no_execution_or_satisfaction_promoted_from_navigation(self):
        hit=self.index.search('A','计划聚合')['hits'][0]
        self.assertEqual(hit['operations'][0]['phase'],'defined_in_code')
        self.assertFalse(hit['semantic_correctness_certified'])
        self.assertFalse(self.index.requirement('A','R0001')['links'][0]['satisfaction_certified'])

    def test_invalid_quote_not_repaired_after_successful_search(self):
        self.index.search('A','sum amount')
        c=self.index.citation('A','/annotations/units/0/operations/0/evidence/0')
        self.assertEqual(c['quote'],'a rewritten quote')
        self.assertFalse(c['eligible_as_literal_evidence'])

    def test_sql_return_scope_and_unrecorded_dataframe_state(self):
        result=self.index.sql_result_binding('A','S0001')
        self.assertEqual(result['individual_result_binding'],'not_recorded_separately')
        self.assertFalse(result['statement_execution_verified'])
        state=self.index.runtime_variable('A','S0001','df')
        self.assertIsNone(state['value'])
        self.assertFalse(state['value_inferred_from_code'])

    def test_artifact_presence_and_evaluation_do_not_prove_agent_execution(self):
        outcome=self.index.outcome('A')
        self.assertFalse(outcome['deliverables'][0]['value']['agent_creation_certified'])
        self.assertTrue(outcome['post_run_snapshots_and_evaluation_not_agent_time_knowledge'])
        self.assertFalse(self.index.neighbors('A','S0001')['causal_dependency_certified'])

    def test_hash_changed_source_trace_rejected(self):
        p=self.root/'A.json';p.write_text(p.read_text()+' ')
        with self.assertRaises(ValueError):EvidenceIndex(self.root)


if __name__=='__main__':unittest.main()
