"""Read-only protocol/source checks; never modifies native model outputs."""
import importlib.util,json
from pathlib import Path
s=importlib.util.spec_from_file_location('gate_run',Path(__file__).with_name('run.py'));b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
def excerpts(x):
 if isinstance(x,dict):
  if x.get('status')=='source_excerpt' and 'text' in x:yield x
  for v in x.values():yield from excerpts(v)
 elif isinstance(x,list):
  for v in x:yield from excerpts(v)
def run():
 plan=b.verify_plan(); root=b.ROOT
 assert all(b.sha(b.BASE/p)==h for p,h in plan['preserved_control_hashes'].items()),'historical_control_mutated'
 index=b.EvidenceIndex(b.BUNDLE);rows=[]
 for job in plan['jobs']:
  rid=job['record_id'];d=root/'results'/rid
  safe=b.SafeEvidence(index,rid); initial=safe.initial()
  assert initial==b.read(root/'inputs'/(rid+'.json'))
  requests=list(sorted((d/'calls').glob('*/request.json')))
  page_count=0
  if (d/'registered_extra_sources.json').exists():
   for item in b.read(d/'registered_extra_sources.json'):
    if item['evidence_id'] in safe.extra:continue
    raw=index.registry(rid).resolve(item['original_source_ref'])
    safe.extra[item['evidence_id']]={**item,'text':raw if isinstance(raw,str) else b.encode(raw)}
  for path in (d/'tool_reads').glob('*.json'):
   item=b.read(path);assert item['round']<12,'tool_executed_at_deadline'
   safe.note_read(item['result'])
   for e in excerpts(item['result']):
    exact=safe.page(e['evidence_id'],e['block_char_start'],max(1,e['block_char_end']-e['block_char_start']))
    assert exact['text']==e['text'],str(path)
    page_count+=1
  for p in requests:
   v=b.read(p);assert v['messages'][0]['content']==(b.CONTRACT/'diagnosis.prompt.md').read_text()
   assert v['messages'][1]['content']=='CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n'+b.encode(initial)
   assert v['tools']==b.TOOLS
   for k in ['model','max_tokens','thinking_token_budget','temperature']:assert v[k]==plan[k]
   assert 'response_format' not in v
   assert v['tool_choice']==('none' if int(p.parent.name)==12 else 'auto')
   assert int(p.parent.name)<=12
  audit={'source_pages_verified':page_count,'request_protocol_checks_passed':len(requests),'references':[],'errors':[]}
  if (d/'diagnosis.json').exists():
   result=b.read(d/'diagnosis.json')
   if isinstance(result,dict):
    for section in ['root_cause_chain','critical_decision_points']:
     for i,v in enumerate(result.get(section,[])):
      for eid in v.get('evidence_ids',[]):
       known=eid in safe.extra or eid in index.catalogs[rid]
       seen=eid in safe.read_ranges
       audit['references'].append({'section':section,'index':i,'evidence_id':eid,'exists':known,'actually_read':seen})
       if not known or not seen:audit['errors'].append({'kind':'reference','evidence_id':eid})
  audit['references_valid']=not audit['errors']
  b.save(d/'independent_reference_audit.json',audit)
  result=b.read(d/'result.json') if (d/'result.json').exists() else {'status':'not_finished'}
  receipts=[b.read(p) for p in (d/'calls').glob('*/receipt.json')]
  rows.append({'task_id':job['task_id'],'record_id':rid,'status':result['status'],
   'requests':len(requests),'responses':sum((p.parent/'response.json').exists() for p in requests),
   'transport_failures':[{'round':int(p.parent.name),'receipt':b.read(p)} for p in (d/'calls').glob('*/receipt.json') if b.read(p).get('status') in ['provider_failure','provider_or_identity_failure']],
   'final_json':(d/'diagnosis.json').exists(),'strict_pass':result['status']=='mechanically_passed',
   'model_rounds':result.get('model_rounds',0),'tool_calls':result.get('tool_calls',0),
   'citation_instances':len(audit['references']),'reference_errors':len(audit['errors']),
   'verified_source_pages':page_count,'usage':{k:sum((r.get('usage') or {}).get(k,0) or 0 for r in receipts) for k in ['prompt_tokens','completion_tokens','total_tokens']}})
 b.save(root/'protocol_audit.json',{'rows':rows,'prompt_single_insert_verified':True,
   'historical_controls_unchanged':True,'frozen_processor_files_unchanged':b.verify_processor(),
   'new_output_repair_calls':0,'new_retries':0,'semantic_entailment_verified_by_script':False})
 print(json.dumps([{k:r[k] for k in ['task_id','status','requests','responses','final_json','strict_pass','reference_errors']} for r in rows],ensure_ascii=False))
if __name__=='__main__':run()
