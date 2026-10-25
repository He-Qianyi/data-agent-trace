"""Run the last actually exercised diagnosis method on ten previously unused v5 traces."""
import argparse, collections, copy, fcntl, hashlib, importlib.util, json, os, re, shutil, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
BASE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(BASE),str(BASE/'scripts')]
spec=importlib.util.spec_from_file_location('native_diagnosis',BASE/'work/diagnosis_eight_native_tools_20261018/run.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
from pipeline.stages.diagnosis.validation import validate_diagnosis, POLICY_ID
from revalidate_diagnosis_outputs import reference_audit
ROOT=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_20261020'
LAST=BASE/'artifacts/05_diagnosis/diagnosis_no_repeat_unknown_two_20261018'
CONTRACT=BASE/'artifacts/05_diagnosis/diagnosis_prompt_v1_no_repeat_unknown_candidate_20261018'
QUOTA={('dabstep','claude_code'):1,('dabstep','codex'):2,('dabstep','kimi_code'):1,('dataagentbench','claude_code'):3,('agenticdatabench','claude_code'):1,('agenticdatabench','codex'):2}
SEED='unseen-diagnosis-20261020:'

def configure():
 b.ROOT=ROOT;b.CONTRACT=CONTRACT;b.BUNDLE=ROOT/'source_view'
 b.verify_plan=verify;b.progress=progress
 b.validate_diagnosis=lambda data,safe:validate_diagnosis(data,safe.index.steps[safe.rid])

def verify():
 assert os.path.ismount('/Volumes/Public HD'),'external_disk_not_mounted'
 plan=b.read(ROOT/'plan.json')
 for path,digest in plan['guarded_files'].items():assert b.sha(BASE/path)==digest,path
 return plan

def progress(states,status='running'):
 with b.LOCK:
  data={'status':status,'updated_at':b.now(),'worker_pid':os.getpid(),'total_traces':10,
        'finished_traces':sum(s.get('status') not in ['pending','running'] for s in states.values()),'traces':copy.deepcopy(states)}
  b.save(ROOT/'progress.json',data)
  lines=['新十条 v5 轨迹诊断进度','',f"状态：{status}；完成：{data['finished_traces']}/10",'', '|任务|状态|模型轮数|取证次数|','|---|---|---:|---:|']
  for s in states.values():lines.append(f"|{s['task_id']}|{s['status']}|{s.get('model_rounds',0)}|{s.get('tool_calls',0)}|")
  (ROOT/'progress.md').write_text('\n'.join(lines)+'\n')
  print(b.encode({'status':status,'finished':data['finished_traces'],'traces':[{k:s.get(k) for k in ['task_id','status','model_rounds','tool_calls']} for s in states.values()]}),flush=True)

def prepare():
 assert os.path.ismount('/Volumes/Public HD')
 assert not ROOT.exists(),'never_overwrite_experiment'
 cfg=b.read(BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json');source=BASE/cfg['normalized_results']
 prior=collections.defaultdict(list)
 for d in (BASE/'artifacts/05_diagnosis').iterdir():
  if not d.is_dir() or d.name.startswith('._'):continue
  for sub in ['results','outcomes']:
   for p in (d/sub).glob('rawio-*'):
    match=re.search(r'rawio-[a-f0-9]{20}',p.name)
    if match:prior[match[0]].append(str(p.relative_to(BASE)))
  for p in (d/'examples').glob('*/input.json'):
   for rid in set(re.findall(r'rawio-[a-f0-9]{20}',p.read_text())):prior[rid].append(str(p.relative_to(BASE)))
 rows=b.read(source/'validation.json');groups=collections.defaultdict(list);pool=[]
 for row in rows:
  if row['record_id'] in prior:continue
  trace=b.read(source/row['trace_path']);f=trace['facts'];m=f['trace_metadata']
  scores=[e['payload'].get('score') for e in f['evaluation']['records']]
  eligible=bool(scores and type(scores[0]) in [int,float] and scores[0]<1)
  item={'record_id':m['record_id'],'benchmark':m['benchmark'],'agent_id':m['agent_id'],'task_id':m['task_id'],
        'tool_steps':len(f['steps']),'official_score_selection_only':scores[0] if scores else None,'eligible':eligible,
        'selection_key':hashlib.sha256((SEED+m['record_id']).encode()).hexdigest()}
  pool.append(item)
  if eligible:groups[m['benchmark'],m['agent_id']].append(item)
 selected=[]
 for k,n in QUOTA.items():
  assert len(groups[k])>=n
  selected.extend(sorted(groups[k],key=lambda i:i['selection_key'])[:n])
 assert len(selected)==10 and not ({i['record_id'] for i in selected}&set(prior))
 ROOT.mkdir(parents=True)
 b.save(ROOT/'selection.json',{'seed':SEED,'policy':'Exclude any prior diagnosis attempt, retrieval pilot outcome and prompt example; select non-full official scores; fixed dataset/framework quotas; SHA256 order within strata. Scores used only offline for selection.',
  'corpus_size':len(rows),'prior_excluded':dict(prior),'never_diagnosed_remaining':len(pool),'quotas':[{'benchmark':k[0],'agent_id':k[1],'n':n} for k,n in QUOTA.items()],'candidate_pool':pool,'selected':selected})
 view=ROOT/'source_view';view.mkdir()
 for name in ['traces','objects']:(view/name).symlink_to(source/name,target_is_directory=True)
 chosen_rows=[r for r in rows if r['record_id'] in {i['record_id'] for i in selected}]
 b.save(view/'validation.json',chosen_rows)
 configure();index=b.EvidenceIndex(view)
 jobs=[];checks=[]
 for item in selected:
  rid=item['record_id'];safe=b.SafeEvidence(index,rid);packet=safe.initial()
  assert not any(k in packet for k in ['evaluation','score','deliverables','gold_answer'])
  for e in packet['evidence']:
   if e.get('status')=='source_excerpt' and e['evidence_id'] in index.catalogs[rid]:
    assert e['text']==index.catalogs[rid][e['evidence_id']]['text'][e['block_char_start']:e['block_char_end']]
  assert validate_diagnosis({'root_cause_chain':[],'critical_decision_points':[],'correct_strategy':'','stage_labels':[],'summary':'Unable to determine.'},index.steps[rid])['passed']
  b.save(ROOT/'inputs'/f'{rid}.json',packet)
  jobs.append({k:v for k,v in item.items() if k not in ['selection_key','eligible','official_score_selection_only']})
  jobs[-1].update(input_sha256=b.sha(ROOT/'inputs'/f'{rid}.json'),trace_sha256=b.sha(source/'traces'/f'{rid}.json'))
  checks.append({'record_id':rid,'final_source_verified':True,'initial_literal_pages_match':True,'excluded_top_level_outcomes_absent':True,'unknown_diagnosis_accepted':True})
 runtime=b.read(LAST/'current_runtime.json');old=b.read(LAST/'plan.json')
 runtime.update(prompt_path=str(CONTRACT/'diagnosis.prompt.md'))
 assert b.sha(Path(runtime['prompt_path']))==runtime['prompt_sha256']
 paths=[Path(__file__),BASE/'work/diagnosis_eight_native_tools_20261018/run.py',BASE/'pipeline/stages/evidence_builder/index.py',BASE/'pipeline/stages/diagnosis/validation.py',BASE/'scripts/revalidate_diagnosis_outputs.py',BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json',BASE/'configs/05_diagnosis/CURRENT_RUNTIME.json',BASE/'configs/05_diagnosis/CURRENT_ACCEPTANCE.json',CONTRACT/'diagnosis.prompt.md',view/'validation.json']
 paths += [source/r['trace_path'] for r in rows]
 paths += [ROOT/'inputs'/f"{j['record_id']}.json" for j in jobs]
 plan={**{k:old[k] for k in ['provider','endpoint','expected_returned_model','schema_sha256','outbound_exclusions']},**runtime,
       'prepared_at':b.now(),'source_bundle':str(source.relative_to(BASE)),'jobs':jobs,'concurrency':2,
       'method_source':str(LAST.relative_to(BASE)),'runtime_resolution':'Use last actually run candidate; global CURRENT_RUNTIME remains original-v1 and is not modified.',
       'guarded_files':{str(p.relative_to(BASE)):b.sha(p) for p in paths},'acceptance_standard':POLICY_ID,
       'fresh_start':True,'historical_responses_reused':0,'output_repairs':0,'review_model_calls':0,
       'retrieval_index_scope':'selected ten only; trace-local retrieval, same indexing algorithm'}
 b.save(ROOT/'plan.json',plan);b.save(ROOT/'current_runtime.json',runtime);b.save(ROOT/'tools.json',b.TOOLS);b.save(ROOT/'offline_checks.json',checks)
 (ROOT/'method').mkdir()
 for p,name in [(Path(__file__),'run.py'),(BASE/'work/diagnosis_eight_native_tools_20261018/run.py','native_runner.py'),(CONTRACT/'diagnosis.prompt.md','diagnosis.prompt.md'),(BASE/'pipeline/stages/diagnosis/validation.py','validation.py'),(BASE/'pipeline/stages/evidence_builder/index.py','index.py'),(BASE/'artifacts/05_diagnosis/diagnosis_prompt_v1_20261018/diagnosis.schema.json','diagnosis.schema.json')]:shutil.copyfile(p,ROOT/'method'/name)
 verify();print(b.encode({'prepared':10,'selected':selected,'offline_checks_passed':True}),flush=True)

def summarize():
 plan=verify();rows=[];usage=collections.Counter();failures=0
 for job in plan['jobs']:
  directory=ROOT/'results'/job['record_id'];row={**job,**b.read(directory/'result.json')};row['has_final_json']=(directory/'diagnosis.json').exists();row['accepted']=False
  if (directory/'mechanical_validation.json').exists():row['acceptance']=b.read(directory/'mechanical_validation.json');row['accepted']=row['acceptance']['passed']
  if row['has_final_json']:
   try:audit=reference_audit(b.read(directory/'diagnosis.json'),b.read(b.BUNDLE/'traces'/f"{job['record_id']}.json"),directory,directory)
   except Exception as exc:audit={'audit_error':type(exc).__name__,'errors':[],'affects_acceptance':False}
   b.save(directory/'reference_audit.json',audit);row['reference_audit']=audit
  for p in (directory/'calls').glob('*/receipt.json'):
   rec=b.read(p);failures+=int(rec.get('status','').endswith('failure'))
   for k in ['prompt_tokens','completion_tokens','total_tokens']:usage[k]+=(rec.get('usage') or {}).get(k,0) or 0
  rows.append(row)
 summary={'finished_at':b.now(),'total':10,'traces':rows,'accepted':sum(r['accepted'] for r in rows),'final_json':sum(r['has_final_json'] for r in rows),'status_counts':dict(collections.Counter(r['status'] for r in rows)),
          'model_rounds':sum(r.get('model_rounds',0) for r in rows),'tool_calls':sum(r.get('tool_calls',0) for r in rows),'service_failures':failures,'usage':dict(usage),
          'prompt_version':plan['prompt_version'],'prompt_unchanged':b.sha(CONTRACT/'diagnosis.prompt.md')==plan['prompt_sha256'],
          'source_and_guarded_files_unchanged':len(plan['guarded_files']),'semantic_accuracy_measured':False,'official_evaluations_sent':False,'benchmark_reexecutions':0,'automatic_retries':0,'output_repairs':0}
 b.save(ROOT/'summary.json',summary)
 lines=['新十条标准化轨迹：最后一版错误定位诊断流程','',f"完成10条；最终JSON {summary['final_json']}/10；CodeTracer兼容验收 {summary['accepted']}/10；服务失败 {failures} 次。",'',
 '输入来自当前 v5。排除历史诊断、取证试验及示例涉及的10条，从其余154条中按预先冻结规则抽取官方非满分轨迹。',
 '采用最后实际运行的 v1-no-repeat-and-unlocalized-candidate；24轮、60次取证、max_tokens=48000、thinking_token_budget=16000、并发2、无自动重试或强制最终轮。全局配置不改动。',
 '取证仅访问录制证据；官方评分、参考答案、评估反馈、运行后文件快照不发送。没有重执行轨迹中的代码。',
 '机械验收不认证根因正确性；引用存在性与实际读过的检查单独保存，未做独立金标准准确率测量。','',
 '|数据集|Agent|任务|工具步骤|状态|模型轮数|取证次数|JSON验收|引用问题数|','|---|---|---|---:|---|---:|---:|---|---:|']
 for r in rows:lines.append(f"|{r['benchmark']}|{r['agent_id']}|{r['task_id']}|{r['tool_steps']}|{r['status']}|{r.get('model_rounds',0)}|{r.get('tool_calls',0)}|{r['accepted']}|{len(r.get('reference_audit',{}).get('errors',[]))}|")
 lines += ['',f"模型响应 {summary['model_rounds']} 轮，只读取证 {summary['tool_calls']} 次。",'逐条原生诊断位于 results/<record_id>/diagnosis.json；逐轮请求响应、取证、引用核查均保留。']
 (ROOT/'report.md').write_text('\n'.join(lines)+'\n')
 files={str(p.relative_to(ROOT)):b.sha(p) for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink() and not p.name.startswith('._') and 'source_view' not in p.parts and p.name not in ['manifest.json','worker.lock','run.log']}
 b.save(ROOT/'manifest.json',{'files':files})
 print(b.encode({k:v for k,v in summary.items() if k!='traces'}),flush=True)

def run():
 configure();plan=verify();assert not (ROOT/'results').exists(),'existing_run_not_resubmitted'
 lock=(ROOT/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 index=b.EvidenceIndex(b.BUNDLE);client=b.NativeClient()
 states={j['record_id']:{'task_id':j['task_id'],'status':'pending'} for j in plan['jobs']}
 with ThreadPoolExecutor(max_workers=plan['concurrency']) as pool:
  futures=[pool.submit(b.run_one,index,client,j,states) for j in plan['jobs']]
  for f in as_completed(futures):f.result()
 progress(states,'finished');summarize()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','summarize']);a=p.parse_args()
 configure();globals()[a.action]()
