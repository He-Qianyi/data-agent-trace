"""Require all differences to be the26 audited values and13 derivation notes."""
from pathlib import Path
import os,sys,json,copy,collections,hashlib
R=Path(__file__).resolve().parents[2];sys.path.insert(0,str(R));assert os.path.ismount('/Volumes/Public HD')
from pipeline.stages.trace_processor.merge_layer.build import read,save,checked
from pipeline.stages.trace_processor.fact_layer.sources import pointer
from pipeline.stages.trace_processor.semantic_layer.evidence import requirement_input,operation_input,encode
P=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020';OLD=R/read(P/'parent_current_validation.json')['normalized_results'];A=R/'artifacts/04_annotation_quality/20261020-v4-reaudit-v1';cases=read(A/'connection_positional_omissions.json')+read(A/'derived_connection_omissions.json');manifest={v['record_id']:v for v in (json.loads(l) for l in (P/'facts/manifest.jsonl').read_text().splitlines())};assert len(manifest)==164 and all(v['status']!='failed' for v in manifest.values())
rows=read(OLD/'validation.json');reports=[];recovered=[];held=[];metadata=[];counts=collections.Counter()
def assign(v,p,value):
 base,key=p.rsplit('/',1);parent=pointer(v,base)
 if isinstance(parent,list):parent[int(key)]=value
 else:parent[key]=value
for row in rows:
 rid=row['record_id'];t=read(OLD/row['trace_path']);o=t['facts'];m=manifest[rid];new=read(P/'facts'/m['path']);checked(OLD/row['trace_path'],row['trace_sha256']);checked(P/'facts'/m['path'],m['sha256']);restored={'facts':copy.deepcopy(new)}
 for c in [c for c in cases if c['record_id']==rid]:
  before=pointer(t,c['pointer']);actual=pointer({'facts':new},c['pointer']);assert before is None and actual==c['expected_value'];assign(restored,c['pointer'],None)
  if '/data_operations/' in c['pointer']:
   op_pointer=c['pointer'].rsplit('/',1)[0];n=pointer({'facts':new},op_pointer);oldop=pointer(t,op_pointer);assert 'constant_resolution' not in oldop;note=n['constant_resolution'];assert note=={'path_literal':{'method':'bounded_source_order_strings','expression':c['first_argument_as_written']}}
   assert n['engine_verified'] is False and n['statement_execution_verified'] is False and n['expression']==c['source_expression'];pointer(restored,op_pointer).pop('constant_resolution');metadata.append({'record_id':rid,'pointer':op_pointer+'/constant_resolution','method':'bounded_source_order_strings'})
  recovered.append({**{k:c[k] for k in ['record_id','task_id','step_id','pointer']},'recovered_value':actual,'status':'recovered','source_expression':c.get('source_expression'),'basis_operation_pointer':c.get('basis_operation_pointer')})
 # This exact equality protects every old field, array order, unknown, source ID,
 # model message, script binding, evaluation and artifact, beyond separate tests.
 assert restored['facts']==o,(rid,'unexpected differences outside the26 fields and13 derivation notes')
 oldtask=read(OLD/t['provenance']['annotation_inputs']['task_evidence.json']['bundle_path']);oldop=read(OLD/t['provenance']['annotation_inputs']['operation_evidence.json']['bundle_path']);assert requirement_input(new)==oldtask and operation_input(new)==oldop
 reports.append({**{k:row[k] for k in ['record_id','benchmark','task_id','agent_id']},'facts_changed':new!=o,'changed_sections':[k for k in o if o[k]!=new[k]],'task_evidence_equal':True,'operation_evidence_equal':True,'operation_input_sha256':hashlib.sha256(encode(oldop).encode()).hexdigest()})
 counts['traces']+=1;counts['tool_steps']+=len(new['steps']);counts['model_exchanges']+=len(new['model_exchanges']);counts['native_units']+=len(t['annotations']['units'])
 for s in new['steps']:
  counts['static_operations']+=len(s['action']['static_extraction']['data_operations']);counts['connections']+=sum(x['operation']=='connection_api' for x in s['action']['static_extraction']['data_operations']);counts['script_invocations']+=len(s['action']['static_extraction']['script_invocations'])
 for c in [c for c in read(R/'artifacts/03_trace_processor/integrated_runs/trace_processor_constant_connection_v4_20261020/reasonable_null_preservation.json') if c['record_id']==rid]:assert pointer({'facts':new},c['pointer']) is None;held.append(c)
assert len(recovered)==26 and len(metadata)==13 and len(held)==53
changed=[r for r in reports if r['facts_changed']];assert len(changed)==2
for rel,h in read(P/'protected_implementations.json').items():checked(R/rel,h)
for name,v in [('annotation_compatibility.json',reports),('changed_facts.json',changed),('known26_recovery.json',recovered),('constant_resolution_notes.json',metadata),('reasonable_null_preservation.json',held)]:save(P/name,v)
summary={'status':'passed','counts':dict(counts),'known_null_fields_recovered':26,'primary_connection_fields_recovered':13,'derived_resource_fields_recovered':13,'known_null_affected_traces':2,'changed_final_traces':2,'unchanged_final_traces':162,'B_C_evidence_views_identical':164,'native_units_reusable':4259,'reasonable_script_nulls_preserved':53,'all_prior_fields_identical_except26_recoveries_and13_derivation_notes':True,'new_tool_steps_or_connection_calls_created':False,'model_calls':0};save(P/'comparison_summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
