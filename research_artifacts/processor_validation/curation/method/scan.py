from pathlib import Path
import json,re,hashlib,collections
BASE=Path(__file__).resolve().parents[2];ROOT=BASE/'artifacts/02_recording_quality/trace_curation_environment_short_v1_20261020'
assert Path('/Volumes/Public HD').is_mount()
assert not ROOT.exists()
ROOT.mkdir(parents=True)
def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
config=read(BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json');bundle=BASE/config['normalized_results'];rows=read(bundle/'validation.json')
plan={'source_bundle':str(bundle.relative_to(BASE)),'source_config_sha256':sha(BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json'),
 'rules':{'short_success':'Exclude official score == 1 and fewer than 10 facts.steps; keep historical outputs.',
 'environment':'Exclude only confirmed external runtime/testbed/input corruption that makes the recorded task/evaluation untrustworthy; require source evidence and check later recovery. Agent-selected wrong path/database/library/API or bad generated code remains diagnostic material.',
 'unknown':'Separate unresolved external blockers for review; never equate an absent current temporary path or a keyword match with historical corruption.',
 'data_checks':'Recorded observations and metadata plus read-only checks of preserved task input resources; present-state file checks are not proof of historical state.',
 'actions':'No model calls, no original task code or evaluator execution, no original data edits.'},
 'traces':[{'record_id':r['record_id'],'trace_path':r['trace_path'],'sha256':sha(bundle/r['trace_path'])} for r in rows]}
save(ROOT/'plan.json',plan);save(ROOT/'parent_current_validation.json',config)
patterns={
 'connect':r'connection refused|could not connect|connection (?:to .* )?(?:failed|closed|reset)|server closed the connection|could not translate host name|could not open connection|no route to host|authentication failed|password authentication|database .* does not exist|cannot connect to (?:the )?docker|docker daemon|is the server running',
 'missing_file':r'FileNotFoundError|No such file or directory|cannot (?:access|open).*No such file|unable to open database file|does not exist:|file .* not found',
 'dependency':r'ModuleNotFoundError|ImportError|command not found|No module named|Library not loaded|cannot import name|symbol not found',
 'corrupt':r'database disk image is malformed|file is not a database|not a valid (?:DuckDB|database)|database.*corrupt|malformed database|invalid (?:database|file) header|JSONDecodeError|ParserError|UnicodeDecodeError|BadZipFile|unexpected end of (?:file|data)|invalid parquet|magic bytes|checksum (?:mismatch|failure)',
 'permission_storage':r'Permission denied|Read-only file system|No space left|disk quota|Input/output error|disk I/O error|Operation not permitted',
 'external_service':r'HTTP(?: Error)? (?:401|403|429|500|502|503|504)|AccessDenied|Unauthorized|SSL(?:Error| certificate)|certificate verify failed|timed out|TimeoutError|Network is unreachable|Could not resolve host',
 'resource_limit':r'out of memory|MemoryError|OOM|Killed: 9|segmentation fault|Bus error',
}
compiled={k:re.compile(v,re.I) for k,v in patterns.items()};hits=[];catalog=[];evals=[]
for row in rows:
 p=bundle/row['trace_path'];t=read(p);f=t['facts'];m=f['trace_metadata'];rid=m['record_id'];attempt=Path(m['attempt_directory']);scores=[e['payload'].get('score') for e in f['evaluation']['records']]
 item={k:m[k] for k in ['record_id','benchmark','agent_id','task_id','attempt_directory']};item.update(tool_steps=len(f['steps']),scores=scores,short_success=bool(len(f['steps'])<10 and any(type(s) in [int,float] and s==1 for s in scores)),
 workspace=str(attempt/'workspace'),input_manifests=[])
 for name in ['input_data_manifest.json','input_manifest.json','dataset_manifest.json']:
  q=attempt/name
  if q.is_file():item['input_manifests'].append({'path':str(q),'payload':read(q),'sha256':sha(q)})
 for sub in ['data','context','input','inputs']:
  q=attempt/'workspace'/sub
  if q.exists():item.setdefault('workspace_input_dirs',[]).append(str(q))
 for step in f['steps']:
  obs=step['observation'];text=obs.get('text')
  if not isinstance(text,str):continue
  cats=[k for k,pat in compiled.items() if pat.search(text)]
  if not cats:continue
  excerpts=[]
  for ln,line in enumerate(text.splitlines(),1):
   if any(pat.search(line) for pat in compiled.values()):excerpts.append({'line':ln,'text':line[:2000]})
  hits.append({'record_id':rid,'dataset':m['benchmark'],'agent':m['agent_id'],'task':m['task_id'],'step_id':step['step_id'],
   'categories':cats,'exit_code':obs.get('exit_code_as_recorded'),'excerpts':excerpts[:12],
   'action':str(step['action'].get('arguments_raw') or step['action'].get('arguments'))[:1600],
   'observation_pointer':f"/facts/steps/{f['steps'].index(step)}/observation/text"})
 item['flagged_steps']=sum(h['record_id']==rid for h in hits)
 catalog.append(item)
 for e in f['evaluation']['records']:evals.append({'record_id':rid,'task':m['task_id'],'payload':e['payload'],'source_ref':e['source_ref']})
save(ROOT/'trace_catalog.json',catalog);save(ROOT/'signal_candidates.json',hits);save(ROOT/'evaluations.json',evals)
summary={'traces':len(catalog),'signal_steps':len(hits),'signal_traces':len({h['record_id'] for h in hits}),'categories':dict(collections.Counter(c for h in hits for c in h['categories'])),'short_success':[i for i in catalog if i['short_success']]}
save(ROOT/'scan_summary.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k!='short_success'},ensure_ascii=False))
for h in hits:print(h['record_id'],h['dataset'],h['agent'],h['task'],h['step_id'],','.join(h['categories']),' | '.join(e['text'] for e in h['excerpts'])[:900])
