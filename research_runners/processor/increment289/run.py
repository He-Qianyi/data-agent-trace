"""Missing-work-only execution of frozen B/C and v5 D, with durable checkpoints."""
from pathlib import Path
from datetime import datetime,timezone
from collections import defaultdict,Counter
import importlib.util,json,hashlib,sys,shutil,os,fcntl,traceback
BASE=Path('${PROJECT_ROOT}');sys.path.insert(0,str(BASE))
ROOT=BASE/'outputs/trace_processor_increment289_20261025'
FREEZE=BASE/'outputs/trace_processor_frozen_v1_20261018'
from pipeline.stages.trace_processor.semantic_layer.evidence import operation_input,requirement_input,encode
from pipeline.stages.trace_processor.semantic_layer.validate import requirements,schema_check
from pipeline.stages.trace_processor.validation_policy import allow_extra_fields
from pipeline.stages.trace_processor.fact_layer.sources import digest
from pipeline.stages.trace_processor.merge_layer.build import Bundle,merge_one,validate_final,bundled_sources,validated_task
from pipeline.stages.trace_processor.fact_layer.build import validate as validate_facts
from pipeline.stages.trace_processor.merge_layer.downstream import export as export_downstream
from pipeline.stages.trace_processor.merge_layer.verify import verify as verify_delivery
spec=importlib.util.spec_from_file_location('frozen_batch_runner',BASE/'work/trace_processor_batch164_20261018/run.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
old.ROOT=ROOT;old.MIRROR=ROOT

def read(p):return json.loads(Path(p).read_text())
def write(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.writing');tmp.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def sig(v):return hashlib.sha256(encode(v).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def progress(**values):
 current=read(ROOT/'progress.json') if (ROOT/'progress.json').exists() else {};current.update(values,updated_at_utc=now(),worker_pid=os.getpid());write(ROOT/'progress.json',current)
 print(json.dumps({k:v for k,v in current.items() if k not in ['active','summary']},ensure_ascii=False),flush=True)
old.progress=progress
original_schema_check=schema_check
operation_schema=read(FREEZE/'operations.schema.json')
def accepted_schema(native,schema):
 return original_schema_check(native,allow_extra_fields(schema) if schema==operation_schema else schema)
old.schema_check=accepted_schema

def verify_freeze():
 for p,h in read(FREEZE/'frozen_manifest.json')['files'].items():
  if digest(BASE/p)!=h:raise ValueError('Frozen file changed: '+p)
 for p,h in read(ROOT/'execution_freeze.json')['files'].items():
  if digest(BASE/p)!=h:raise ValueError('Increment execution file changed: '+p)
old.verify_freeze=verify_freeze

def prepare():
 if (ROOT/'selected_traces.json').exists():return
 decisions=read(ROOT/'curation/decisions.json');retained={r['record_id'] for r in decisions if r['decision']=='retained'}
 facts=[r for r in read(ROOT/'facts/index.json') if r['record_id'] in retained and r['status']!='failed']
 previous=BASE/'outputs/trace_processor_batch164_20261018';b_index=defaultdict(list)
 for p in (previous/'shared').glob('*/requirements_input.json'):
  if p.name.startswith('._'):continue
  if (p.parent/'requirements_native.json').exists() and (p.parent/'requirements_accepted.json').exists():b_index[sig(read(p))].append(p.parent)
 selected=[];reuse=[];groups={}
 for row in facts:
  fact=read(row['fact_path']);view=operation_input(fact);tv=requirement_input(fact);bv={'evidence':[{k:e[k] for k in ['evidence_id','role','text']} for e in tv['evidence']]};shared=ROOT/'shared'/row['record_id']
  write(shared/'operation_evidence.json',view);write(shared/'task_evidence.json',tv);write(shared/'requirements_input.json',bv)
  row={**row,'units':len(view['units']),'operation_view_path':str(shared/'operation_evidence.json'),'operation_view_sha256':digest(shared/'operation_evidence.json'),'reused_C_units':0,'B_reused':False}
  for candidate in sorted(b_index.get(sig(bv),[])):
   native=read(candidate/'requirements_native.json');review=read(candidate/'requirements_review.json') if (candidate/'requirements_review.json').exists() else None
   try:accepted=validated_task(native,tv,review)
   except (ValueError,KeyError):continue
   write(shared/'requirements_native.json',native);write(shared/'requirements_accepted.json',accepted)
   if review:shutil.copyfile(candidate/'requirements_review.json',shared/'requirements_review.json')
   (shared/'requirements_call').mkdir(exist_ok=True)
   for p in (candidate/'requirements_call').glob('*.json'):
    if not p.name.startswith('._'):shutil.copyfile(p,shared/'requirements_call'/p.name)
   proof={'source_request':str(candidate/'requirements_call/request.json'),'source_request_sha256':digest(candidate/'requirements_call/request.json'),'source_response_sha256':digest(candidate/'requirements_call/response.json'),'native_semantics_unchanged':True,'citations_rebound_to_this_fact_sources':True,'llm_calls':0}
   write(shared/'requirements_reuse.json',proof);reuse.append({'record_id':row['record_id'],'stage':'B',**proof});row['B_reused']=True;break
  if not row['B_reused']:groups.setdefault(sig(bv),[]).append(row['record_id'])
  selected.append(row)
 historical=set(read(previous/'historical_request_signatures.json'))
 for p in (previous/'request_reservations').iterdir():
  if len(p.name)==64 and not p.name.startswith('._'):historical.add(p.name)
 write(ROOT/'selected_traces.json',selected);write(ROOT/'requirements_missing_groups.json',groups);write(ROOT/'jobs.json',[]);write(ROOT/'reuse_ledger.json',reuse);write(ROOT/'historical_request_signatures.json',sorted(historical))
 plan={'traces':len(selected),'B_reused_traces':sum(r['B_reused'] for r in selected),'B_unique_missing_inputs':len(groups),'C_reused_units':0,'total_units':sum(r['units'] for r in selected),'historical_requests_reserved':len(historical),'new_generation_frozen_prompt':True,'acceptance_overlay':'allow_extra_fields_v1_20261019_only','automatic_retries':0,'repair_calls':0,'review_calls':0}
 write(ROOT/'annotation_plan.json',plan)
 preserve={str(FREEZE/n):digest(FREEZE/n) for n in ['requirements.compiled.md','compiled_system_prompt.md','requirements.schema.json','operations.schema.json','runtime_profile.json']}
 preserve.update({r['fact_path']:r['fact_sha256'] for r in selected});write(ROOT/'preserved_inputs.json',preserve)
 files={str(p.relative_to(BASE)):digest(p) for p in [Path(__file__),ROOT/'annotation_plan.json',ROOT/'selected_traces.json',ROOT/'requirements_missing_groups.json',ROOT/'curation/decisions.json',BASE/'work/trace_processor_increment289_20261025/prepare_facts.py']}
 write(ROOT/'execution_freeze.json',{'files':files,'created_at_utc':now(),'frozen_parent_manifest_sha256':digest(FREEZE/'frozen_manifest.json')})
 print('PREPARED',json.dumps(plan,ensure_ascii=False),flush=True)

def merge():
 verify_freeze();final=ROOT/'final';final.mkdir(exist_ok=True);bundle=Bundle(final)
 normalized=read(FREEZE/'normalized_trace.schema.json');write(final/'normalized_trace.schema.json',normalized)
 contracts={p.name:bundle.add(p) for p in FREEZE.iterdir() if p.is_file() and not p.name.startswith('._')}
 rows=read(ROOT/'selected_traces.json');jobs=read(ROOT/'jobs.json');reports=[];blocked=[]
 for row in rows:
  rid=row['record_id'];relative=Path('traces')/(rid+'.json')
  try:
   shared=ROOT/'shared'/rid
   if not (shared/'requirements_accepted.json').exists():raise ValueError('B_unavailable_no_semantic_trace_invented')
   relevant=[j for j in jobs if j['record_id']==rid];valid=[j for j in relevant if read(ROOT/j['directory']/'result.json')['status'] in ['native_schema_valid','reused_native_schema_valid']]
   trace=merge_one(row,ROOT,valid,bundle,allow_extra_fields(operation_schema),allow_missing=True);trace['provenance']['frozen_contracts']=contracts
   trace['provenance']['unsuccessful_packets']=[{'packet':j['packet'],'files':[bundle.add(p) for p in (ROOT/j['directory']).rglob('*.json') if not p.name.startswith('._')]} for j in relevant if j not in valid]
   if (ROOT/'unavailable.json').exists():trace['provenance']['unavailable_annotations']=[v for v in read(ROOT/'unavailable.json') if v['record_id']==rid]
   write(final/relative,trace);original_schema_check(trace,normalized);validation=validate_final(trace,final,read(row['fact_path']))
   reports.append({'record_id':rid,'benchmark':row['benchmark'],'agent_id':row['agent_id'],'task_id':row['task_id'],'trace_path':str(relative),'trace_sha256':digest(final/relative),'origin':'increment_frozen_v1_v5_facts','quality':trace['quality'],'validation':validation})
  except Exception as exc:
   fact=read(row['fact_path']);fact_file=bundle.add(row['fact_path'],row['fact_sha256']);source_files=[{'source_id':s['source_id'],**bundle.add(s['path'],s['sha256'])} for s in fact['sources'] if s['exists']]
   incomplete={'schema_version':'data-agent-stage-d-incomplete-v1','facts':fact,'provenance':{'fact_file':fact_file,'source_files':source_files,'annotation_files':[bundle.add(p) for p in (ROOT/'shared'/rid).rglob('*.json') if not p.name.startswith('._')]},'quality':{'status':'incomplete_annotations','reason':type(exc).__name__+':'+str(exc)[:1500],'fact_mutation_performed':False,'semantic_correctness_certified':False}}
   validate_facts(fact,bundled_sources(incomplete,final));write(final/'incomplete'/f'{rid}.json',incomplete);blocked.append({'record_id':rid,'task_id':row['task_id'],'reason':incomplete['quality']['reason'],'trace_path':f'incomplete/{rid}.json'})
  write(final/'validation.json',reports);write(final/'blocked_traces.json',blocked);progress(stage='D',status='running',D_finished_traces=len(reports),D_blocked_traces=len(blocked))
 write(final/'input_manifest.json',list(bundle.inputs.values()))
 if reports:export_downstream(final)
 # Preserve input/provenance hashes; never change an old trace or annotation.
 for p,h in read(ROOT/'preserved_inputs.json').items():
  if digest(p)!=h:raise ValueError('Preserved input changed: '+p)
 files=[{'path':str(p.relative_to(final)),'sha256':digest(p),'bytes':p.stat().st_size} for p in final.rglob('*') if p.is_file() and not p.name.startswith('._') and p.name not in ['manifest.json','portable_verification.json']]
 write(final/'manifest.json',{'schema':'stage-d-delivery-manifest-v1','files':files})
 verified=verify_delivery(final);write(final/'portable_verification.json',verified)
 complete=sum(r['quality']['units_retained']==r['quality']['planned_units'] for r in reports)
 summary={'status':'completed' if complete==len(rows) else 'completed_with_incomplete_annotations','retained_input_traces':len(rows),'normalized_traces':len(reports),'complete_annotation_traces':complete,'blocked_traces':len(blocked),'new_B_calls':len(read(ROOT/'B_results.json')),'new_C_calls':len(read(ROOT/'C_results.json')),'original_processed_traces_rerun':0,'automatic_retries':0,'repair_calls':0,'review_calls':0,'semantic_accuracy_certified':False,'completed_at_utc':now()}
 write(ROOT/'summary.json',summary);progress(stage='finished',status=summary['status'],summary=summary)
 # Keep the prior corpus active until this independent increment is complete
 # and reviewed; publish a discoverable pointer to the new verified result.
 write(BASE/'configs/03_trace_processor/CURRENT_INCREMENT.json',{'version':'increment289-frozen-v1-v5-facts-20261025','path':str(ROOT.relative_to(BASE)),'normalized_results':str((final).relative_to(BASE)),'summary':str((ROOT/'summary.json').relative_to(BASE)),'status':summary['status'],'parent_current_validation_unchanged':True})

def main():
 lock=open(ROOT/'worker.lock','a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if (ROOT/'summary.json').exists():print('Existing completed increment; nothing resubmitted');return
 prepare();verify_freeze();plan=read(ROOT/'annotation_plan.json');write(ROOT/'worker.json',{'pid':os.getpid(),'started_at_utc':now()})
 progress(status='running',stage='B',B_reused_traces=plan['B_reused_traces'],C_reused_units=0,total_traces=plan['traces'])
 b_jobs=[{'group_id':k,'record_ids':v} for k,v in sorted(read(ROOT/'requirements_missing_groups.json').items())]
 if not old.pool_execute('B',b_jobs,old.b_call,ROOT/'B_results.json'):return
 c_jobs=old.prepare_c()
 if not old.pool_execute('C',c_jobs,old.c_call,ROOT/'C_results.json'):return
 merge()
if __name__=='__main__':
 try:main()
 except BaseException as exc:
  progress(status='stopped',stop_reason=type(exc).__name__+':'+str(exc)[:1800]);traceback.print_exc();raise
