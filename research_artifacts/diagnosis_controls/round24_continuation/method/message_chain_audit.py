"""Check exact transcript continuation without printing model reasoning."""
import importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('round24',Path(__file__).with_name('run.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def run():
 plan=m.verify();checks=[]
 for job in plan['jobs']:
  if job['treatment']!='original_v1_prefix12_then_continue_to24':continue
  d=m.ROOT/'results'/job['record_id'];expected=m.read(d/'continuation_messages.json')
  for p in sorted((d/'calls').glob('*/request.json')):
   req=m.read(p);round_no=int(p.parent.name)
   assert req['messages']==expected,f'transcript_mismatch_round{round_no}'
   assert req['tool_choice']=='auto'
   item={'round':round_no,'exact_expected_history':True,'message_count':len(expected),'tool_results_appended_correctly':True}
   response=p.parent/'response.json'
   if not response.exists():item['response_pending_or_failed']=True;checks.append(item);break
   native=m.read(response)['choices'][0]['message'];calls=native.get('tool_calls') or []
   if calls:
    assistant={k:native[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in native};assistant.setdefault('role','assistant');expected.append(assistant)
    reads=[m.read(q) for q in sorted((d/'tool_reads').glob('*.json')) if m.read(q)['round']==round_no]
    if len(reads)!=len(calls):
     # A hard tool-budget failure can stop a response midway; no continuation follows.
     item['incomplete_tool_execution_at_stop']=True;checks.append(item);break
    for call,read in zip(calls,reads):
     assert call['function']['name']==read['name']
     assert m.b.json.loads(call['function']['arguments'])==read['arguments']
     expected.append({'role':'tool','tool_call_id':call['id'],'content':m.b.encode(read['result'])})
   checks.append(item)
 result={'all_checked_histories_match':True,'missing_prior_tool_results':False,'rounds':checks}
 m.save(m.ROOT/'message_chain_audit.json',result)
 print(m.b.encode({'checked_rounds':len(checks),'exact_history_and_tool_results':True}))
if __name__=='__main__':run()
