"""Remove only surrounding prose and a unique JSON fence; preserve diagnostic content."""
import hashlib,json,re,shutil,sys
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(BASE),str(BASE/'scripts')]
from pipeline.stages.diagnosis.validation import CORE_FIELDS,validate_diagnosis_text
from revalidate_diagnosis_outputs import reference_audit
SOURCE=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_20261020'
ROOT=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_format_revalidation_20261020'
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def verify_sources():
 manifest=read(SOURCE/'manifest.json')['files']
 for rel,h in manifest.items():assert sha(SOURCE/rel)==h,rel
 guards=read(SOURCE/'plan.json')['guarded_files']
 for rel,h in guards.items():assert sha(BASE/rel)==h,rel
 return {'original_experiment_files_unchanged':len(manifest),'source_and_guarded_files_unchanged':len(guards)}
assert Path('/Volumes/Public HD').is_mount(),'external_disk_not_mounted'
assert not ROOT.exists(),'preserve_previous_results_use_new_version'
before=verify_sources();original=read(SOURCE/'summary.json');review=read(SOURCE/'output_review.json')
packaging={r['record_id']:r['packaging'] for r in review['rows']}
ROOT.mkdir(parents=True);(ROOT/'method').mkdir()
shutil.copyfile(Path(__file__),ROOT/'method/run.py')
shutil.copyfile(BASE/'pipeline/stages/diagnosis/validation.py',ROOT/'method/validation.py')
shutil.copyfile(BASE/'configs/05_diagnosis/CURRENT_ACCEPTANCE.json',ROOT/'acceptance_policy.json')
rows=[]
for job in original['traces']:
 rid=job['record_id'];directory=SOURCE/'results'/rid;native=directory/'native_final.txt'
 raw=native.read_bytes().decode('utf-8');start=0;end=len(raw);mode='unchanged_native_json'
 if not job['accepted']:
  assert packaging[rid]=='single_fenced_json_with_surrounding_text'
  candidates=[]
  for match in re.finditer(r'```(?:json)?\s*\n(.*?)\n```',raw,re.S|re.I):
   try:data=json.loads(match[1])
   except ValueError:continue
   if isinstance(data,dict) and all(k in data for k in CORE_FIELDS):candidates.append(match)
  assert len(candidates)==1,'ambiguous_or_absent_diagnosis_fence'
  start,end=candidates[0].span(1);mode='removed_surrounding_prose_and_json_fence'
 cleaned=raw[start:end];data=json.loads(cleaned)
 assert isinstance(data,dict)
 target=ROOT/'results'/rid;target.mkdir(parents=True)
 (target/'diagnosis.json').write_bytes(cleaned.encode('utf-8'))
 assert json.loads((target/'diagnosis.json').read_bytes())==data
 trace=read(SOURCE/'source_view/traces'/f'{rid}.json');valid_ids=[s['step_id'] for s in trace['facts']['steps']]
 validation=validate_diagnosis_text(cleaned,valid_ids)
 audit=reference_audit(data,trace,directory,directory)
 save(target/'mechanical_validation.json',validation);save(target/'reference_audit.json',audit)
 extraction={'mode':mode,'native_response':str(native.relative_to(BASE)),'native_sha256':sha(native),
 'retained_char_range':[start,end],'retained_utf8_byte_range':[len(raw[:start].encode('utf-8')),len(raw[:end].encode('utf-8'))],
 'removed_prefix_characters':start,'removed_suffix_characters':len(raw)-end,
 'cleaned_sha256':sha(target/'diagnosis.json'),'retained_text_is_exact_source_substring':True,
 'json_payload_edited':False,'reserialized':False}
 save(target/'extraction.json',extraction)
 row={k:job[k] for k in ['record_id','benchmark','agent_id','task_id']}
 row.update(native_accepted=job['accepted'],wrapper_removed=mode!='unchanged_native_json',accepted=validation['passed'],
  validation_errors=validation['errors'],reference_errors=len(audit['errors']),diagnosis_path=str((target/'diagnosis.json').relative_to(ROOT)))
 rows.append(row)
after=verify_sources();assert before==after
summary={'source_experiment':str(SOURCE.relative_to(BASE)),'total':len(rows),'native_accepted':sum(r['native_accepted'] for r in rows),
 'wrappers_removed':sum(r['wrapper_removed'] for r in rows),'accepted_after_wrapper_removal':sum(r['accepted'] for r in rows),
 'recovered_acceptance':sum(r['accepted'] and not r['native_accepted'] for r in rows),'reference_error_count':sum(r['reference_errors'] for r in rows),
 'new_model_calls':0,'prompt_changed':False,'diagnostic_fields_edited':False,'semantic_accuracy_measured':False,**after,'traces':rows}
save(ROOT/'summary.json',summary)
lines=['十条诊断：去除说明文字与 JSON 代码围栏后重新验收','',
 f"原生验收 {summary['native_accepted']}/10；去除4条输出外包装后验收 {summary['accepted_after_wrapper_removal']}/10。",'',
 '只保留唯一可解析诊断 JSON 代码块的原文子串；未修改字段、数值、文字或引用，未重新序列化。其余6条直接复制原生JSON内容。',
 '相同 CodeTracer detailed 兼容标准：可解析JSON对象、五个核心字段存在、已提供且非null的关键决策步骤ID存在。额外字段忽略。',
 '原实验的响应、验收记录和当前164条标准化轨迹保持不变；本报告为单独保存的后处理重验结果。',
 '引用存在性与实际读取记录独立核查，不代表语义正确性已认证。没有调用模型。','',
 '|数据集|Agent|任务|去除外包装|原生验收|本次验收|引用问题|','|---|---|---|---|---|---|---:|']
for r in rows:lines.append(f"|{r['benchmark']}|{r['agent_id']}|{r['task_id']}|{r['wrapper_removed']}|{r['native_accepted']}|{r['accepted']}|{r['reference_errors']}|")
lines+=['','清理后的完整诊断JSON位于 results/<record_id>/diagnosis.json；每条的 extraction.json 记录原文位置与哈希。']
(ROOT/'report.md').write_text('\n'.join(lines)+'\n')
files={str(p.relative_to(ROOT)):sha(p) for p in ROOT.rglob('*') if p.is_file() and not p.name.startswith('._') and p.name!='manifest.json'}
save(ROOT/'manifest.json',{'files':files})
for rel,h in files.items():assert sha(ROOT/rel)==h,rel
print(json.dumps({k:v for k,v in summary.items() if k!='traces'},ensure_ascii=False,indent=2))
