"""Fault-injection software tests only. All provider outputs here are explicit mocks."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from reproduction.common import ROOT,read,save,digest,load_native
from reproduction.run_state import Journal,RunStopped,atomic_save
from reproduction.processor import process
from reproduction.diagnose import diagnose
import test_release_reproduction as fixtures
from test_release_reproduction import MockAnnotationClient

CFG=ROOT/'configs/diagnosis/reproduction.json'


class RecordingMock:
    """Match the native wire shape to exercise recovery without any network call."""
    def __init__(self,log,mode='normal',two_tools=False):
        self.log,self.mode,self.two_tools=log,mode,two_tools
    def ask(self,messages,directory):
        self.log.append(copy.deepcopy(messages));folder=Path(directory);folder.mkdir(parents=True)
        native=load_native();cfg=read(CFG)
        request={'model':cfg['model'],'messages':messages,'tools':native.TOOLS,'tool_choice':'auto',
                 'max_tokens':cfg['max_tokens'],'thinking_token_budget':cfg['thinking_token_budget'],'temperature':0}
        save(folder/'request.json',request)
        has_tools=any(m['role']=='tool' for m in messages)
        if self.mode=='network_failure' and has_tools:raise OSError('unit-test network interruption')
        if not has_tools:
            calls=[{'id':'mock-call-1','type':'function','function':{'name':'read_steps','arguments':json.dumps({'step_ids':['S0001']})}}]
            if self.two_tools:calls.append({'id':'mock-call-2','type':'function','function':{'name':'read_steps','arguments':json.dumps({'step_ids':['S0001']})}})
            message={'role':'assistant','content':None,'tool_calls':calls};finish='tool_calls'
        else:
            data={'root_cause_chain':[],'critical_decision_points':[],'correct_strategy':'',
                  'stage_labels':[],'summary':'Explicit unit-test mock, not a research result.','extra_field':'accepted unchanged'}
            message={'role':'assistant','content':'not JSON' if self.mode=='invalid_output' else json.dumps(data)};finish='stop'
        response={'model':cfg['expected_returned_model'],'choices':[{'message':message,'finish_reason':finish}]}
        receipt={'returned_model':response['model'],'request_sha256':native.signature(request),
                 'unit_test_mock':True,'real_model_calls':0}
        save(folder/'response.json',response);save(folder/'receipt.json',receipt)
        if self.mode=='interrupt_after_saved_response' and not has_tools:
            raise KeyboardInterrupt('unit-test crash after complete transport was saved')
        return message,finish,receipt


class ResumableRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        helper=fixtures.ReleaseReproductionTests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.bundle=helper.full_mock_bundle();self.facts=helper.root/'facts'
    def config(self,rounds=3,tools=60,name='config.json'):
        cfg=read(CFG);cfg.update(max_model_rounds_per_trace=rounds,max_tool_calls_per_trace=tools)
        p=self.root/name;save(p,cfg);return p
    def journal_hashes(self,out):
        return {str(p.relative_to(out)):digest(p) for p in (out/'.resume/calls').rglob('*.json')
                if not p.name.startswith('._')}

    def test_processor_budget_resume_reuses_successful_annotations(self):
        out=self.root/'processor';calls=[]
        class Client(MockAnnotationClient):
            def ask(self,*args):calls.append(1);return super().ask(*args)
        with self.assertRaises(RunStopped) as stopped:
            process(self.facts,out,ROOT/'configs/processor/reproduction.json',max_requests=1,client_factory=Client)
        self.assertEqual(stopped.exception.status,'budget_exhausted');self.assertEqual(len(calls),1)
        before=self.journal_hashes(out)
        result=process(self.facts,out,ROOT/'configs/processor/reproduction.json',max_requests=10,client_factory=Client,resume=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(calls),2)
        self.assertEqual(result['requests_attempted'],1);self.assertEqual(result['reused_model_responses'],1)
        self.assertTrue(all(digest(out/p)==h for p,h in before.items()))
        def forbidden(*args,**kwargs):raise AssertionError('Completed resume must not initialize a provider')
        result=process(self.facts,out,ROOT/'configs/processor/reproduction.json',client_factory=forbidden,resume=True)
        self.assertTrue(result['completed_run_reused']);self.assertEqual(result['new_request_attempts'],0)

    def test_processor_recovers_complete_transport_before_checkpoint_commit(self):
        from pipeline.stages.trace_processor.semantic_layer.evidence import encode,text_hash
        out=self.root/'processor_transport';log=[];profile=read(ROOT/'configs/processor/reproduction.json')
        class Client(MockAnnotationClient):
            def ask(self,prompt,data,schema,directory):
                value,receipt=super().ask(prompt,data,schema,directory);log.append(1)
                request={'model':profile['model'],'messages':[
                    {'role':'system','content':prompt+'\nOUTPUT JSON SCHEMA (structural contract):\n'+encode(schema)},
                    {'role':'user','content':'UNTRUSTED ANNOTATION INPUT DATA:\n'+encode(data)+'\nApply the system contract and return JSON only.'}],
                    'max_tokens':profile['max_tokens'],'temperature':0,'response_format':{'type':'json_object'},
                    'thinking_token_budget':profile['thinking_token_budget']}
                response={'model':profile['expected_returned_model'],'choices':[{'message':{'content':encode(value)},'finish_reason':'stop'}]}
                receipt.update(request_sha256=text_hash(encode(request)),response_sha256=text_hash(encode(response)))
                save(Path(directory)/'request.json',request);save(Path(directory)/'response.json',response);save(Path(directory)/'receipt.json',receipt)
                if len(log)==1:raise KeyboardInterrupt()
                return value,receipt
        with self.assertRaises(RunStopped):process(self.facts,out,ROOT/'configs/processor/reproduction.json',client_factory=Client)
        result=process(self.facts,out,ROOT/'configs/processor/reproduction.json',client_factory=Client,resume=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(log),2)
        self.assertEqual(result['recovered_saved_responses'],1)

    def test_processor_recovers_after_interrupted_merge_without_new_calls(self):
        out=self.root/'processor_merge';calls=[]
        class Client(MockAnnotationClient):
            def ask(self,*args):calls.append(1);return super().ask(*args)
        def interrupted(rows,jobs,output,*args):
            (output/'final').mkdir();(output/'final/partial.txt').write_text('test-only partial derived output')
            raise KeyboardInterrupt()
        with patch('reproduction.processor._merge',side_effect=interrupted):
            with self.assertRaises(RunStopped):process(self.facts,out,ROOT/'configs/processor/reproduction.json',client_factory=Client)
        old_calls=len(calls)
        result=process(self.facts,out,ROOT/'configs/processor/reproduction.json',client_factory=Client,resume=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(calls),old_calls)
        self.assertTrue(any((out/'.resume/derived').rglob('partial.txt')))

    def test_diagnosis_round_budget_resume_replays_prefix_and_only_requests_tail(self):
        out=self.root/'diagnosis';log=[]
        first=diagnose(self.bundle,out,self.config(rounds=1),client_factory=lambda:RecordingMock(log))
        self.assertEqual(first[0]['status'],'round_budget_exhausted');self.assertEqual(len(log),1)
        checkpoint=self.journal_hashes(out)
        result=diagnose(self.bundle,out,self.config(rounds=3,name='more-rounds.json'),
                        client_factory=lambda:RecordingMock(log),resume=True)
        self.assertEqual(result[0]['status'],'mechanically_passed');self.assertEqual(len(log),2)
        self.assertTrue(any(m['role']=='tool' for m in log[-1]))
        self.assertEqual(read(out/'run_summary.json')['exit_code'],0)
        self.assertTrue(all(digest(out/p)==h for p,h in checkpoint.items()))
        def forbidden():raise AssertionError('No new call is needed')
        result=diagnose(self.bundle,out,self.config(),client_factory=forbidden,resume=True)
        self.assertEqual(result[0]['status'],'mechanically_passed')
        self.assertEqual(read(out/'run_summary.json')['new_request_attempts'],0)
        self.assertEqual(read(out/'run_summary.json')['reused_model_responses'],2)

    def test_diagnosis_tool_budget_resume_reconstructs_partial_tool_round(self):
        out=self.root/'tool_limit';log=[]
        result=diagnose(self.bundle,out,self.config(tools=1),client_factory=lambda:RecordingMock(log,two_tools=True))
        self.assertEqual(result[0]['status'],'tool_budget_exhausted')
        result=diagnose(self.bundle,out,self.config(tools=2,name='more-tools.json'),
                        client_factory=lambda:RecordingMock(log,two_tools=True),resume=True)
        self.assertEqual(result[0]['status'],'mechanically_passed');self.assertEqual(result[0]['tool_calls'],2)
        self.assertEqual(len(log),2)

    def test_complete_saved_transport_recovers_after_interruption_without_resending(self):
        out=self.root/'saved_transport';log=[];config=self.config()
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log,'interrupt_after_saved_response'))
        self.assertEqual(result[0]['status'],'paused');self.assertEqual(len(log),1)
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log),resume=True)
        self.assertEqual(result[0]['status'],'mechanically_passed');self.assertEqual(len(log),2)
        summary=read(out/'run_summary.json')
        self.assertEqual(summary['recovered_saved_responses'],1);self.assertEqual(summary['new_request_attempts'],1)

    def test_uncertain_request_requires_explicit_retry_and_retains_old_attempt(self):
        out=self.root/'uncertain';log=[];config=self.config()
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log,'network_failure'))
        self.assertEqual(result[0]['status'],'provider_failed');self.assertEqual(len(log),2)
        old=[(p,digest(p)) for p in (out/'.resume/calls').rglob('request.json')]
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log),resume=True)
        self.assertEqual(result[0]['status'],'incomplete_call');self.assertEqual(len(log),2)
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log),resume=True,retry_incomplete=True)
        self.assertEqual(result[0]['status'],'mechanically_passed');self.assertEqual(len(log),3)
        self.assertTrue(all(digest(p)==h for p,h in old))
        self.assertTrue(any((out/'.resume/calls').rglob('attempt-0002')))

    def test_changed_settings_or_saved_response_never_trigger_new_requests(self):
        out=self.root/'identity';log=[];config=self.config(rounds=1)
        diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log))
        changed=read(config);changed['max_tokens']+=1;save(self.root/'changed.json',changed)
        with self.assertRaises(RunStopped) as stopped:
            diagnose(self.bundle,out,self.root/'changed.json',client_factory=lambda:RecordingMock(log),resume=True)
        self.assertEqual(stopped.exception.status,'invalid_input');self.assertEqual(len(log),1)
        path=next((out/'.resume/calls').rglob('returned.json'));path.write_text('{}')
        result=diagnose(self.bundle,out,config,client_factory=lambda:RecordingMock(log),resume=True)
        self.assertEqual(result[0]['status'],'invalid_input');self.assertEqual(len(log),1)
        self.assertEqual(read(out/'run_summary.json')['exit_code'],2)

    def test_historical_or_legacy_directory_is_not_adopted_or_modified(self):
        out=self.root/'old_experiment';out.mkdir();source=out/'result.json';source.write_text('{"historical":true}')
        original=digest(source)
        with self.assertRaises(RunStopped):
            with Journal(out,{'kind':'test'},resume=True):pass
        self.assertEqual(digest(source),original);self.assertEqual([p.name for p in out.iterdir() if not p.name.startswith('._')],['result.json'])

    def test_exclusive_run_lock_blocks_second_writer(self):
        out=self.root/'locked'
        with Journal(out,{'kind':'lock-test'}):
            with self.assertRaises(RunStopped) as stopped:
                with Journal(out,{'kind':'lock-test'},resume=True):pass
            self.assertEqual(stopped.exception.status,'busy')

    def test_processor_cli_returns_explicit_success_and_budget_exit_codes(self):
        from reproduction.processor import main
        actual=process
        for budget,expected in [(1,3),(10,0)]:
            with self.subTest(budget=budget):
                out=self.root/('processor_cli_'+str(budget))
                def run(*args,**kwargs):return actual(*args,**kwargs,client_factory=MockAnnotationClient)
                argv=['processor','--facts',str(self.facts),'--output',str(out),
                      '--env-file','missing-unit-test-only.env','--max-requests',str(budget),'--allow-network']
                stream=io.StringIO()
                with patch('sys.argv',argv),patch('reproduction.processor.process',side_effect=run),contextlib.redirect_stdout(stream):
                    with self.assertRaises(SystemExit) as stopped:main()
                self.assertEqual(stopped.exception.code,expected)
                self.assertEqual(json.loads(stream.getvalue())['exit_code'],expected)

    def test_cli_exit_codes_distinguish_completion_budget_provider_and_output(self):
        from reproduction.diagnose import main
        actual=diagnose
        for label,rounds,mode,expected in [('completed',3,'normal',0),('budget',1,'normal',3),
                                            ('provider',3,'network_failure',4),('bad_output',3,'invalid_output',5)]:
            with self.subTest(label=label):
                out=self.root/('cli_'+label);config=self.config(rounds=rounds,name=label+'.json');log=[]
                def run(*args,**kwargs):return actual(*args,**kwargs,client_factory=lambda:RecordingMock(log,mode))
                argv=['diagnose','--bundle',str(self.bundle),'--output',str(out),'--config',str(config),
                      '--env-file','missing-unit-test-only.env','--allow-network']
                stream=io.StringIO()
                with patch('sys.argv',argv),patch('reproduction.diagnose.diagnose',side_effect=run),contextlib.redirect_stdout(stream):
                    with self.assertRaises(SystemExit) as stopped:main()
                self.assertEqual(stopped.exception.code,expected)
                self.assertEqual(json.loads(stream.getvalue())['exit_code'],expected)
                self.assertFalse(any(out.rglob('strict_schema_and_read_audit.json')))

if __name__=='__main__':unittest.main()
