"""Versioned deterministic merge; no semantic edits or recorded code execution."""
from pathlib import Path
import os,sys,json,copy,shutil,collections,datetime,time
R=Path(__file__).resolve().parents[2];sys.path.insert(0,str(R));assert os.path.ismount('/Volumes/Public HD')
from pipeline.stages.trace_processor.merge_layer.build import read,save,checked,Bundle,validate_final
from pipeline.stages.trace_processor.merge_layer.verify import verify
from pipeline.stages.trace_processor.merge_layer.downstream import export
from pipeline.stages.trace_processor.semantic_layer.validate import schema_check
from pipeline.stages.trace_processor.fact_layer_v5 import IMPLEMENTATION_VERSION
from pipeline.stages.trace_processor.fact_layer.sources import digest
P=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020';N=P/'final';parent=read(P/'parent_current_validation.json');OLD=R/parent['normalized_results'];oldrows=read(OLD/'validation.json');comp=read(P/'comparison_summary.json');compat=read(P/'annotation_compatibility.json');facts={x['record_id']:x for x in (json.loads(l) for l in (P/'facts/manifest.jsonl').read_text().splitlines())}
assert comp['status']=='passed' and comp['known_null_fields_recovered']==26 and comp['primary_connection_fields_recovered']==13 and comp['B_C_evidence_views_identical']==164
assert all(x['task_evidence_equal'] and x['operation_evidence_equal'] for x in compat)
assert all(x['success'] for x in read(P/'test_results.json'))
for rel,h in read(P/'protected_implementations.json').items():checked(R/rel,h)
for name,h in read(P/'facts/run_config.json')['implementation_sha256'].items():checked(R/'pipeline/stages/trace_processor/fact_layer_v5'/name,h)
frozen=R/'outputs/trace_processor_frozen_v1_20261018';fm=read(frozen/'frozen_manifest.json')['files']
def check_frozen():
 for rel,h in fm.items():
  q=frozen/'source_code'/rel;checked(q if q.exists() else R/rel,h)
check_frozen();N.mkdir(exist_ok=False);(N/'objects').mkdir();(N/'traces').mkdir()
objects=[p for p in (OLD/'objects').iterdir() if p.is_file() and not p.name.startswith('._')];print('Copying',len(objects),'portable source and native annotation objects',flush=True)
last=time.monotonic()
for i,p in enumerate(objects,1):
 shutil.copyfile(p,N/'objects'/p.name)
 if time.monotonic()-last>20:print('Copied',i,'/',len(objects),flush=True);last=time.monotonic()
shutil.copyfile(OLD/'normalized_trace.schema.json',N/'normalized_trace.schema.json');schema=read(N/'normalized_trace.schema.json');bundle=Bundle(N)
methods={str(p.relative_to(R)):bundle.add(p) for p in (R/'pipeline/stages/trace_processor/fact_layer_v5').iterdir() if p.suffix in {'.py','.json','.md'} and not p.name.startswith('._')}
for p in [R/'tests/test_fact_layer_v5_connection_positional.py',*[R/'work/connection_positional_v5_20261020'/name for name in ['compare.py','integrate.py','test_all.py']]]:methods[str(p.relative_to(R))]=bundle.add(p)
inputs={name:bundle.add(P/name) for name in ['parent_current_validation.json','source_manifest.jsonl','annotation_compatibility.json','comparison_summary.json','changed_facts.json','known26_recovery.json','constant_resolution_notes.json','reasonable_null_preservation.json','test_results.json','protected_implementations.json']};inputs['facts_run_config']=bundle.add(P/'facts/run_config.json')
reports=[];counts=collections.Counter();changed=[]
for i,row in enumerate(oldrows,1):
 rid=row['record_id'];oldpath=OLD/row['trace_path'];checked(oldpath,row['trace_sha256']);old=read(oldpath);factpath=P/'facts'/facts[rid]['path'];checked(factpath,facts[rid]['sha256']);newfacts=read(factpath);dest=N/row['trace_path']
 if newfacts==old['facts']:
  shutil.copyfile(oldpath,dest);t=old;origin='unchanged_parent_trace'
 else:
  t=copy.deepcopy(old);t['facts']=newfacts;t['provenance']['fact_file']=bundle.add(factpath,facts[rid]['sha256']);t['provenance']['source_files']=[{'source_id':s['source_id'],**bundle.add(s['path'],s['sha256'])} for s in newfacts['sources'] if s['exists']]
  t['provenance']['fact_layer_connection_positional_recovery']={'implementation_version':IMPLEMENTATION_VERSION,'parent_trace':bundle.add(oldpath,row['trace_sha256']),'parent_fact_file':old['provenance']['fact_file'],'implementation_files':methods,'validation_inputs':inputs,'B_C_evidence_views_identical':True,'new_model_calls':0,'raw_code_executed':False,'scope':'Bounded constant resolution of the first database connection argument and synchronized resource paths; semantic annotations and reasonable nulls preserved.'}
  for idx,step in zip(t['analysis_index'],newfacts['steps']):assert idx['step_id']==step['step_id'];idx['artifact_refs']=copy.deepcopy(step['artifact_refs'])
  t['quality']['original_recording_limits']=copy.deepcopy(newfacts['quality']);save(dest,t);origin='v5_facts_identical_native_annotations';changed.append(rid)
 assert t['annotations']==old['annotations'] and t['evidence_catalog']==old['evidence_catalog'] and t['citation_index']==old['citation_index']
 schema_check(t,schema);v={'normalized_schema':'passed',**validate_final(t,N,newfacts)};reports.append({**{k:row[k] for k in ['record_id','benchmark','agent_id','task_id']},'trace_path':row['trace_path'],'trace_sha256':digest(dest),'origin':origin,'quality':t['quality'],'validation':v})
 counts['traces']+=1;counts['tool_steps']+=len(newfacts['steps']);counts['model_exchanges']+=len(newfacts['model_exchanges']);counts['native_units']+=len(t['annotations']['units'])
 if i%20==0:print('Merged and source-validated',i,'/164',flush=True)
assert counts=={'traces':164,'tool_steps':4208,'model_exchanges':4162,'native_units':4259} and len(changed)==comp['changed_final_traces']
save(N/'validation.json',reports);save(N/'blocked_traces.json',[]);save(N/'active_fact_manifest.json',[{'record_id':row['record_id'],'fact_file':read(N/row['trace_path'])['provenance']['fact_file'],'implementation':'v5' if row['record_id'] in changed else 'unchanged_parent'} for row in reports])
source_manifest={r['original_path']:r for r in read(OLD/'input_manifest.json')};source_manifest.update(bundle.inputs);save(N/'input_manifest.json',list(source_manifest.values()))
print('Exporting all164 Evidence Builder and diagnosis inputs',flush=True);downstream=export(N);assert len(downstream)==164
# Compare actual DuckDB retrieval of every original static field, not merely SQL counts.
def fields_equal(wanted,stored,path=''):
 if isinstance(wanted,dict):
  assert isinstance(stored,dict),(path,'dict type changed')
  for k,v in wanted.items():assert k in stored,(path,k,'missing');fields_equal(v,stored[k],path+'/'+k)
  for k in stored.keys()-wanted.keys():assert stored[k] is None,(path,k,'unexpected nonnull extra')
 elif isinstance(wanted,list):
  assert isinstance(stored,list) and len(wanted)==len(stored),(path,'list changed')
  for i,(a,b) in enumerate(zip(wanted,stored)):fields_equal(a,b,path+'/'+str(i))
 else:assert type(wanted)==type(stored) and wanted==stored,(path,'value changed',type(wanted).__name__,type(stored).__name__)
fields_equal({'x':1},{'x':1,'optional':None})
for bad in [{'x':2},{'x':1,'optional':False},{}]:
 try:fields_equal({'x':1},bad)
 except AssertionError:pass
 else:raise AssertionError('invalid DB projection accepted')
expected={}
for row in reports:
 t=read(N/row['trace_path'])
 for step in t['facts']['steps']:expected[(row['record_id'],step['step_id'])]=step['action']['static_extraction']
import duckdb
con=duckdb.connect(str(N/'downstream/trace_evidence.duckdb'),read_only=True);seen=set();db_counts=collections.Counter()
for rid,encoded in con.execute('SELECT trace_id,to_json(evidence_events) FROM trace_evidence').fetchall():
 for event in json.loads(encoded):
  if event.get('event_type')!='tool_step':continue
  key=(rid,event['step_id']);stored=event['data_agent_extension']['static_extraction'];fields_equal(expected[key],stored);seen.add(key);db_counts['steps']+=1;db_counts['script_invocations']+=len(expected[key]['script_invocations']);db_counts['data_operations']+=len(expected[key]['data_operations']);db_counts['connection_operations']+=sum(o['operation']=='connection_api' for o in expected[key]['data_operations'])
con.close();assert seen==set(expected) and db_counts['steps']==4208 and db_counts['script_invocations']==430
save(N/'downstream/static_fact_validation.json',{'status':'passed','counts':dict(db_counts),'all_original_values_types_and_order_preserved':True,'only_extra_optional_null_keys_allowed':True,'known26_positional_connection_fields_reachable':True,'runtime_execution_certified':False});print('DuckDB static fact fields verified',dict(db_counts),flush=True)
for row in oldrows:checked(OLD/row['trace_path'],row['trace_sha256'])
for rel,h in read(P/'protected_implementations.json').items():checked(R/rel,h)
check_frozen()
manifest=[{'path':str(p.relative_to(N)),'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(N.rglob('*')) if p.is_file() and not p.name.startswith('._') and p.name not in {'manifest.json','portable_verification.json'}];save(N/'manifest.json',{'schema':'stage-d-delivery-manifest-v1','files':manifest,'excludes':['manifest.json','portable_verification.json','AppleDouble ._*']})
print('Independent portable verification of all164 traces and',len(manifest),'files',flush=True);portable=verify(N);assert portable['status']=='passed' and portable['traces']==164;save(N/'portable_verification.json',portable)
summary={**comp,'status':'validated_not_yet_activated','implementation_version':IMPLEMENTATION_VERSION,'changed_record_ids':changed,'tests_passed':sum(x['tests'] for x in read(P/'test_results.json')),'total_model_calls':0,'raw_recorded_code_executed':False,'frozen_manifest_entries_verified':len(fm),'v1_v2_v3_v4_implementations_unchanged':True,'prior164_traces_unchanged':True,'native_annotations_and_citations_unchanged':164,'native_units_reused':4259,'portable_bundle_status':'passed','portable_files_verified':portable['files_verified'],'downstream_static_fact_counts':dict(db_counts),'final_results':str(N.relative_to(R)),'completed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};save(P/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
