"""Exact final-corpus checks and preservation of all earlier known repairs."""
from pathlib import Path
import sys,os,collections,ast,warnings
warnings.filterwarnings('ignore',category=SyntaxWarning)
R=Path(__file__).resolve().parents[2];sys.path.insert(0,str(R));assert os.path.ismount('/Volumes/Public HD')
from pipeline.stages.trace_processor.merge_layer.build import read,save,checked
from pipeline.stages.trace_processor.fact_layer.sources import pointer
from pipeline.stages.trace_processor.fact_layer_v5.constants import call_string_environments,string_value
P=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020';N=P/'final';parent=read(P/'parent_current_validation.json');OLD=R/parent['normalized_results'];PR=R/parent['fact_layer_upgrade'];assert read(P/'summary.json')['portable_bundle_status']=='passed'
graph=read(P/'postmerge_checks/scan_summary.json');assert graph['counts']['traces']==164 and graph['relationship_issues']==0
recovered=read(P/'known26_recovery.json');old122=read(PR/'known122_recovery.json');old709=read(PR/'known709_connections.json');old32=read(PR/'postmerge_validation.json')['prior32_still_recovered'];held=read(P/'reasonable_null_preservation.json');counts=collections.Counter();before=collections.Counter();after=collections.Counter();missing=[];positional_checks=[]
def tally(v,p,out):
 if isinstance(v,dict):
  for k,z in v.items():tally(z,p+'.'+k,out)
 elif isinstance(v,list):
  for z in v:tally(z,p+'[]',out)
 elif v is None:out[p]+=1
for row in read(N/'validation.json'):
 rid=row['record_id'];t=read(N/row['trace_path']);o=read(OLD/row['trace_path']);checked(N/row['trace_path'],row['trace_sha256']);assert t['annotations']==o['annotations'] and t['evidence_catalog']==o['evidence_catalog'] and t['citation_index']==o['citation_index'];counts['traces']+=1;counts['native_units']+=len(t['annotations']['units']);tally(o,'$',before);tally(t,'$',after)
 for c in [c for c in recovered if c['record_id']==rid]:assert pointer(t,c['pointer'])==c['recovered_value'];counts['known26_fields_rechecked']+=1
 for c in [c for c in old122 if c['record_id']==rid]:assert pointer(t,c['current_pointer'])==c['recovered_value'];counts['prior122_rechecked']+=1
 for c in [c for c in old709 if c['record_id']==rid]:
  op=pointer(t,c['operation_pointer']);d=pointer(t,c['resource_pointer']);assert op==pointer(o,c['operation_pointer']) and d==pointer(o,c['resource_pointer']);counts['prior709_connections_rechecked']+=1
 for c in [c for c in old32 if c['record_id']==rid]:assert pointer(t,c['current_pointer'])==pointer(o,c['current_pointer']) and pointer(t,c['current_pointer']) is not None;counts['prior32_rechecked']+=1
 for c in [c for c in held if c['record_id']==rid]:assert pointer(t,c['pointer']) is None;counts['reasonable_script_nulls_rechecked']+=1
 f=t['facts'];versions={v['revision_id']:v for v in f['script_revisions']}
 for si,s in enumerate(f['steps']):
  x=s['action']['static_extraction'];counts['script_invocations']+=len(x['script_invocations'])
  codes={('code_ref',c['code_id']):c['code'] for c in x['code_units'] if c.get('language')=='python' and isinstance(c.get('code'),str)}
  for run in x['script_invocations']:
   rr=run.get('candidate_revision_ref')
   if rr and versions[rr]['path_as_written'].endswith('.py'):codes[('revision_ref',rr)]=versions[rr]['content']
  parsed={}
  for oi,op in enumerate(x['data_operations']):
   if op['operation']!='connection_api':continue
   counts['all_connection_operations']+=1
   if not op.get('connection_driver_as_written'):continue
   assert op['engine_verified'] is False and op['statement_execution_verified'] is False
   key=('code_ref',op['code_ref']) if op.get('code_ref') else ('revision_ref',op['revision_ref']);code=codes[key]
   if key not in parsed:
    tree=ast.parse(code);parsed[key]=(tree,call_string_environments(tree))
   tree,env=parsed[key];call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and n.lineno==op['code_line_start'] and n.end_lineno==op['code_line_end'] and ast.get_source_segment(code,n)==op['expression'])
   if call.args:
    counts['positional_connection_expressions_checked']+=1;context=env.get(id(call));resolved=string_value(call.args[0],*context) if context else None
    if resolved is not None:
     if op['path_literal']!=resolved:missing.append({'record_id':rid,'step_id':s['step_id'],'pointer':f'/facts/steps/{si}/action/static_extraction/data_operations/{oi}/path_literal','expected':resolved})
     positional_checks.append({'record_id':rid,'step_id':s['step_id'],'expression':op['expression'],'resolved_value':resolved,'status':'matches'})
 # Resources retain exact parents and keyword values, including unknown options.
 resources=[d for d in f['data_resources'] if d['basis']=='static_api_argument'];source_ops=[(s,op) for s in f['steps'] for op in s['action']['static_extraction']['data_operations'] if op['operation'] in ('data_read_api','connection_api','file_open_api')];assert len(resources)==len(source_ops)
 for d,(s,op) in zip(resources,source_ops):
  assert d['step_ref']==s['step_id'] and d['path_as_recorded']==op.get('path_literal') and d['code_ref']==op.get('code_ref') and d['revision_ref']==op.get('revision_ref') and d['access_observed'] is None
  if 'connection_options_as_recorded' in d:assert d['connection_options_as_recorded']==op['options_literal'] and d['connection_parameters_as_written']==op['connection_parameters_as_written']
  counts['resource_parent_pairs_checked']+=1
assert counts['traces']==164 and counts['known26_fields_rechecked']==26 and counts['prior122_rechecked']==122 and counts['prior709_connections_rechecked']==709 and counts['prior32_rechecked']==32 and counts['reasonable_script_nulls_rechecked']==53
assert counts['all_connection_operations']==1946 and counts['script_invocations']==430 and counts['native_units']==4259 and not missing and len(positional_checks)==13
# No new nulls or incidental filling: exactly13 operation and13 resource paths.
delta={k:after[k]-before[k] for k in before.keys()|after.keys() if before[k]!=after[k]};assert delta=={'$.facts.steps[].action.static_extraction.data_operations[].path_literal':-13,'$.facts.data_resources[].path_as_recorded':-13},delta
assert sum(before.values())==64779 and sum(after.values())==64753
for rel,h in read(P/'protected_implementations.json').items():checked(R/rel,h)
for name,h in read(P/'facts/run_config.json')['implementation_sha256'].items():checked(R/'pipeline/stages/trace_processor/fact_layer_v5'/name,h)
for row in read(OLD/'validation.json'):checked(OLD/row['trace_path'],row['trace_sha256'])
report={'status':'passed','counts':dict(counts),'graph_relationship_issues':0,'all26_values_match_independent_audit_expectations':True,'constant_positional_connection_fields_still_missing':missing,'prior122_preserved':122,'prior709_connections_preserved':709,'prior32_preserved':32,'reasonable_script_nulls_preserved':53,'native_annotations_citations_and_catalog_unchanged':164,'null_total_before':sum(before.values()),'null_total_after':sum(after.values()),'null_pattern_delta':delta,'all_other_null_patterns_identical':True,'raw_sources_evaluations_and_parent_version_unchanged':True,'model_calls':0,'captured_code_executed':False};save(P/'postmerge_validation.json',report);save(P/'positional_connection_checks.json',positional_checks);print(report)
