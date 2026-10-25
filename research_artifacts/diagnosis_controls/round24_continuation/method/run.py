"""Extend original v1 from 12 to24 rounds; preserve recorded prefixes and outputs."""
import argparse,copy,hashlib,importlib.util,json,shutil,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location('original_native_v1',BASE/'work/diagnosis_eight_native_tools_20261018/run.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
from pipeline.stages.diagnosis.validation import validate_diagnosis_text,POLICY_ID
ROOT=BASE/'outputs/diagnosis_v1_round24_20261018'
OLD=BASE/'outputs/diagnosis_eight_native_tools_20261018'
CONTRACT=BASE/'outputs/diagnosis_prompt_v1_20261018'
BUNDLE=BASE/'outputs/stage_d_ten_20261018'
b.ROOT=ROOT
b.CONTRACT=CONTRACT

def read(p):return b.read(p)
def save(p,x):b.save(p,x)
def sha(p):return b.sha(p)

def verify():
 p=read(ROOT/'plan.json')
 for path,h in p['guarded_files'].items():assert sha(BASE/path)==h,path
 assert sha(Path(__file__))==p['runner_sha256']
 assert sha(CONTRACT/'diagnosis.prompt.md')==p['prompt_sha256']
 assert sha(BASE/'pipeline/stages/diagnosis/validation.py')==p['acceptance_validator_sha256']
 assert sha(BASE/'configs/05_diagnosis/CURRENT_RUNTIME.json')==p['current_runtime_sha256']
 assert sha(BASE/'configs/05_diagnosis/CURRENT_ACCEPTANCE.json')==p['current_acceptance_sha256']
 assert p['max_model_rounds_per_trace']==24 and p['tool_choice']=='auto'
 b.verify_processor()
 return p

def source_safe(index,rid):
 safe=b.SafeEvidence(index,rid);initial=safe.initial()
 assert initial==read(OLD/'inputs'/(rid+'.json'))
 source=OLD/'results'/rid
 for item in read(source/'registered_extra_sources.json'):
  if item['evidence_id'] in safe.extra:continue
  raw=index.registry(rid).resolve(item['original_source_ref'])
  safe.extra[item['evidence_id']]={**item,'text':raw if isinstance(raw,str) else b.encode(raw)}
 for p in sorted((source/'tool_reads').glob('*.json')):safe.note_read(read(p)['result'])
 assert safe.read_ranges==read(source/'read_ranges.json')
 return safe

def continuation_messages(rid):
 folder=OLD/'results'/rid
 request=read(folder/'calls/12/request.json')
 response=read(folder/'calls/12/response.json')
 assert request['tool_choice']=='auto' and response['choices'][0]['finish_reason']=='tool_calls'
 messages=copy.deepcopy(request['messages'])
 native=response['choices'][0]['message']
 assistant={k:native[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in native}
 assistant.setdefault('role','assistant');messages.append(assistant)
 reads=[read(p) for p in sorted((folder/'tool_reads').glob('*.json')) if read(p)['round']==12]
 assert len(reads)==len(native['tool_calls'])
 for call,item in zip(native['tool_calls'],reads):
  assert call['function']['name']==item['name']
  assert json.loads(call['function']['arguments'])==item['arguments']
  messages.append({'role':'tool','tool_call_id':call['id'],'content':b.encode(item['result'])})
 return messages

def prepare():
 assert not ROOT.exists(),'experiment_exists'
 ROOT.mkdir()
 old=read(OLD/'plan.json');index=b.EvidenceIndex(BUNDLE)
 assert sha(CONTRACT/'diagnosis.prompt.md')==old['prompt_sha256']
 runtime={'schema':'diagnosis-current-runtime-v1','prompt_path':str(CONTRACT/'diagnosis.prompt.md'),
  'prompt_version':'original-v1','prompt_sha256':old['prompt_sha256'],
  'max_model_rounds_per_trace':24,'max_tool_calls_per_trace':60,'tool_choice':'auto',
  'forced_final_round':False,'acceptance_policy':'configs/05_diagnosis/CURRENT_ACCEPTANCE.json',
  'model':old['model'],'max_tokens':old['max_tokens'],'thinking_token_budget':old['thinking_token_budget'],
  'temperature':old['temperature'],'response_format':None,'automatic_retries':0}
 save(BASE/'configs/05_diagnosis/CURRENT_RUNTIME.json',runtime)
 guarded={str(p.relative_to(BASE)):sha(p) for p in OLD.rglob('*') if p.is_file()}
 guarded[str((BASE/'work/diagnosis_eight_native_tools_20261018/run.py').relative_to(BASE))]=sha(BASE/'work/diagnosis_eight_native_tools_20261018/run.py')
 guarded[str((BASE/'pipeline/stages/evidence_builder/index.py').relative_to(BASE))]=sha(BASE/'pipeline/stages/evidence_builder/index.py')
 jobs=[]
 for job in old['jobs']:
  rid=job['record_id'];source=OLD/'results'/rid;target=ROOT/'results'/rid
  target.mkdir(parents=True)
  result=read(source/'result.json');item={**job,'baseline_status':result['status']}
  if (source/'diagnosis.json').exists():
   item['treatment']='unchanged_early_terminal_reuse'
   safe=source_safe(index,rid)
   for name in ['diagnosis.json','native_final.txt','read_ranges.json','registered_extra_sources.json']:
    if (source/name).exists():shutil.copy2(source/name,target/name)
   validation=validate_diagnosis_text((target/'native_final.txt').read_text(),index.steps[rid])
   save(target/'mechanical_validation.json',validation)
   result={**result,'status':'mechanically_passed' if validation['passed'] else 'mechanically_failed',
           'new_model_requests':0,'reused_model_rounds':result['model_rounds'],'acceptance_standard':POLICY_ID}
   save(target/'result.json',result)
  elif result['status']=='round_budget_exhausted':
   item['treatment']='original_v1_prefix12_then_continue_to24'
   safe=source_safe(index,rid);messages=continuation_messages(rid)
   assert messages[0]['content']==(CONTRACT/'diagnosis.prompt.md').read_text()
   save(target/'continuation_messages.json',messages)
   save(target/'prefix_read_ranges.json',safe.read_ranges)
   save(target/'prefix_check.json',{'original_model_rounds_reused':12,'original_tool_calls_reused':result['tool_calls'],
    'original_round12_tool_results_included':True,'new_prompt_rules':0,'messages_added_for_deadline':0,
    'system_prompt_unchanged':True,'first_new_round':13,'max_round':24,'tool_choice_all_rounds':'auto'})
  else:
   item['treatment']='transport_censored_reuse'
   save(target/'result.json',{**result,'new_model_requests':0,'comparison_status':'historical_service_censored'})
  jobs.append(item)
 plan={**{k:old[k] for k in ['model','expected_returned_model','provider','endpoint','max_tokens','thinking_token_budget','temperature','response_format','max_tool_calls_per_trace','prompt_sha256','schema_sha256']},
  'schema':'diagnosis-original-v1-24-rounds-paired-continuation','prepared_at':b.now(),
  'max_model_rounds_per_trace':24,'tool_choice':'auto','forced_final_round':False,
  'runner_sha256':sha(Path(__file__)),'guarded_files':guarded,'jobs':jobs,
  'acceptance_standard':POLICY_ID,'acceptance_validator_sha256':sha(BASE/'pipeline/stages/diagnosis/validation.py'),
  'current_runtime_sha256':sha(BASE/'configs/05_diagnosis/CURRENT_RUNTIME.json'),
  'current_acceptance_sha256':sha(BASE/'configs/05_diagnosis/CURRENT_ACCEPTANCE.json'),
  'automatic_retries':0,'output_repairs':0,'review_model_calls':0,
  'comparison_design':'reuse identical original-v1 histories; only one capped case gets rounds13-24; five early outputs reused; two service cases censored',
  'single_runtime_change_relative_to_original_v1':'maximum model rounds12->24; no prompt/provider/evidence/tool-budget change',
  'outbound_exclusions':old['outbound_exclusions']}
 save(ROOT/'plan.json',plan)
 (ROOT/'method').mkdir();shutil.copy2(Path(__file__),ROOT/'method/run.py')
 shutil.copy2(CONTRACT/'diagnosis.prompt.md',ROOT/'method/diagnosis.prompt.md')
 save(ROOT/'current_runtime.json',runtime)
 verify();progress('prepared')
 print(b.encode({'reuse_completed':5,'service_censored':2,'continuation_cases':1,'first_new_round':13,'maximum_rounds':24,'original_prompt_unchanged':True}))

def progress(status='running',active=None):
 p=read(ROOT/'plan.json');rows=[]
 for job in p['jobs']:
  f=ROOT/'results'/job['record_id']/'result.json'
  row=read(f) if f.exists() else {'task_id':job['task_id'],'status':'pending'}
  if active and active['task_id']==job['task_id']:row=active
  rows.append(row)
 save(ROOT/'progress.json',{'status':status,'updated_at':b.now(),'traces':rows})

def run():
 plan=verify();index=b.EvidenceIndex(BUNDLE);client=b.NativeClient()
 for job in plan['jobs']:
  if job['treatment']!='original_v1_prefix12_then_continue_to24':continue
  rid=job['record_id'];target=ROOT/'results'/rid
  assert not (target/'result.json').exists(),'completed_or_failed_case_not_resubmitted'
  assert not (target/'calls').exists(),'uncommitted_calls_require_review_not_resubmission'
  safe=source_safe(index,rid);messages=read(target/'continuation_messages.json')
  prefix_count=sum(1 for _ in (OLD/'results'/rid/'tool_reads').glob('*.json'))
  state={'task_id':job['task_id'],'status':'running','model_rounds':12,'tool_calls':prefix_count,
   'new_model_requests':0,'reused_model_rounds':12,'reused_tool_calls':prefix_count,'acceptance_standard':POLICY_ID}
  try:
   for round_no in range(13,25):
    verify();state['active_round']=round_no;state['new_model_requests']+=1;progress(active=state)
    native,finish,receipt=client.ask(messages,target/'calls'/f'{round_no:02}')
    state['model_rounds']=round_no
    if finish=='length':state.update(status='output_truncated',reason='provider_finish_reason_length');break
    calls=native.get('tool_calls') or []
    if calls:
     message={k:native[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in native}
     message.setdefault('role','assistant');messages.append(message)
     for call in calls:
      if state['tool_calls']>=plan['max_tool_calls_per_trace']:raise RuntimeError('tool_budget_exceeded')
      name=call['function']['name']
      try:
       args=json.loads(call['function']['arguments'])
       schema=next(t['function']['parameters'] for t in b.TOOLS if t['function']['name']==name)
       b.jsonschema.validate(args,schema);result=safe.invoke(name,args)
      except (ValueError,KeyError,StopIteration,b.jsonschema.ValidationError) as exc:
       args=None;result={'status':'invalid_tool_arguments','error_type':type(exc).__name__}
      safe.note_read(result);state['tool_calls']+=1
      save(target/'tool_reads'/f"{state['tool_calls']:03}.json",{'round':round_no,'ordinal':state['tool_calls'],'name':name,'arguments':args,'result':result})
      messages.append({'role':'tool','tool_call_id':call['id'],'content':b.encode(result)})
     progress(active=state);continue
    content=native.get('content') or '';(target/'native_final.txt').write_text(content)
    validation=validate_diagnosis_text(content,index.steps[rid]);save(target/'mechanical_validation.json',validation)
    try:data=json.loads(content)
    except ValueError:state.update(status='invalid_json',reason='no_parseable_final_JSON_no_repair')
    else:
     save(target/'diagnosis.json',data);state['status']='mechanically_passed' if validation['passed'] else 'mechanically_failed'
    break
   else:state.update(status='round_budget_exhausted',reason='no_final_diagnosis_within24_rounds')
  except RuntimeError as exc:state.update(status='failed',reason=str(exc))
  except Exception as exc:state.update(status='runner_failed',reason=type(exc).__name__)
  save(target/'read_ranges.json',safe.read_ranges)
  save(target/'registered_extra_sources.json',[{k:v for k,v in e.items() if k!='text'} for e in safe.extra.values()])
  state['finished_at']=b.now();save(target/'result.json',state);verify()
  progress('finished' if state['status']=='mechanically_passed' else 'stopped')
  print(b.encode(state),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');a=p.parse_args()
 if a.prepare:prepare()
 elif a.run:run()
