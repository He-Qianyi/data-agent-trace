import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('gate_run',Path(__file__).with_name('run.py'))
b=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(b)
class Control(unittest.TestCase):
 def test_request_changes_only_prompt_and_preserves_termination(self):
  plan=b.read(b.ROOT/'plan.json')
  old=b.read(b.BASE/'outputs/diagnosis_eight_native_tools_20261018/plan.json')
  control_prompt=(b.BASE/'outputs/diagnosis_prompt_v1_20261018/diagnosis.prompt.md').read_text()
  new_prompt=(b.CONTRACT/'diagnosis.prompt.md').read_text()
  client=b.NativeClient.__new__(b.NativeClient)
  client.base='https://soclaas-api.comp.nus.edu.sg/v1';client.key='offline-test-placeholder';client.context=None
  class Response:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return json.dumps({'model':plan['expected_returned_model'],'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'{}'}}]}).encode()
  for round_no in [1,11,12]:
   with self.subTest(round=round_no),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
    root=Path(tmp); b.save(root/'plan.json',plan)
    with patch.object(b,'ROOT',root),patch.object(b.urllib.request,'urlopen',return_value=Response()) as call:
     messages=[{'role':'system','content':new_prompt},{'role':'user','content':'same input'}]
     client.ask(messages,root/'calls'/f'{round_no:02}')
     payload=json.loads(call.call_args.args[0].data)
     expected={'model':old['model'],'messages':[{'role':'system','content':control_prompt},{'role':'user','content':'same input'}], 'tools':b.TOOLS,'tool_choice':'none' if round_no==12 else 'auto','max_tokens':old['max_tokens'],'thinking_token_budget':old['thinking_token_budget'],'temperature':0}
     payload['messages'][0]['content']=control_prompt
     self.assertEqual(payload,expected)
     self.assertNotIn('response_format',payload)
 def test_only_one_generic_rule_no_example_change(self):
  original=b.BASE/'outputs/diagnosis_prompt_v1_20261018'
  new=(b.CONTRACT/'diagnosis.prompt.md').read_text()
  gate=(b.ROOT/'confirmation_rule.txt').read_text()
  self.assertEqual(new.count(gate),1)
  self.assertEqual(new.replace(gate,'',1),(original/'diagnosis.prompt.md').read_text())
  self.assertEqual(b.sha(original/'diagnosis.schema.json'),b.sha(b.CONTRACT/'diagnosis.schema.json'))
  self.assertNotIn('Ridge',gate)
  self.assertNotIn('caus',gate)
if __name__=='__main__':unittest.main(verbosity=2)
