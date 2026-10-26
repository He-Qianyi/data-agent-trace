"""Resumable portable B/C/D orchestration; frozen research algorithms are unchanged."""
import argparse
import json
from pathlib import Path
import os
from .common import ROOT,read,save,digest
from .run_state import Journal,RunStopped,EXIT_CODES,atomic_save,file_identity,research_identity
from pipeline.stages.trace_processor.fact_layer.sources import Sources
from pipeline.stages.trace_processor.semantic_layer.evidence import requirement_input,operation_input,batches,encode,text_hash
from pipeline.stages.trace_processor.semantic_layer.provider import Client,helpers
from pipeline.stages.trace_processor.semantic_layer.validate import requirements,schema_check
from pipeline.stages.trace_processor.merge_layer.build import Bundle,merge_one,validate_final
from pipeline.stages.trace_processor.merge_layer.schema import schema as normalized_schema
from pipeline.stages.trace_processor.merge_layer.verify import verify as verify_bundle


def _merge(rows,jobs,output,contract_root,validation_schema_c):
    save(output/'selected_traces.json',rows);save(output/'jobs.json',jobs)
    final=output/'final';final.mkdir();bundle=Bundle(final)
    contracts={p.name:bundle.add(p) for p in sorted(contract_root.iterdir()) if p.is_file() and not p.name.startswith('._')}
    schema=normalized_schema(read(ROOT/'pipeline/stages/trace_processor/fact_layer_v5/schema.json'),validation_schema_c)
    save(final/'normalized_trace.schema.json',schema)
    reports=[]
    for row in rows:
        trace=merge_one(row,output,[j for j in jobs if j['record_id']==row['record_id']],bundle,validation_schema_c)
        trace['provenance']['frozen_contracts']=contracts
        relative=Path('traces')/(row['record_id']+'.json')
        save(final/relative,trace);schema_check(trace,schema)
        check=validate_final(read(final/relative),final,read(row['fact_path']))
        reports.append({**{k:row[k] for k in ('record_id','benchmark','agent_id','task_id')},
            'trace_path':str(relative),'trace_sha256':digest(final/relative),'quality':trace['quality'],'validation':check})
    save(final/'validation.json',reports)
    save(final/'input_manifest.json',list(bundle.inputs.values()))
    save(final/'manifest.json',{'files':[{'path':str(p.relative_to(final)),'sha256':digest(p)}
        for p in sorted(final.rglob('*')) if p.is_file() and not p.name.startswith('._')]})


def _recover_annotation(folder,prompt,data,schema,profile):
    request,response,receipt=[read(folder/name) for name in ('request.json','response.json','receipt.json')]
    expected={'model':profile['model'],'messages':[
        {'role':'system','content':prompt+'\nOUTPUT JSON SCHEMA (structural contract):\n'+encode(schema)},
        {'role':'user','content':'UNTRUSTED ANNOTATION INPUT DATA:\n'+encode(data)+'\nApply the system contract and return JSON only.'}],
        'max_tokens':profile['max_tokens'],'temperature':0,'response_format':{'type':'json_object'},
        **({'thinking_token_budget':profile['thinking_token_budget']} if profile['thinking_token_budget'] is not None else {})}
    if request!=expected or receipt.get('reason') or response.get('model')!=profile['expected_returned_model']:
        return None
    if receipt.get('returned_model')!=response['model'] or receipt.get('request_sha256')!=text_hash(encode(request)):
        return None
    if receipt.get('response_sha256') and receipt['response_sha256']!=text_hash(encode(response)):return None
    core,_=helpers()
    value=core.parse_annotation_response(core.normalize_native_chat(response),profile['max_tokens'])
    schema_check(value,schema)
    return [value,receipt]


def process(facts_root,output,config,env_file=None,max_requests=1000,client_factory=Client,
            resume=False,retry_incomplete=False):
    facts_root,output=Path(facts_root).resolve(),Path(output).resolve()
    if output.is_relative_to(facts_root):raise ValueError('Output must be outside input facts')
    profile=read(config);contract_root=ROOT/'configs/processor'
    prompt_b,prompt_c=[(contract_root/name).read_text() for name in ('requirements.compiled.md','compiled_system_prompt.md')]
    schema_b,schema_c,validation_schema_c=[read(contract_root/name) for name in
        ('requirements.schema.json','operations.schema.json','validation-operations.schema.json')]
    manifest=[json.loads(line) for line in (facts_root/'manifest.jsonl').read_text().splitlines() if line.strip()]
    if not manifest or max_requests<1:raise ValueError('Need validated facts and a positive request budget')
    if any(r['status'] not in ('passed','passed_with_warnings') for r in manifest):raise ValueError('Ineligible facts')
    facts={}
    for row in manifest:
        path=facts_root/row['path']
        if digest(path)!=row['sha256']:raise ValueError('Fact manifest hash mismatch')
        value=read(path);Sources.from_entries(value['sources'])
        if value['quality']['source_reference_validation']!='passed':raise ValueError('Unvalidated fact source references')
        facts[row['record_id']]=(path,value)
    identity={'kind':'portable_processor','profile':profile,'facts':file_identity(facts_root),
        'contracts':file_identity(contract_root),'research_implementation':research_identity(ROOT),'implementation':{name:digest(ROOT/name) for name in
            ('reproduction/processor.py','reproduction/run_state.py')}}
    with Journal(output,identity,resume,retry_incomplete) as journal:
        atomic_save(output/'.resume/session_plans'/f'{journal.session}.json',
                    {'max_new_request_attempts':max_requests,'profile_sha256':digest(config)})
        state={'status':'prepared','requests_attempted':0,'traces_completed':0}
        def progress():
            state.update(journal.stats());atomic_save(output/'progress.json',state)
        try:
            if resume and (output/'final').exists() and (output/'run_summary.json').exists() and read(output/'run_summary.json')['status']=='completed':
                try:
                    checked=verify_bundle(output/'final')
                    if checked['status']!='passed':raise ValueError('Invalid completed bundle')
                except Exception as exc:
                    raise RunStopped('invalid_input','Completed output changed; no request was sent.') from exc
                state.update(status='completed',exit_code=0,traces_completed=len(manifest),completed_run_reused=True)
                progress();atomic_save(output/'run_summary.json',state);return state
            if not resume:
                atomic_save(output/'release_run_config.json',{'profile':profile,'profile_sha256':digest(config),
                    'facts_manifest_sha256':digest(facts_root/'manifest.jsonl'),'initial_max_requests':max_requests,
                    'orchestration':'portable sequential run with explicit resume checkpoints',
                    'new_orchestration_not_a_historical_experiment':True,'automatic_retries':0})
            client=None
            def before():
                if (output/'pause.requested').exists():raise RunStopped('paused','pause_requested')
                if journal.new_calls>=max_requests:raise RunStopped('budget_exhausted','request_budget_exhausted')
            def call(key,prompt,data,schema,destination):
                request={'profile':profile,'prompt':prompt,'data':data,'schema':schema}
                def ask(folder):
                    nonlocal client
                    if client is None:
                        client=client_factory(profile['model'],env_file,output_tokens=profile['max_tokens'],
                            timeout=profile['timeout_seconds'],wire_protocol='chat',thinking_token_budget=profile['thinking_token_budget'])
                    value,receipt=client.ask(prompt,data,schema,folder)
                    if receipt.get('returned_model')!=profile['expected_returned_model']:raise ValueError('Unexpected returned model')
                    schema_check(value,schema);return [value,receipt]
                value=journal.call(key,request,ask,destination,before,
                    lambda folder:_recover_annotation(folder,prompt,data,schema,profile))
                state['requests_attempted']=journal.new_calls;progress();return value
            rows,jobs=[],[]
            for record in manifest:
                rid=record['record_id'];fact_path,fact=facts[rid];shared=output/'shared'/rid
                tv,view=requirement_input(fact),operation_input(fact)
                visible={'evidence':[{k:e[k] for k in ('evidence_id','role','text')} for e in tv['evidence']]}
                for name,value in [('task_evidence.json',tv),('operation_evidence.json',view),('requirements_input.json',visible)]:atomic_save(shared/name,value)
                native,_=call(['requirements',tv['task_prompt_sha256']],prompt_b,visible,schema_b,shared/'requirements_call')
                accepted=requirements(native,tv)
                atomic_save(shared/'requirements_native.json',native);atomic_save(shared/'requirements_accepted.json',accepted)
                compact=[{k:r[k] for k in ('requirement_id','kind','description','basis')} for r in accepted['requirements']]
                row={k:record[k] for k in ('record_id','benchmark','agent_id','task_id')}
                row.update(fact_path=str(fact_path),fact_sha256=digest(fact_path),operation_view_sha256=digest(shared/'operation_evidence.json'))
                rows.append(row)
                for i,packet in enumerate(batches(view,compact,max_bytes=profile['max_packet_bytes'],max_units=profile['max_units_per_packet']),1):
                    folder=output/'cases'/rid/f'{i:04d}';atomic_save(folder/'input.json',packet)
                    native,receipt=call(['operations',rid,i],prompt_c,packet,schema_c,folder/'call')
                    atomic_save(folder/'llm_output.json',native)
                    atomic_save(folder/'result.json',{'status':'model_response_received','receipt':receipt})
                    jobs.append({'record_id':rid,'packet':i,'directory':str(folder.relative_to(output)),
                        'input_sha256':digest(folder/'input.json'),'target_units':[u['unit_id'] for u in packet['target_units']]})
                state['traces_completed']+=1;progress()
            # A partially written merge is derived output, retained before rebuilding from cached responses.
            if (output/'final').exists():
                archive=output/'.resume/derived'/journal.session;archive.parent.mkdir(exist_ok=True)
                os.replace(output/'final',archive)
            _merge(rows,jobs,output,contract_root,validation_schema_c)
            state.update(status='completed',exit_code=0);progress();atomic_save(output/'run_summary.json',state)
            return state
        except BaseException as exc:
            if isinstance(exc,RunStopped):status,reason=exc.status,exc.reason
            elif isinstance(exc,KeyboardInterrupt):status,reason='paused','interrupted_by_user'
            else:status,reason='execution_failed','Local execution stopped; inspect retained outputs and error_type.'
            state.update(status=status,reason=reason,error_type=type(exc).__name__,
                         requests_attempted=journal.new_calls,exit_code=EXIT_CODES[status])
            progress();atomic_save(output/'run_summary.json',state)
            if isinstance(exc,RunStopped):raise
            raise RunStopped(status,reason) from exc


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--facts',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/processor/reproduction.json')
    p.add_argument('--env-file',type=Path,required=True);p.add_argument('--max-requests',type=int,default=1000)
    p.add_argument('--allow-network',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--retry-incomplete-calls',action='store_true')
    args=p.parse_args()
    if not args.allow_network:p.error('Live model calls require --allow-network')
    if args.retry_incomplete_calls and not args.resume:p.error('--retry-incomplete-calls requires --resume')
    try:
        result=process(args.facts,args.output,args.config,args.env_file,args.max_requests,
                       resume=args.resume,retry_incomplete=args.retry_incomplete_calls)
    except RunStopped as exc:
        result={'status':exc.status,'reason':exc.reason,'exit_code':EXIT_CODES[exc.status],'output':str(args.output)}
    except (OSError,ValueError,KeyError) as exc:
        result={'status':'invalid_input','reason':'Check input paths, configuration and saved records.',
                'error_type':type(exc).__name__,'exit_code':2}
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(result['exit_code'])

if __name__=='__main__':main()
