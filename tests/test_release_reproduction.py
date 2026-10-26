"""Offline packaging integration tests. Mock outputs are never research results."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from reproduction.common import ROOT,read,save,digest
from reproduction.processor import process
from reproduction.diagnose import diagnose
from reproduction.evaluate import evaluate
from pipeline.stages.trace_processor.fact_layer_v5.build import build
from pipeline.stages.trace_processor.fact_layer_v5.__main__ import main as fact_cli
import test_fact_layer_adapters as adapters_fixture
from test_semantic_layer import task_answer,operation_answer


class MockAnnotationClient:
    def __init__(self,*args,**kwargs):pass
    def ask(self,prompt,data,schema,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
        native=operation_answer(data) if 'target_units' in data else task_answer(data)
        save(directory/'request.json',{'origin':'unit_test_mock','data':data})
        save(directory/'response.json',{'origin':'unit_test_mock','data':native})
        receipt={'returned_model':'deepseek-v4.1-flash-1017','unit_test_mock':True,'real_model_calls':0}
        save(directory/'receipt.json',receipt)
        return native,receipt


class MockDiagnosisClient:
    def __init__(self):self.calls=0;self.messages=[]
    def ask(self,messages,directory):
        self.calls+=1;self.messages=copy.deepcopy(messages)
        Path(directory).mkdir(parents=True,exist_ok=False)
        if self.calls==1:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'mock-call','type':'function','function':
                {'name':'read_steps','arguments':json.dumps({'step_ids':['S0001']})}}]}
            finish='tool_calls'
        else:
            message={'role':'assistant','content':json.dumps({'root_cause_chain':[],
                'critical_decision_points':[],'correct_strategy':'','stage_labels':[],
                'summary':'Unit-test mock only; no semantic conclusion.'})}
            finish='stop'
        save(Path(directory)/'request.json',{'origin':'unit_test_mock','messages':messages})
        save(Path(directory)/'response.json',{'origin':'unit_test_mock','message':message})
        return message,finish,{'unit_test_mock':True,'real_model_calls':0}


class ReleaseReproductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def full_mock_bundle(self):
        helper=adapters_fixture.FactLayerTests();helper.setUp();self.addCleanup(helper.doCleanups)
        row=helper.fixture()
        row.update(benchmark="unit_test_fixture",agent_id="mock_agent",task_id="mock_task")
        facts=self.root/'facts'
        source=self.root/'source_manifest.jsonl';source.write_text(json.dumps(row)+'\n')
        self.assertEqual(fact_cli(['--manifest',str(source),'--output',str(facts)]),0)
        result=process(facts,self.root/'processor',ROOT/'configs/processor/reproduction.json',
                       client_factory=MockAnnotationClient,max_requests=10)
        self.assertEqual(result['status'],'completed')
        return self.root/'processor/final'

    def test_full_facts_annotations_merge_and_native_tool_diagnosis_with_mocks(self):
        bundle=self.full_mock_bundle()
        from pipeline.stages.trace_processor.merge_layer.verify import verify
        self.assertEqual(verify(bundle)['status'],'passed')
        from pipeline.stages.trace_processor.merge_layer.downstream import export
        exported=export(bundle)
        self.assertEqual(len(exported),1)
        self.assertTrue((bundle/'downstream/trace_evidence.duckdb').is_file())
        client=MockDiagnosisClient()
        result=diagnose(bundle,self.root/'diagnosis',ROOT/'configs/diagnosis/reproduction.json',client_factory=lambda:client)
        self.assertEqual(result[0]['status'],'mechanically_passed')
        self.assertEqual(client.calls,2)
        self.assertTrue(any(m['role']=='tool' for m in client.messages))
        self.assertEqual(result[0]['tool_calls'],1)

    def test_extra_fields_and_legacy_schema_mismatch_do_not_gate_new_diagnosis(self):
        # Explicit test mock only; this is not a research model result.
        bundle=self.full_mock_bundle()
        from reproduction.common import load_native
        native=load_native()
        def forbidden(*args,**kwargs):
            raise AssertionError('Legacy strict audit must not run')
        native.validate_diagnosis=forbidden
        class PermissiveMock(MockDiagnosisClient):
            def ask(self,messages,directory):
                message,finish,receipt=super().ask(messages,directory)
                if finish=='stop':
                    data=json.loads(message['content'])
                    data['root_cause_chain']='mock legacy-schema mismatch'
                    data['extra_note']='preserved test-only field'
                    message['content']=json.dumps(data)
                return message,finish,receipt
        with patch('reproduction.diagnose.load_native',return_value=native):
            result=diagnose(bundle,self.root/'permissive',ROOT/'configs/diagnosis/reproduction.json',
                            client_factory=PermissiveMock)
        self.assertEqual(result[0]['status'],'mechanically_passed')
        self.assertFalse(result[0]['legacy_strict_audit_applied'])
        saved=next((self.root/'permissive').rglob('diagnosis.json'))
        self.assertEqual(read(saved)['extra_note'],'preserved test-only field')
        self.assertFalse(any((self.root/'permissive').rglob('strict_schema_and_read_audit.json')))

    def test_diagnosis_budget_exhaustion_is_not_a_success_or_fabricated_output(self):
        bundle=self.full_mock_bundle()
        config=read(ROOT/'configs/diagnosis/reproduction.json');config['max_model_rounds_per_trace']=1
        save(self.root/'config.json',config)
        result=diagnose(bundle,self.root/'limited',self.root/'config.json',client_factory=MockDiagnosisClient)
        self.assertEqual(result[0]['status'],'round_budget_exhausted')
        self.assertFalse(any((self.root/'limited').rglob('diagnosis.json')))

    def test_real_excerpt_uses_only_codetracer_acceptance_and_has_no_accuracy_gold(self):
        r=evaluate(ROOT/'samples/real/agriculture_22_claude',self.root/'evaluation')
        self.assertTrue(r['accepted'])
        self.assertFalse(r['legacy_strict_audit_applied'])
        self.assertNotIn('strict_declared_schema',r)
        self.assertTrue(r['current_structural_acceptance']['passed'])
        self.assertIsNone(r['semantic_evaluation']['root_cause_accuracy'])
        self.assertFalse(r['semantic_evaluation']['independent_gold_available'])
        self.assertEqual(r['new_model_calls'],0)

    def test_modified_real_diagnosis_is_rejected(self):
        source=ROOT/'samples/real/agriculture_22_claude'
        target=self.root/'tampered';shutil.copytree(source,target)
        (target/'diagnosis.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            evaluate(target,self.root/'evaluation')

    def test_live_cli_requires_explicit_network_flag(self):
        from reproduction.diagnose import main
        with patch('sys.argv',['diagnose','--bundle','missing','--output',str(self.root/'out'),'--env-file','missing']):
            with self.assertRaises(SystemExit) as stopped:main()
        self.assertEqual(stopped.exception.code,2)
        self.assertFalse((self.root/'out').exists())

if __name__=='__main__':unittest.main()
