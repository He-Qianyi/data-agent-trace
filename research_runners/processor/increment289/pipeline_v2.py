"""User-authorized B/C streaming scheduler: release C after every 30 B traces.

Only dispatch order changes. Original frozen prompts, payload generation,
post-validation, no-retry policy, global concurrency=2, and D merge are reused.
"""
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from collections import Counter
import importlib.util,json,hashlib,fcntl,os,sys,traceback
BASE=Path('${PROJECT_ROOT}');sys.path.insert(0,str(BASE))
ROOT=BASE/'outputs/trace_processor_increment289_20261025'
spec=importlib.util.spec_from_file_location('increment_frozen_v1',BASE/'work/trace_processor_increment289_20261025/run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from pipeline.stages.trace_processor.semantic_layer.evidence import batches
read=m.read;write=m.write;digest=m.digest
BATCH_SIZE=30;GLOBAL_CONCURRENCY=2
parent_verify=m.verify_freeze

def verify():
 parent_verify()
 for p,h in read(ROOT/'pipeline_execution_freeze_v2.json')['files'].items():
  if digest(BASE/p)!=h:raise ValueError('Pipeline scheduling file changed: '+p)
m.verify_freeze=verify;m.old.verify_freeze=verify

def now():return datetime.now(timezone.utc).isoformat()
def terminal_records(results):return [rid for r in results for rid in r['record_ids']]
def releasable_windows(results,already_count,all_b_done):
 ordered=terminal_records(results);new=[];start=already_count
 while len(ordered)-start>=BATCH_SIZE or (all_b_done and start<len(ordered)):
  end=min(start+BATCH_SIZE,len(ordered));new.append((start,end,ordered[start:end]));start=end
 return new

def dispatch_stages(b_waiting,c_waiting,b_active,c_active,free):
 """Reserve one slot per ready stage; use otherwise idle slots productively."""
 result=[]
 while free and (b_waiting or c_waiting):
  if c_waiting and c_active==0:stage='C'
  elif b_waiting and b_active==0:stage='B'
  elif c_waiting and not b_waiting:stage='C'
  elif b_waiting and not c_waiting:stage='B'
  elif c_waiting and c_active<b_active:stage='C'
  else:stage='B'
  result.append(stage);free-=1
  if stage=='B':b_waiting-=1;b_active+=1
  else:c_waiting-=1;c_active+=1
 return result

class Pipeline:
 def __init__(self):
  self.rows=read(ROOT/'selected_traces.json');self.row_by_id={r['record_id']:r for r in self.rows}
  self.b_jobs=[{'group_id':k,'record_ids':v} for k,v in sorted(read(ROOT/'requirements_missing_groups.json').items())]
  self.b_results=read(ROOT/'B_results.json') if (ROOT/'B_results.json').exists() else []
  self.c_results=read(ROOT/'C_results.json') if (ROOT/'C_results.json').exists() else []
  self.jobs=read(ROOT/'jobs.json');self.releases=read(ROOT/'C_batches.json') if (ROOT/'C_batches.json').exists() else []
  self.unavailable=read(ROOT/'unavailable.json') if (ROOT/'unavailable.json').exists() else []
  self.pending={};self.stop=None
  # Recover only already-committed result sidecars, never resubmit a call.
  known={r['group_id'] for r in self.b_results}
  candidates=sorted((ROOT/'B_results').glob('*.json'),key=lambda p:(p.stat().st_mtime_ns,p.name))
  for p in candidates:
   if p.name.startswith('._'):continue
   value=read(p)
   if value['group_id'] not in known:self.b_results.append(value);known.add(value['group_id'])
  known_c={r['directory'] for r in self.c_results}
  for job in self.jobs:
   path=ROOT/job['directory']/'result.json'
   if path.exists() and job['directory'] not in known_c:
    value=read(path);self.c_results.append({**job,**value});known_c.add(job['directory'])
  self.save_results()
  self.released_count=sum(len(r['record_ids']) for r in self.releases)
  assert terminal_records(self.b_results)[:self.released_count]==[rid for r in self.releases for rid in r['record_ids']]
  self.b_done={r['group_id'] for r in self.b_results};self.c_done={r['directory'] for r in self.c_results}
  self.b_todo=[j for j in self.b_jobs if j['group_id'] not in self.b_done]
  self.c_todo=[j for j in self.jobs if j['directory'] not in self.c_done]
  for j in self.b_todo:
   call=ROOT/'shared'/j['record_ids'][0]/'requirements_call'
   if call.exists():raise RuntimeError('Uncommitted B request requires offline recovery; not resent: '+str(call))
  for j in self.c_todo:
   if (ROOT/j['directory']/'call').exists():raise RuntimeError('Uncommitted C request requires offline recovery; not resent: '+j['directory'])
 def save_results(self):
  write(ROOT/'B_results.json',self.b_results);write(ROOT/'C_results.json',self.c_results)
 def release(self):
  windows=releasable_windows(self.b_results,self.released_count,len(self.b_results)==len(self.b_jobs))
  statuses={rid:r['status'] for r in self.b_results for rid in r['record_ids']}
  for start,end,rids in windows:
   batch_id=f'batch-{len(self.releases)+1:03d}';batch_jobs=[];blocked=[]
   for rid in rids:
    row=self.row_by_id[rid];shared=ROOT/'shared'/rid
    if statuses[rid]!='passed' or not (shared/'requirements_accepted.json').exists():
     reason={'record_id':rid,'stage':'B','batch_id':batch_id,'reason':'requirements_not_accepted'};blocked.append(reason);self.unavailable.append(reason);continue
    if any(j['record_id']==rid for j in self.jobs):
     # Only inputs from an interrupted, uncommitted release may exist here.
     existing=[j for j in self.jobs if j['record_id']==rid];batch_jobs.extend(existing);continue
    m.old.checked(shared/'operation_evidence.json',row['operation_view_sha256'])
    view=read(shared/'operation_evidence.json');reqs=m.old.compact(read(shared/'requirements_accepted.json'))
    try:packets=list(batches(view,reqs,max_bytes=32000,max_units=4))
    except ValueError as exc:
     reason={'record_id':rid,'stage':'C','batch_id':batch_id,'reason':'frozen_packet_budget:'+str(exc)};blocked.append(reason);self.unavailable.append(reason);continue
    for n,packet in enumerate(packets,1):
     folder=ROOT/'cases'/rid/'packets'/f'{n:04d}'
     if (folder/'input.json').exists():
      if read(folder/'input.json')!=packet:raise ValueError('Existing C input differs; no overwrite')
     else:write(folder/'input.json',packet)
     job={'record_id':rid,'task_id':row['task_id'],'packet':n,'directory':str(folder.relative_to(ROOT)),'input_sha256':digest(folder/'input.json'),'target_units':[u['unit_id'] for u in packet['target_units']],'reused':False,'batch_id':batch_id}
     self.jobs.append(job);batch_jobs.append(job);self.c_todo.append(job)
   release={'batch_id':batch_id,'B_completion_start':start+1,'B_completion_end':end,'record_ids':rids,'trace_count':len(rids),'packet_count':len(batch_jobs),'job_directories':[j['directory'] for j in batch_jobs],'blocked_inputs':blocked,'released_at_utc':now()}
   self.releases.append(release);self.released_count=end
   write(ROOT/'jobs.json',self.jobs);write(ROOT/'C_jobs.json',self.jobs);write(ROOT/'unavailable.json',self.unavailable);write(ROOT/'C_batches.json',self.releases)
   print('C_BATCH_RELEASED',json.dumps({k:v for k,v in release.items() if k not in ['record_ids','job_directories']},ensure_ascii=False),flush=True)
 def snapshot(self,status='running'):
  current=read(ROOT/'progress.json');active=[]
  for stage,job in self.pending.values():
   active.append({'stage':stage,'task_id':job.get('task_id') or self.row_by_id[job['record_ids'][0]]['task_id'],'record_id':job.get('record_id') or job['record_ids'][0],'group_id':job.get('group_id'),'packet':job.get('packet'),'batch_id':job.get('batch_id')})
  c_done={r['directory'] for r in self.c_results};c_success={r['directory'] for r in self.c_results if r['status']=='native_schema_valid'}
  batch_status=[]
  for batch in self.releases:
   dirs=set(batch['job_directories']);batch_status.append({'batch_id':batch['batch_id'],'trace_count':batch['trace_count'],'packets_total':len(dirs),'packets_finished':len(dirs&c_done),'packets_schema_valid':len(dirs&c_success),'blocked_trace_inputs':len(batch['blocked_inputs']),'status':'finished' if dirs<=c_done else 'running_or_queued'})
  c_rids={j['record_id'] for j in self.jobs};c_finished_traces=sum(all(j['directory'] in c_done for j in self.jobs if j['record_id']==rid) for rid in c_rids)
  current.update(status=status,stage='B+C_pipeline',scheduler_version='pipeline-v2-batch30-global2',worker_pid=os.getpid(),updated_at_utc=now(),active=active,total_concurrency_limit=2,B_new_calls_planned=len(self.b_jobs),B_new_calls_finished=len(self.b_results),B_calls_passed=sum(r['status']=='passed' for r in self.b_results),B_calls_failed=sum(r['status']!='passed' for r in self.b_results),C_new_calls_planned=len(self.jobs),C_plan_complete=len(self.b_results)==len(self.b_jobs),C_new_calls_finished=len(self.c_results),C_calls_schema_valid=sum(r['status']=='native_schema_valid' for r in self.c_results),C_calls_failed=sum(r['status']!='native_schema_valid' for r in self.c_results),C_new_units_schema_valid=sum(r.get('units',0) for r in self.c_results if r['status']=='native_schema_valid'),C_released_traces=self.released_count,C_finished_traces=c_finished_traces,C_trace_inputs_unavailable=len(self.unavailable),C_batches=batch_status,old_traces_rerun=0)
  current.pop('stop_reason',None)
  if self.stop:current['stop_reason']=self.stop
  write(ROOT/'progress.json',current)
  print('PIPELINE_PROGRESS',json.dumps({k:current[k] for k in ['status','updated_at_utc','B_new_calls_finished','B_calls_failed','C_new_calls_planned','C_new_calls_finished','C_calls_failed','C_released_traces','C_trace_inputs_unavailable']},ensure_ascii=False),flush=True)
 def run(self):
  self.release()
  with ThreadPoolExecutor(max_workers=2) as pool:
   while self.pending or ((self.b_todo or self.c_todo) and not self.stop):
    if (ROOT/'pause.requested').exists():self.stop='user_pause_requested'
    if not self.stop:
     self.release();counts=Counter(stage for stage,job in self.pending.values())
     for stage in dispatch_stages(len(self.b_todo),len(self.c_todo),counts['B'],counts['C'],2-len(self.pending)):
      job=(self.b_todo if stage=='B' else self.c_todo).pop(0)
      fn=m.old.b_call if stage=='B' else m.old.c_call
      self.pending[pool.submit(fn,job)]=(stage,job)
    self.snapshot()
    done,_=wait(self.pending,timeout=30,return_when=FIRST_COMPLETED) if self.pending else (set(),set())
    for future in done:
     stage,job=self.pending.pop(future);result=future.result()
     (self.b_results if stage=='B' else self.c_results).append(result);self.save_results()
     reason=m.old.halt_reason(result)
     if reason:self.stop=reason
    if done and not self.stop:self.release()
  if self.stop:self.snapshot('stopped');return
  self.release();self.snapshot('annotation_phases_finished')
  assert len(self.b_results)==len(self.b_jobs) and len(self.c_results)==len(self.jobs)
  m.merge()

def main():
 lock=open(ROOT/'worker.lock','a+');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert not (ROOT/'pause.requested').exists(),'Drain/pause flag still exists'
 assert read(ROOT/'external_transfer_authorization.json')['user_authorization']=='允许发送并运行'
 if (ROOT/'summary.json').exists():print('Completed increment; nothing resubmitted');return
 verify();write(ROOT/'worker.json',{'pid':os.getpid(),'started_at_utc':now(),'scheduler':'pipeline-v2-batch30-global2','previous_worker_pid':4349})
 Pipeline().run()
if __name__=='__main__':
 try:main()
 except BaseException as exc:
  current=read(ROOT/'progress.json');current.update(status='stopped',stop_reason=type(exc).__name__+':'+str(exc)[:1600],updated_at_utc=now(),worker_pid=os.getpid());write(ROOT/'progress.json',current);traceback.print_exc();raise
