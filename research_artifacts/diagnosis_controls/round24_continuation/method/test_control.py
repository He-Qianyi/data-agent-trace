import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('round24',Path(__file__).with_name('run.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Round24Tests(unittest.TestCase):
 def test_original_prefix_and_completed_outputs_reused(self):
  plan=m.verify();index=m.b.EvidenceIndex(m.BUNDLE)
  for job in plan['jobs']:
   rid=job['record_id'];folder=m.ROOT/'results'/rid
   if job['treatment']=='unchanged_early_terminal_reuse':
    self.assertEqual(m.sha(folder/'native_final.txt'),m.sha(m.OLD/'results'/rid/'native_final.txt'))
    self.assertEqual(m.sha(folder/'diagnosis.json'),m.sha(m.OLD/'results'/rid/'diagnosis.json'))
    self.assertTrue(m.read(folder/'mechanical_validation.json')['passed'])
   elif job['treatment']=='original_v1_prefix12_then_continue_to24':
    self.assertEqual(m.read(folder/'continuation_messages.json'),m.continuation_messages(rid))
    self.assertEqual(m.source_safe(index,rid).read_ranges,m.read(folder/'prefix_read_ranges.json'))
    self.assertEqual(m.read(folder/'prefix_check.json')['original_tool_calls_reused'],26)
 def test_round13_and24_requests_allow_tools_with_original_prompt(self):
  plan=m.read(m.ROOT/'plan.json');rid=next(j['record_id'] for j in plan['jobs'] if j['treatment']=='original_v1_prefix12_then_continue_to24')
  messages=m.read(m.ROOT/'results'/rid/'continuation_messages.json')
  client=m.b.NativeClient.__new__(m.b.NativeClient);client.base='https://soclaas-api.comp.nus.edu.sg/v1';client.key='offline-placeholder';client.context=None
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return json.dumps({'model':plan['expected_returned_model'],'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{}'}}]}).encode()
  for n in [13,24]:
   with self.subTest(round=n),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
    root=Path(tmp);m.save(root/'plan.json',plan)
    with patch.object(m.b,'ROOT',root),patch.object(m.b.urllib.request,'urlopen',return_value=Response()) as ask:
     client.ask(messages,root/'calls'/f'{n:02}')
     p=json.loads(ask.call_args.args[0].data)
     self.assertEqual(p['tool_choice'],'auto')
     self.assertEqual(p['messages'][0]['content'],(m.CONTRACT/'diagnosis.prompt.md').read_text())
     self.assertEqual(p['messages'],messages)
     self.assertEqual(p['max_tokens'],48000);self.assertEqual(p['thinking_token_budget'],16000)
     self.assertNotIn('response_format',p)
 def test_config_restores_v1_with24_rounds(self):
  p=m.read(m.BASE/'configs/05_diagnosis/CURRENT_RUNTIME.json')
  self.assertEqual(p['max_model_rounds_per_trace'],24);self.assertFalse(p['forced_final_round'])
  self.assertEqual(p['prompt_sha256'],m.sha(m.CONTRACT/'diagnosis.prompt.md'))
  self.assertEqual(m.read(m.BASE/p['acceptance_policy'])['policy_id'],m.POLICY_ID)
if __name__=='__main__':unittest.main(verbosity=2)
