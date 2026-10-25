import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from pipeline.stages.trace_processor.semantic_layer import contract
from pipeline.stages.trace_processor.semantic_layer.evidence import requirement_input,operation_input,batches,chunks,validate_evidence_catalog,encode
from pipeline.stages.trace_processor.semantic_layer.validate import requirements,operations
from pipeline.stages.trace_processor.semantic_layer.run import prepare,execute
from pipeline.stages.trace_processor.semantic_layer.provider import Client,ProviderFailure
from pipeline.stages.trace_processor.fact_layer.sources import digest


def task_answer(view):
    return {"task_summary":{"description":"fixture task","evidence":[{"evidence_id":view['evidence'][0]['evidence_id'],"quote":view['evidence'][0]['text']}]},
            "requirements":[{"kind":"goal","description":"fixture requirement","basis":"explicit",
                             "evidence":[{"evidence_id":e['evidence_id'],"quote":e['text']}]} for e in view['evidence']],
            "coverage":[{"evidence_id":e['evidence_id'],"disposition":"requirement","reason":"explicit fixture"} for e in view['evidence']],
            "ambiguities":[]}


def operation_answer(packet):
    evidence={e['evidence_id']:e for e in packet['evidence']}
    return {"units":[{"unit_id":u['unit_id'],"operations":[{"type":"unknown","phase":"unknown","description":"fixture interpretation",
                     "basis":"uncertain","evidence":[{"evidence_id":u['evidence_ids'][0],"quote":evidence[u['evidence_ids'][0]]['text']}],
                     "details":[],"requirement_links":[],"limitations":["fixture"]}]} for u in packet['target_units']]}


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.response=self.root/'response.json'
        self.response.write_text(json.dumps({'content':[{'type':'text','text':'I will inspect the input data.'}]}))
        self.trace={"schema_version":"data-agent-fact-layer-v1","sources":[{"source_id":"S0001","path":str(self.response),
                    "exists":True,"roles":["model_io"],"sha256":digest(self.response)}],
                    "trace_metadata":{"record_id":"fixture"},
                    "task":{"instruction":"Read the provided data.\nSave output.csv.\n", "instruction_ref":None,
                            "metadata_as_recorded":{"gold_file_name":"DO_NOT_SEND_GOLD"}},
                    "model_exchanges":[{"exchange_id":"X0001","response_ref":{"source_id":"S0001","json_pointer":""}}],
                    "steps":[{"step_id":"S0001","model_exchange_ref":"X0001","tool_call_id":"c1",
                              "action":{"tool_name":"Bash","arguments":{"command":"python3 analyze.py","timeout":20},
                                        "arguments_raw":{'command':'python3 analyze.py','timeout':20},"arguments_ref":{}},
                              "observation":{"text":"Observed data rows\n","variants":[{"source_ref":{}}],"availability":"recorded",
                                             "tool_status_as_recorded":None,"is_error_as_recorded":False,"exit_code_as_recorded":0,
                                             "truncation":{"status":"not_explicitly_marked"}},
                              "annotations":{"status":"not_run"}}],
                    "evaluation":{"SECRET_OFFICIAL_SCORE":"DO_NOT_SEND_EVALUATION"},
                    "deliverables":[{"snapshot":"DO_NOT_SEND_SNAPSHOT"}],"final_response":{"text":"DO_NOT_SEND_FINAL"},
                    "quality":{"source_reference_validation":"passed","semantic_annotation_run":False}}

    def packet(self):
        view=operation_input(self.trace)
        return view,next(batches(view,[{"requirement_id":"R0001","description":"Inspect data"}]))

    def fact_directory(self):
        facts=self.root/'facts';(facts/'traces').mkdir(parents=True)
        p=facts/'traces/fixture.json';p.write_text(json.dumps(self.trace))
        row={"record_id":"fixture","path":"traces/fixture.json","sha256":digest(p),"status":"passed", "benchmark":"fixture","agent_id":"fixture","task_id":"one"}
        (facts/'manifest.jsonl').write_text(json.dumps(row)+'\n')
        return facts

    def test_task_view_only_sees_original_prompt(self):
        view=requirement_input(self.trace)
        self.assertNotIn('DO_NOT_SEND',encode(view))
        validate_evidence_catalog(self.trace,view['evidence'])

    def test_operation_view_excludes_evaluator_snapshots_final_and_gold(self):
        view,packet=self.packet()
        self.assertNotIn('DO_NOT_SEND',encode(view))
        self.assertNotIn('DO_NOT_SEND',encode(packet))
        self.assertIn('I will inspect the input data.',encode(packet))

    def test_utf8_chunk_boundaries_are_lossless_and_bounded(self):
        text='甲乙丙\nabc\u2028xyz'*80
        parts=list(chunks(text,37))
        self.assertEqual(''.join(v for _,_,v in parts),text)
        self.assertTrue(all(len(v.encode())<=37 for _,_,v in parts))

    def test_task_ids_assigned_only_after_validation(self):
        view=requirement_input(self.trace);raw=task_answer(view)
        result=requirements(raw,view)
        self.assertNotIn('requirement_id',raw['requirements'][0])
        self.assertEqual(result['requirements'][0]['requirement_id'],'R0001')

    def test_task_block_coverage_cannot_drop_a_line(self):
        view=requirement_input(self.trace);raw=task_answer(view);raw['coverage'].pop()
        with self.assertRaisesRegex(ValueError,'coverage_mismatch'):requirements(raw,view)

    def test_task_requirement_block_needs_a_requirement_reference(self):
        view=requirement_input(self.trace);raw=task_answer(view);raw['requirements'].pop()
        with self.assertRaisesRegex(ValueError,'not_supported'):requirements(raw,view)

    def test_invented_evidence_is_rejected(self):
        view=requirement_input(self.trace);raw=task_answer(view)
        raw['requirements'][0]['evidence'][0]['quote']='fabricated text'
        with self.assertRaisesRegex(ValueError,'quote_not_exact'):requirements(raw,view)

    def test_invented_source_id_is_rejected(self):
        view=requirement_input(self.trace);raw=task_answer(view)
        raw['task_summary']['evidence'][0]['evidence_id']='T9999'
        with self.assertRaisesRegex(ValueError,'unknown_evidence'):requirements(raw,view)

    def test_operation_complete_unit_coverage(self):
        view,packet=self.packet();parsed=operation_answer(packet)
        checked=operations(parsed,packet,view['evidence'])
        self.assertEqual(checked['units'][0]['step_id'],'S0001')
        self.assertEqual(checked['units'][0]['operations'][0]['operation_id'],'U00001-O001')

    def test_missing_operation_unit_rejected(self):
        view,packet=self.packet();parsed=operation_answer(packet);parsed['units'].append(copy.deepcopy(parsed['units'][0]))
        with self.assertRaisesRegex(ValueError,'coverage_mismatch'):operations(parsed,packet,view['evidence'])

    def test_observed_claim_requires_observation_quote(self):
        view,packet=self.packet();parsed=operation_answer(packet)
        parsed['units'][0]['operations'][0]['phase']='observed_in_result'
        with self.assertRaisesRegex(ValueError,'observed_phase'):operations(parsed,packet,view['evidence'])

    def test_unknown_requirement_id_rejected(self):
        view,packet=self.packet();parsed=operation_answer(packet);op=parsed['units'][0]['operations'][0]
        op['requirement_links']=[{'requirement_id':'R9999','relation':'targets','evidence':copy.deepcopy(op['evidence'])}]
        with self.assertRaisesRegex(ValueError,'unknown_requirement'):operations(parsed,packet,view['evidence'])

    def test_evidence_view_mutation_detected(self):
        view=operation_input(self.trace);view['evidence'][0]['text']='changed'
        with self.assertRaisesRegex(ValueError,'Evidence differs'):validate_evidence_catalog(self.trace,view['evidence'])

    def test_packet_byte_budget_enforced(self):
        view=operation_input(self.trace)
        with self.assertRaisesRegex(ValueError,'exceeds'):list(batches(view,[],max_bytes=20))

    def test_full_offline_pipeline_tasks_before_operations_and_fact_unchanged(self):
        facts=self.fact_directory();output=self.root/'semantic';prepare(facts,output)
        seen=[]
        class FakeClient:
            def __init__(self,*args,**kwargs):pass
            def ask(self,prompt,data,schema,directory):
                seen.append(prompt)
                Path(directory).mkdir(parents=True)
                if schema is contract.REQUIREMENT_SCHEMA:return task_answer(data),{'fixture':True}
                return operation_answer(data),{'fixture':True}
        before=(facts/'traces/fixture.json').read_bytes()
        result=execute(output,'not-read.env',client_factory=FakeClient)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(seen,[contract.REQUIREMENT_PROMPT,contract.OPERATION_PROMPT])
        self.assertEqual((facts/'traces/fixture.json').read_bytes(),before)
        normalized=json.loads((output/'traces/fixture.json').read_text())
        self.assertEqual(normalized['steps'][0]['action'],self.trace['steps'][0]['action'])
        self.assertFalse(normalized['semantic_layer']['evaluation_provided_to_annotator'])

    def test_provider_failure_stops_without_second_request_or_retry(self):
        facts=self.fact_directory();output=self.root/'semantic';prepare(facts,output)
        seen=[]
        class FailingClient:
            def __init__(self,*args,**kwargs):pass
            def ask(self,*args):seen.append(1);raise ProviderFailure({'reason':'provider_http_failure','http_status':429})
        result=execute(output,'not-read.env',client_factory=FailingClient)
        self.assertEqual(result['status'],'stopped_provider_failure');self.assertEqual(len(seen),1)
        with self.assertRaisesRegex(ValueError,'not executable'):execute(output,'not-read.env',client_factory=FailingClient)

    def test_frozen_implementation_and_inputs_checked_before_requests(self):
        facts=self.fact_directory();output=self.root/'semantic';prepare(facts,output)
        (output/'selection.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'Selection changed'):execute(output,'not-read.env')

    def test_http_error_body_does_not_expose_secrets_or_trigger_retry(self):
        key='private-fixture-secret'
        directory=self.root/'call'
        client=object.__new__(Client);client.thinking_token_budget=None;client.key=key;client.base='https://soclaas-api.comp.nus.edu.sg/v1'
        client.model='fixture';client.output_tokens=18000;client.timeout=10
        body={'error':{'code':'continuation_limit_exceeded','message':key}}
        err=urllib.error.HTTPError(client.base+'/responses',400,'secret',{},io.BytesIO(json.dumps(body).encode()))
        with patch('urllib.request.urlopen',side_effect=err) as send:
            with self.assertRaises(ProviderFailure) as stopped:client.ask('fixture',{},contract.REQUIREMENT_SCHEMA,directory)
        self.assertEqual(send.call_count,1)
        self.assertEqual(stopped.exception.metadata['provider_error_code'],'continuation_limit_exceeded')
        self.assertNotIn(key,(directory/'receipt.json').read_text()+(directory/'request.json').read_text())

    def test_chat_transport_sends_schema_in_prompt_and_preserves_native_response(self):
        from contextlib import contextmanager
        directory=self.root/'chat-call'
        client=object.__new__(Client);client.thinking_token_budget=None;client.key='private-fixture-secret';client.base='https://soclaas-api.comp.nus.edu.sg/v1'
        client.model='x-test-2';client.output_tokens=18000;client.timeout=10;client.wire_protocol='chat'
        body={'id':'chat-fixture','model':'deepseek-v4.1-flash','choices':[{'message':{'content':'{"units":[]}'},'finish_reason':'stop'}],
              'usage':{'completion_tokens':12}}
        @contextmanager
        def response(*args,**kwargs):
            result=io.BytesIO(json.dumps(body).encode());result.status=200;yield result
        with patch('urllib.request.urlopen',side_effect=response) as send:
            parsed,receipt=client.ask('fixture contract',{},contract.OPERATION_SCHEMA,directory)
        request=send.call_args.args[0];payload=json.loads(request.data)
        self.assertTrue(request.full_url.endswith('/chat/completions'))
        self.assertEqual(payload['response_format'],{'type':'json_object'})
        self.assertIn('OUTPUT JSON SCHEMA',payload['messages'][0]['content'])
        self.assertNotIn('input',payload);self.assertNotIn('text',payload)
        self.assertEqual(parsed,{'units':[]});self.assertEqual(receipt['wire_protocol'],'chat')
        self.assertEqual(json.loads((directory/'response.json').read_text()),body)


if __name__=='__main__':unittest.main()
