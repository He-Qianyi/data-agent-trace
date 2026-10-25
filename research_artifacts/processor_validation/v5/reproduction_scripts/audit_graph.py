import os,json,pathlib,hashlib,collections,re,ast,shlex,posixpath,warnings
warnings.filterwarnings('ignore',category=SyntaxWarning)
R=pathlib.Path('/Volumes/Public HD/Data Agent Trace');assert pathlib.Path.cwd()==R and os.path.ismount('/Volumes/Public HD')
O=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020/postmerge_checks';cfg=R/'configs/03_trace_processor/CURRENT_VALIDATION.json';F=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020/final'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,v):(O/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def ptr(v,p):
 for k in p.split('/')[1:] if p else []:
  k=k.replace('~1','/').replace('~0','~');v=v[int(k)] if isinstance(v,list) else v[k]
 return v
issues=[];candidates=collections.defaultdict(list);counts=collections.Counter();nulls=collections.defaultdict(lambda:{'count':0,'records':set(),'examples':[]});before={};cohort=collections.Counter()
def add(kind,m,**kw):candidates[kind].append({**m,**kw})
def check(ok,kind,m,**kw):
 counts[kind+'_checked']+=1
 if not ok:issues.append({**m,'check':kind,**kw})
def walk(v,p='',pat='$'):
 if isinstance(v,dict):
  for k,z in v.items():yield from walk(z,p+'/'+k.replace('~','~0').replace('/','~1'),pat+'.'+k)
 elif isinstance(v,list):
  for i,z in enumerate(v):yield from walk(z,p+'/'+str(i),pat+'[]')
 else:yield p,pat,v
for row in json.loads((F/'validation.json').read_text()):
 p=F/row['trace_path'];before[row['trace_path']]=sha(p);t=json.loads(p.read_text());f=t['facts'];meta=f['trace_metadata'];m={k:meta[k] for k in ('record_id','benchmark','agent_id','task_id')};cohort[meta['benchmark']+'/'+meta['agent_id']]+=1
 steps={s['step_id']:s for s in f['steps']};exchanges={x['exchange_id']:x for x in f['model_exchanges']};units={u['unit_id']:u for u in t['annotations']['units']};revs={v['revision_id']:v for v in f['script_revisions']};reqs={r['requirement_id'] for r in t['annotations']['task']['requirements']};ev={e['evidence_id']:e for e in t['evidence_catalog']};arts={a['artifact_id'] for a in f['deliverables']};stepidx={s['step_id']:i for i,s in enumerate(f['steps'])}
 for name,vals,key in [('steps',f['steps'],'step_id'),('exchanges',f['model_exchanges'],'exchange_id'),('units',t['annotations']['units'],'unit_id'),('revisions',f['script_revisions'],'revision_id'),('evidence',t['evidence_catalog'],'evidence_id'),('requirements',t['annotations']['task']['requirements'],'requirement_id')]:
  counts[name]+=len(vals);check(len(vals)==len({v[key] for v in vals}),'unique_'+name,m)
 for po,pat,val in walk(t):
  if val is None:
   q=nulls[pat];q['count']+=1;q['records'].add(meta['record_id'])
   if len(q['examples'])<3:q['examples'].append({**m,'pointer':po})
 for si,s in enumerate(f['steps']):
  sm={**m,'step_id':s['step_id'],'pointer':f'/facts/steps/{si}'};x=s['action']['static_extraction'];a=s['action'];e=exchanges.get(s['model_exchange_ref']);check(e is not None and s['tool_call_id'] in e['tool_call_ids'],'step_exchange_call',sm)
  check(a['model_exchange_ref']==s['model_exchange_ref'] and a['tool_call_id']==s['tool_call_id'],'action_parent',sm)
  cid={c['code_id'] for c in x['code_units']};check(len(cid)==len(x['code_units']),'code_id_scope',sm)
  for oi,op in enumerate(x['data_operations']):
   om={**sm,'pointer':sm['pointer']+f'/action/static_extraction/data_operations/{oi}'}
   if op.get('code_ref'):check(op['code_ref'] in cid,'operation_code_scope',om)
   if op.get('revision_ref'):check(op['revision_ref'] in revs,'operation_revision_scope',om)
   if op.get('operation')=='sql_api':
    counts['sql_api_operations']+=1
    if op.get('sql_text') is None:counts['sql_api_null_text']+=1
    ex=op.get('expression')
    if ex:
     try:
      call=ast.parse(ex,mode='eval').body
      if isinstance(call,ast.Call):
       api=op['api_spelling'].rsplit('.',1)[-1]
       if api.startswith('read_sql') and len(call.args)>1:add('sql_connection_classified_as_parameter',om,api=api,expression=ex,parameters_expression=op.get('parameters_expression'))
       kws={k.arg:ast.get_source_segment(ex,k.value) for k in call.keywords}
       if {'sql','sql_query','query','params','parameters'} & set(kws):add('sql_keyword_arguments',om,api=api,expression=ex,keywords=kws,sql_text=op.get('sql_text'),sql_expression=op.get('sql_expression'),parameters_expression=op.get('parameters_expression'))
       if op.get('sql_text') is None:
        n=call.args[0] if call.args else None
        try:v=ast.literal_eval(n)
        except (TypeError,ValueError):v=None
        if isinstance(v,str):add('literal_sql_null',om,expression=ex,value=v)
     except (SyntaxError,ValueError):pass
   if op.get('operation') in ('data_read_api','data_write_api') and op.get('path_literal') is None:
    add('null_data_path',om,operation=op,code_context=next((c.get('context') for c in x['code_units'] if c['code_id']==op.get('code_ref')),None))
  for ii,inv in enumerate(x['script_invocations']):
   counts['script_invocations']+=1;v=revs.get(inv.get('candidate_revision_ref'))
   if v:check(stepidx[v['step_ref']]<=si,'revision_not_future_step',sm,invocation=inv)
   else:counts['unbound_script_invocations']+=1
  for fi,fa in enumerate(x['file_actions']):
   if fa.get('revision_ref'):
    v=revs.get(fa['revision_ref']);check(v is not None and v['step_ref']==s['step_id'] and v['path_as_written']==fa.get('path'),'file_revision_parent',sm,file_action_index=fi)
   if fa.get('code_ref'):check(fa['code_ref'] in cid,'file_code_scope',sm)
  command=a.get('arguments',{});command=command.get('command',command.get('cmd')) if isinstance(command,dict) else None
  if isinstance(command,str):
   # lexical screening only: individual candidates must be adjudicated using raw quoted commands.
   if re.search(r'\bpython(?:3(?:\.\d+)?)?\s+-[^c\s]',command):add('python_flag_command',sm,command=command,invocations=x['script_invocations'])
   if re.search(r'\bcd\s',command) and x['script_invocations']:add('cwd_scope_command',sm,command=command,invocations=x['script_invocations'],file_actions=x['file_actions'])
   if x['script_invocations'] and x['file_actions']:add('same_step_write_run_order',sm,command=command,invocations=x['script_invocations'],file_actions=x['file_actions'])
   if re.search(r'\b(?:env|uv|poetry|conda|sudo|timeout|nohup)\b.*\bpython(?:3)?\b',command):add('python_wrapper_command',sm,command=command,invocations=x['script_invocations'])
   if '<<' in command and x['file_actions']:
    for fa in x['file_actions']:
     if fa.get('evidence_type')=='shell_heredoc_redirection':
      head=command[:command.find('\n')];
      if re.search(r'\bpython(?:3)?\b',head):add('python_heredoc_redirection',sm,command=command,file_action=fa)
  for ci,c in enumerate(x['code_units']):
   if c.get('language')=='python' and isinstance(c.get('code'),str):
    counts['python_code_units']+=1
    try:tree=ast.parse(c['code'])
    except (SyntaxError,ValueError):continue
    known={(z.get('code_line_start'),z.get('api_spelling')) for z in x['data_operations'] if z.get('code_ref')==c['code_id']}
    for n in ast.walk(tree):
     if not isinstance(n,ast.Call):continue
     name=getattr(n.func,'attr',getattr(n.func,'id',''))
     if name in ('read_csv','read_excel','read_parquet','read_json','to_csv','to_excel','savefig','open','connect') and not n.args:
      add('keyword_only_io',sm,code_id=c['code_id'],line=n.lineno,api=name,expression=ast.get_source_segment(c['code'],n),keywords={k.arg:ast.get_source_segment(c['code'],k.value) for k in n.keywords})
 for vi,v in enumerate(f['script_revisions']):
  if v.get('previous_revision_ref'):
   prior=revs.get(v['previous_revision_ref']);check(prior is not None and stepidx[prior['step_ref']]<=stepidx[v['step_ref']] and prior['path_key']==v['path_key'],'revision_predecessor',m,revision=v['revision_id'])
  if v.get('content') is not None:check(hashlib.sha256(v['content'].encode()).hexdigest()==v['content_sha256'],'revision_content_hash',m,revision=v['revision_id'])
  if v.get('content') is None:add('null_revision_content',m,revision=v)
 for ui,u in enumerate(t['annotations']['units']):
  s=steps.get(u['step_id']);check(s is not None and u['model_exchange_ref']==s['model_exchange_ref'],'unit_step_exchange',m,unit_id=u['unit_id']);counts['operations']+=len(u['operations'])
  for oi,op in enumerate(u['operations']):
   for li,link in enumerate(op.get('requirement_links',[])):check(link.get('requirement_id') in reqs,'requirement_link_exists',m,pointer=f'/annotations/units/{ui}/operations/{oi}/requirement_links/{li}',link=link)
   for po,pat,val in walk(op):
    if po.endswith('/evidence_id'):check(val in ev,'annotation_evidence_exists',m,pointer=f'/annotations/units/{ui}/operations/{oi}'+po)
 for ix in t['analysis_index']:
  s=steps.get(ix['step_id']);check(s is not None and ix['model_exchange_ref']==s['model_exchange_ref'] and ix['tool_call_id']==s['tool_call_id'],'analysis_parent',m,step=ix['step_id'])
  check({u['unit_id'] for u in t['annotations']['units'] if u['step_id']==ix['step_id']}==set(ix['unit_ids']),'analysis_unit_coverage',m,step=ix['step_id'])
  expected=[f'/annotations/units/{ui}/operations/{oi}' for ui,u in enumerate(t['annotations']['units']) if u['step_id']==ix['step_id'] for oi in range(len(u['operations']))]
  check(set(expected)==set(ix['operation_pointers']),'analysis_operation_coverage',m,step=ix['step_id'])
  for aid in ix['artifact_refs']:check(aid in arts,'analysis_artifact_exists',m,step=ix['step_id'])
 for ri,dr in enumerate(f['data_resources']):
  if dr.get('step_ref'):
   s=steps.get(dr['step_ref']);check(s is not None,'resource_step_exists',m)
   if dr.get('code_ref'):check(dr['code_ref'] in {c['code_id'] for c in s['action']['static_extraction']['code_units']},'resource_code_scope',m)
   if dr.get('revision_ref'):check(dr['revision_ref'] in revs,'resource_revision_scope',m)
 counts['traces']+=1
 if counts['traces']%40==0:print('SCANNED',counts['traces'],flush=True)
for k,v in nulls.items():v['affected_traces']=len(v.pop('records'))
save('null_profile.json',dict(nulls));save('relationship_issues.json',issues);save('candidates.json',dict(candidates));save('trace_hashes_before.json',before)
summary={'bundle':str(F),'counts':dict(counts),'cohort':dict(cohort),'relationship_issues':len(issues),'candidate_groups':{k:{'occurrences':len(v),'traces':len({z['record_id'] for z in v})} for k,v in candidates.items()},'nulls':sum(v['count'] for v in nulls.values()),'null_patterns':len(nulls),'model_calls':0,'captured_code_executed':False}
save('scan_summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
