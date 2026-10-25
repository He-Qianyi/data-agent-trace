"""Classify native output packaging without repairing or changing acceptance."""
from pathlib import Path
import collections,json,re,sys,hashlib
BASE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(BASE),str(BASE/'scripts')]
from revalidate_diagnosis_outputs import reference_audit
ROOT=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_20261020'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
summary=read(ROOT/'summary.json');rows=[]
for job in summary['traces']:
 d=ROOT/'results'/job['record_id'];native=d/'native_final.txt';text=native.read_text() if native.exists() else ''
 row={k:job[k] for k in ['record_id','benchmark','agent_id','task_id','status','accepted']}
 data=None
 if (d/'diagnosis.json').exists():data=read(d/'diagnosis.json');row['packaging']='native_json'
 else:
  candidates=[]
  for match in re.finditer(r'```(?:json)?\s*\n(.*?)\n```',text,re.S|re.I):
   try:x=json.loads(match[1])
   except ValueError:continue
   if isinstance(x,dict) and all(k in x for k in ['root_cause_chain','critical_decision_points','correct_strategy','stage_labels','summary']):candidates.append(x)
  if len(candidates)==1:data=candidates[0];row['packaging']='single_fenced_json_with_surrounding_text'
  else:row['packaging']='no_unique_fenced_diagnosis_found';row['fenced_candidates']=len(candidates)
 if isinstance(data,dict):
  row['root_chain_items']=len(data.get('root_cause_chain') or [])
  row['critical_decision_items']=len(data.get('critical_decision_points') or [])
  row['model_summary_as_recorded']=data.get('summary')
  trace=read(ROOT/'source_view/traces'/f"{job['record_id']}.json")
  try:row['reference_audit']=reference_audit(data,trace,d,d)
  except Exception as e:row['reference_audit']={'audit_error':type(e).__name__}
 row['native_final_sha256']=sha(native) if native.exists() else None
 rows.append(row)
review={'purpose':'Offline packaging classification and reference-existence/read audit only; not output repair, not reacceptance, not semantic accuracy measurement.',
 'native_acceptance_unchanged':True,'packaging_counts':dict(collections.Counter(r['packaging'] for r in rows)),
 'rows_with_reference_errors':sum(bool(r.get('reference_audit',{}).get('errors')) for r in rows),
 'reference_error_count':sum(len(r.get('reference_audit',{}).get('errors',[])) for r in rows),'rows':rows}
save(ROOT/'output_review.json',review)
lines=['','原生输出包装核查（不修复输出、不更改验收）','']
for k,v in review['packaging_counts'].items():lines.append(f'- {k}: {v}')
lines+=['',f"引用存在性/已读取核查：{review['rows_with_reference_errors']} 条有问题，共 {review['reference_error_count']} 处。此检查不认证引文是否支持诊断结论。",
'围栏中的诊断仅用于区分“没有诊断内容”和“诊断包装不合规”，不另存成通过验收的 diagnosis.json。详情见 output_review.json。']
with (ROOT/'report.md').open('a') as f:f.write('\n'.join(lines)+'\n')
manifest=read(ROOT/'manifest.json');manifest['files']['report.md']=sha(ROOT/'report.md');manifest['files']['output_review.json']=sha(ROOT/'output_review.json')
save(ROOT/'manifest.json',manifest)
print(json.dumps({k:v for k,v in review.items() if k!='rows'},ensure_ascii=False))
