"""Offline source verification and v5 facts for screening. Frozen semantics unchanged."""
import sys,json,hashlib,gc,traceback
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('${PROJECT_ROOT}');sys.path.insert(0,str(BASE))
ROOT=BASE/'outputs/trace_processor_increment289_20261025'
from pipeline.stages.trace_processor.fact_layer_v5 import adapters
from pipeline.stages.trace_processor.fact_layer_v5.build import build
from pipeline.stages.trace_processor.fact_layer_v5.sources import digest
class AnthropicChatRelayAdapter(adapters.AnthropicRelayAdapter):
    name='anthropic-chat-four-surface-relay-v1'
    @staticmethod
    def matches(row):
        return row.get('native_capture_type')=='claude_chat_four_side_v1' and all(k in row for k in ('incoming_anthropic_request','outgoing_anthropic_response','outgoing_soclaas_chat_request','incoming_soclaas_chat_response'))
adapters.ADAPTERS=(AnthropicChatRelayAdapter,*adapters.ADAPTERS)
def read(p):return json.loads(Path(p).read_text())
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.writing');t.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');t.replace(p)
def now():return datetime.now(timezone.utc).isoformat()
assert Path(__file__).resolve().parents[1].is_dir()
manifest=BASE/'outputs/raw_trace_increment_inventory_20261025/new_traces_manifest.jsonl'
rows=[json.loads(s) for s in manifest.read_text().splitlines() if s.strip()]
assert len(rows)==289 and len({r['trace_id'] for r in rows})==289
prior=read(BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json')
prior_rows=[json.loads(s) for s in (BASE/'artifacts/02_recording_quality/raw_trace_model_io_inventory_20261017/strict_complete_ended_manifest.jsonl').read_text().splitlines() if s.strip()]
assert not ({r['trace_id'] for r in rows}&{t for r in prior_rows for t in r['trace_ids']})
plan_path=ROOT/'plan.json'
if not plan_path.exists():
 write(plan_path,{'input_manifest':str(manifest),'input_manifest_sha256':digest(manifest),'input_count':289,'parent_current_validation':prior,'parent_current_validation_sha256':digest(BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json'),'rules':{'short_success':'official score equals 1 and observed tool steps <10; do not count historical replay or unsubmitted generated calls','environment':'confirmed failure of task-declared external service/input binding, with recovery checked; agent wrong paths/API/optional packages alone do not qualify','corruption':'confirmed task-source corruption; present-state missing temporary files or agent reader mistakes alone do not qualify','ambiguous':'hold for review; no score-based selection or speculative exclusion'},'frozen_contract':str(BASE/'outputs/trace_processor_frozen_v1_20261018'),'fact_implementation':'fact_layer_v5 with format-only Anthropic Chat subclass; original four wire surfaces retained','model_calls_before_screening':0,'no_prior_traces_rerun':True,'started_at_utc':now()})
index=[];strict=[]
for i,row in enumerate(rows,1):
 ledger=Path(next(s['path'] for s in row['sources'] if s['role']=='model_io'));attempt=ledger.parent
 rid='rawio-'+hashlib.sha256(str(attempt).encode()).hexdigest()[:20]
 result=read(attempt/'result.json');assert result['status']=='completed' and result['return_code']==0 and not result.get('timed_out')
 r={'record_id':rid,'attempt_directory':str(attempt),**{k:row[k] for k in ['benchmark','agent_id','task_id','run_id','sources']},'trace_ids':[row['trace_id']],'eligible_strict_completed':True,'strict_recording_complete':True,'completion':{'state':'ended_normal','receipts':[{'path':str(attempt/'result.json'),'status':result['status'],'return_code':result['return_code'],'finished_at':result['finished_at'],'timed_out':False,'expected_model_requests':result['trace_counts']['model_requests']}]},'recording_level':'actual_request_response','model_io_path':str(ledger),'declared_source_hashes':row['source_hashes'],'historical_evaluation':{'score':row.get('evaluation_score'),'status':row.get('evaluation_status'),'paths':row['evaluation_refs']}}
 strict.append(r);path=ROOT/'facts/traces'/f'{rid}.json'
 try:
  if path.exists():
   facts=read(path);assert facts['trace_metadata']['attempt_directory']==str(attempt)
   assert facts['quality']['source_reference_validation']=='passed'
   status='reused_completed_facts'
  else:
   facts=build(r);write(path,facts);status='passed'
  if len(facts['steps'])!=row['recording_quality']['tool_calls']:raise ValueError('CLI tool-step count disagrees with ledger')
  item={'record_id':rid,'trace_id':row['trace_id'],**{k:row[k] for k in ['benchmark','agent_id','task_id','run_id']},'fact_path':str(path),'fact_sha256':digest(path),'attempt_directory':str(attempt),'steps':len(facts['steps']),'model_exchanges':len(facts['model_exchanges']),'evaluation_score':row.get('evaluation_score'),'status':status,'adapter':facts['trace_metadata']['adapter']}
  del facts
 except Exception as exc:
  item={'record_id':rid,'trace_id':row['trace_id'],**{k:row[k] for k in ['benchmark','agent_id','task_id']},'attempt_directory':str(attempt),'status':'failed','error_type':type(exc).__name__,'error':str(exc)[:1600]}
 index.append(item);write(ROOT/'facts/index.json',index)
 write(ROOT/'progress.json',{'stage':'offline_facts_for_screening','status':'running','processed':i,'total':len(rows),'facts_passed':sum(r['status']!='failed' for r in index),'facts_failed':sum(r['status']=='failed' for r in index),'new_model_calls':0,'updated_at_utc':now()})
 print(json.dumps({'processed':i,'total':len(rows),**item},ensure_ascii=False),flush=True);gc.collect()
(ROOT/'facts/strict_input_manifest.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in strict))
write(ROOT/'facts/summary.json',{'records':len(index),'succeeded':sum(r['status']!='failed' for r in index),'failed':sum(r['status']=='failed' for r in index),'steps':sum(r.get('steps',0) for r in index),'new_model_calls':0,'completed_at_utc':now()})
print('OFFLINE_FACTS_FINISHED',flush=True)
