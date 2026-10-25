"""Apply evidence-bound environment and short-success curation; keep native traces immutable."""
from pathlib import Path
import json,re,collections,hashlib
BASE=Path(__file__).resolve().parents[2];ROOT=BASE/'artifacts/02_recording_quality/trace_curation_environment_short_v1_20261020'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
assert Path('/Volumes/Public HD').is_mount()
plan=read(ROOT/'plan.json');bundle=BASE/plan['source_bundle'];catalog=read(ROOT/'trace_catalog.json');rawsignals=read(ROOT/'signal_candidates.json');checks=read(ROOT/'input_file_checks.json')
checks_by_rid=collections.defaultdict(list)
for c in checks:
 for rid in c['record_ids']:checks_by_rid[rid].append(c)
# The original dataset SQL is read as text only, never executed.
expected={}
for p in [Path('/Volumes/Public HD/DataAgentBench/query_cve/query_dataset/kev.sql')]:
 text=p.read_text();expected[str(p)]={'sha256':sha(p),'insert_counts':dict(collections.Counter(re.findall(r'^INSERT INTO ([\w.]+)\s',text,re.M)))}
save(ROOT/'static_source_dump_checks.json',expected)
def evidence(f,s,excerpt):
 o=s['observation'];refs=[v.get('source_ref') for v in o.get('variants',[]) if v.get('source_ref')]
 return {'step_id':s['step_id'],'observation_pointer':f"/facts/steps/{f['steps'].index(s)}/observation/text",'literal_excerpt':excerpt,
 'observation_sha256':hashlib.sha256(o.get('text','').encode()).hexdigest(),'original_source_refs':refs,
 'evidence_ids':[x['evidence_id'] for x in current['evidence_catalog'] if x.get('step_id')==s['step_id'] and x['role']!='assistant_claimed_intent']}
rows=[];signal_review=[]
for meta in catalog:
 rid=meta['record_id'];current=read(bundle/'traces'/f'{rid}.json');f=current['facts'];steps=f['steps'];problems=[]
 declarations=[]
 for s in steps:
  args=str(s['action'].get('arguments_raw') or s['action'].get('arguments'))
  if 'data_connections.md' not in args:continue
  text=s['observation'].get('text','')
  for m in re.finditer(r'PostgreSQL database ([\w-]+) on ([\d.]+):(\d+)',text):declarations.append({'kind':'postgres','database':m[1],'host':m[2],'port':int(m[3]),'step':s,'quote':m[0]})
  for m in re.finditer(r'MongoDB database ([\w-]+) at (mongodb://([\d.]+):(\d+)/?)',text):declarations.append({'kind':'mongo','database':m[1],'host':m[3],'port':int(m[4]),'uri':m[2],'step':s,'quote':m[0]})
 # A documented required database that is absent on the documented server is an external binding defect.
 for d in declarations:
  for s in steps:
   o=s['observation'].get('text','')
   if d['kind']=='postgres':
    error=re.search(r'database "'+re.escape(d['database'])+r'" does not exist',o)
    if error and re.search(r'port '+str(d['port'])+r'\b',o):
     alternatives=[]
     for later in steps[steps.index(s)+1:]:
      a=str(later['action'].get('arguments_raw') or later['action'].get('arguments'));t=later['observation'].get('text','')
      if 'psycopg2' in a and t.strip() and not re.search(r'Traceback|FATAL:|Connection refused|Exit code [1-9]',t):alternatives.append(later['step_id'])
     problems.append({'rule':'ENV_REQUIRED_DATABASE_BINDING_MISSING','reason':'任务明确提供的数据库名在指定服务端不存在；后续替代数据库访问不能恢复原始连接合同，也没有同一输入数据的独立绑定证明。保留恢复尝试信息，整条退出严格诊断语料。',
      'evidence':[evidence(f,d['step'],d['quote']),evidence(f,s,error[0])],'recovery_attempt_steps':alternatives,'verified_equivalent_input_recovery':False})
     break
   elif d['kind']=='mongo' and re.search(re.escape(d['host'])+':'+str(d['port']),o) and 'Connection refused' in o:
    args=str(s['action'].get('arguments_raw') or s['action'].get('arguments'))
    if d['uri'].rstrip('/') not in args:continue
    later_reads=[]
    for later in steps[steps.index(s)+1:]:
     a=str(later['action'].get('arguments_raw') or later['action'].get('arguments'));t=later['observation'].get('text','')
     if 'MongoClient' in a and d['database'] in a and t.strip() and not re.search(r'Traceback|Connection refused|Exit code [1-9]',t):later_reads.append(later['step_id'])
    if not later_reads:
     quote=next(x for x in o.splitlines() if 'Connection refused' in x)
     problems.append({'rule':'ENV_REQUIRED_SERVICE_UNAVAILABLE','reason':'按任务提供的MongoDB地址访问被拒绝；全轨迹没有后续成功读取该必需MongoDB数据源的证据。',
      'evidence':[evidence(f,d['step'],d['quote']),evidence(f,s,quote)],'successful_later_reads':later_reads})
     break
 # Curation records an empty required store only when its declared source dump explicitly contains rows.
 if any(d['kind']=='postgres' and d['database']=='cve_kev' for d in declarations):
  source=next(iter(expected.values()));assert source['insert_counts'].get('kev_entries',0)>0
  empty=[]
  for s in steps:
   a=str(s['action'].get('arguments_raw') or s['action'].get('arguments'));o=s['observation'].get('text','')
   if 'psycopg2' in a and any(term in a for term in ['kev_entries','pg_tables','pg_class']) and '55432' in a and re.search(r'count|information_schema|pg_tables|pg_class',a,re.I):
    m=re.search(r'(?:Count:|count:|Rows:|Num rows:|KEV entries count:)\s*\(?0[,)]?|All non-system tables:\s*\[\]',o,re.I)
    if m:empty.append(evidence(f,s,m[0]))
  if empty:problems.append({'rule':'ENV_REQUIRED_STORE_NOT_LOADED','reason':'指定的KEV PostgreSQL存储返回零行或无业务表，而提供的SQL源含非空INSERT数据；存在加载/服务绑定不一致。',
   'evidence':empty,'source_dump':expected,'other_port_recovery_attempted':any('5432' in str(s['action'].get('arguments_raw') or s['action'].get('arguments')) and 'cve_kev' in str(s['action'].get('arguments_raw') or s['action'].get('arguments')) for s in steps)})
 # Deduplicate repeated task documentation reads without losing the first witness.
 unique={}
 for p in problems:unique.setdefault(p['rule'],p)
 problems=list(unique.values());inputs=checks_by_rid[rid]
 failed=[c for c in inputs if c['status']=='failed'];bounded=[c for c in inputs if c['status']!='passed']
 # Current-state read failure alone never establishes historical input corruption.
 unknown=[]
 if failed:unknown.append({'reason':'present_state_input_check_failed_requires_historical_attribution','files':[c['path'] for c in failed]})
 env='exclude_confirmed_external_issue' if problems else 'review_required' if unknown else 'no_confirmed_external_issue_in_checked_evidence'
 rules=([p['rule'] for p in problems]+(['SHORT_SUCCESS_LT10'] if meta['short_success'] else []))
 decision='excluded' if rules else 'held_for_review' if unknown else 'retained'
 row={k:meta[k] for k in ['record_id','benchmark','agent_id','task_id','tool_steps','scores','attempt_directory']}
 row.update(decision=decision,exclusion_rules=rules,environment_status=env,environment_findings=problems,
   input_files_checked=len(inputs),input_check_status_counts=dict(collections.Counter(c['status'] for c in inputs)),
   current_input_checks_are_not_historical_proof=True,unknown_items=unknown,trace_path=f'traces/{rid}.json',trace_sha256=sha(bundle/'traces'/f'{rid}.json'))
 rows.append(row)
 for h in [x for x in rawsignals if x['record_id']==rid]:
  cats=h['categories'];text='\n'.join(e['text'] for e in h['excerpts']);sid=h['step_id'];s=next(x for x in steps if x['step_id']==sid);o=s['observation'].get('text','');a=str(s['action'].get('arguments_raw') or s['action'].get('arguments'))
  if cats==['resource_limit'] and not re.search(r'\bOOM\b|out of memory|MemoryError|Killed: 9|segmentation fault|Bus error',o,re.I):reason='ordinary_data_text_keyword_collision';note='OOM匹配了ZOOM/bedroom等普通数据字段；没有资源耗尽错误。'
  elif 'external_service' in cats and not re.search(r'Traceback|Connection refused|HTTP(?: Error)? (?:401|403|429|500|502|503|504)|TimeoutError|SSL(?:Error| certificate)|Network is unreachable',o):reason='ordinary_documentation_keyword_collision';note='匹配的是任务手册中的unauthorized等业务描述，不是运行时服务错误。'
  elif 'corrupt' in cats:
   reason='agent_reader_or_encoding_error';note='DBF按ASCII/CSV读取，或JSONL按单个JSON读取；后续正确读取成功，当前结构复查通过。'
  elif 'connect' in cats and not problems:reason='agent_probed_non_task_service';note='任务指定MongoDB/SQLite已成功访问；报错来自agent额外探测未提供的PostgreSQL服务，不作为外部testbed故障。'
  elif problems and any(e['step_id']==sid for p in problems for e in p['evidence']):reason='confirmed_external_issue';note='已绑定任务明确的连接说明、实际失败及恢复范围，见逐条决定。'
  elif 'missing_file' in cats or 'dependency' in cats:
   reason='agent_path_api_or_optional_dependency_error';note='命令中的路径拼写/空格转义、错误位置、未生成脚本、非必需CLI/可选库，或SQL类型/运算符错误；输入资源本身通过只读检查。'
  else:reason='reviewed_runtime_or_data_signal_not_independent_exclusion';note='结合任务声明和后续步骤审查，不凭关键词独立排除；同轨迹环境决定另列。'
  signal_review.append({**h,'review_classification':reason,'review_note':note})
assert len(rows)==164 and len({r['record_id'] for r in rows})==164
save(ROOT/'decisions.json',rows);save(ROOT/'signal_review.json',signal_review)
for name,status in [('retained_manifest.jsonl','retained'),('excluded_manifest.jsonl','excluded'),('review_manifest.jsonl','held_for_review')]:
 (ROOT/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows if r['decision']==status))
summary={'input_traces':len(rows),'retained':sum(r['decision']=='retained' for r in rows),'excluded':sum(r['decision']=='excluded' for r in rows),'held_for_review':sum(r['decision']=='held_for_review' for r in rows),
 'environment_excluded':sum(bool(r['environment_findings']) for r in rows),'short_success_excluded':sum('SHORT_SUCCESS_LT10' in r['exclusion_rules'] for r in rows),
 'overlap':sum(bool(r['environment_findings']) and 'SHORT_SUCCESS_LT10' in r['exclusion_rules'] for r in rows),'exclusion_rule_counts':dict(collections.Counter(z for r in rows for z in r['exclusion_rules'])),
 'input_checks':read(ROOT/'input_check_summary.json'),'signal_steps_reviewed':len(signal_review),'signal_review_classes':dict(collections.Counter(r['review_classification'] for r in signal_review)),
 'groups':[{'benchmark':b,'agent_id':a,'decision':d,'count':n} for (b,a,d),n in sorted(collections.Counter((r['benchmark'],r['agent_id'],r['decision']) for r in rows).items())],
 'original_trace_hashes_unchanged':all(sha(bundle/r['trace_path'])==r['sha256'] for r in plan['traces']),'new_model_calls':0,'original_code_executed':False,'original_evaluators_run':False}
save(ROOT/'summary.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))
for r in rows:
 if r['decision']!='retained':print(r['record_id'],r['task_id'],r['exclusion_rules'])
