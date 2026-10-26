"""Resumable native-tool diagnosis with CodeTracer-compatible acceptance only."""
import argparse
import json
from pathlib import Path
import jsonschema
from .common import ROOT,read,digest,load_native
from .run_state import Journal,RunStopped,EXIT_CODES,atomic_save,file_identity,research_identity,diagnosis_summary
from pipeline.stages.evidence_builder.index import EvidenceIndex
from pipeline.stages.diagnosis.validation import validate_diagnosis_text


def diagnose(bundle,output,config,env_file=None,client_factory=None,resume=False,retry_incomplete=False):
    bundle,out=Path(bundle).resolve(),Path(output).resolve()
    if out.is_relative_to(bundle):raise ValueError('Output must be outside the input bundle')
    cfg=read(config)
    if any(type(cfg.get(k)) is not int or cfg[k]<1 for k in ('max_model_rounds_per_trace','max_tool_calls_per_trace')):
        raise ValueError('Round and tool limits must be positive integers')
    index=EvidenceIndex(bundle)
    if not index.traces:raise ValueError('Bundle contains no traces')
    native=load_native();prompt=(ROOT/'configs/diagnosis/diagnosis.prompt.md').read_text()
    settings={k:v for k,v in cfg.items() if k not in ('max_model_rounds_per_trace','max_tool_calls_per_trace')}
    identity={'kind':'portable_diagnosis','bundle':file_identity(bundle),'model_settings':settings,
              'prompt':prompt,'tools':native.TOOLS,'research_implementation':research_identity(ROOT),'implementation':{name:digest(ROOT/name) for name in
                  ('reproduction/diagnose.py','reproduction/run_state.py','research_runners/diagnosis/native_runtime.py')}}
    with Journal(out,identity,resume,retry_incomplete) as journal:
        plan={**cfg,'config_sha256':digest(config),'runtime_kind':'portable resumable native-tool loop',
              'historical_prefix_reused':False,'diagnosis_acceptance':'codetracer-detailed-compatible-v1',
              'legacy_strict_audit_applied':False}
        if not resume:atomic_save(out/'plan.json',plan)
        atomic_save(out/'.resume/session_plans'/f'{journal.session}.json',plan)
        client=None
        if client_factory is None:
            loader=native.provider_config
            native.provider_config=lambda _:loader(Path(env_file))
        def ask(messages,folder):
            nonlocal client
            if client is None:client=client_factory() if client_factory is not None else native.NativeClient()
            # Scope the unchanged native client's duplicate-request reservation to this one attempt.
            atomic_save(folder.parent/'plan.json',plan);previous=native.ROOT;native.ROOT=folder.parent
            try:return list(client.ask(messages,folder))
            finally:native.ROOT=previous
        def recover(messages,folder):
            request,response,receipt=[read(folder/name) for name in ('request.json','response.json','receipt.json')]
            expected={'model':cfg['model'],'messages':messages,'tools':native.TOOLS,'tool_choice':'auto',
                      'max_tokens':cfg['max_tokens'],'thinking_token_budget':cfg['thinking_token_budget'],'temperature':0}
            if request!=expected or receipt.get('status') in ('provider_failure','provider_or_identity_failure'):
                return None
            if response.get('model')!=cfg['expected_returned_model'] or receipt.get('returned_model')!=response['model']:
                return None
            if receipt.get('request_sha256')!=native.signature(request):return None
            choice=response['choices'][0]
            if choice['finish_reason'] not in ('stop','tool_calls','length'):return None
            return [choice['message'],choice['finish_reason'],receipt]
        results=[]
        for rid in index.traces:
            folder=out/'results'/rid;folder.mkdir(parents=True,exist_ok=True)
            safe=native.SafeEvidence(index,rid);initial=safe.initial();atomic_save(folder/'initial_input.json',initial)
            messages=[{'role':'system','content':prompt},{'role':'user','content':'CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n'+native.encode(initial)}]
            state={'record_id':rid,'status':'running','model_rounds':0,'tool_calls':0,'automatic_retries':0,
                   'acceptance_standard':'codetracer-detailed-compatible-v1','legacy_strict_audit_applied':False}
            try:
                for round_no in range(1,cfg['max_model_rounds_per_trace']+1):
                    def before():
                        if (out/'pause.requested').exists():raise RunStopped('paused','pause_requested')
                    if (out/'pause.requested').exists():raise RunStopped('paused','pause_requested')
                    request={'messages':messages,'model_settings':settings,'tools':native.TOOLS}
                    message,finish,receipt=journal.call(['diagnosis',rid,round_no],request,
                        lambda path:ask(messages,path),folder/'calls'/f'{round_no:02}',before,
                        lambda path:recover(messages,path))
                    state['model_rounds']=round_no
                    if finish=='length':state.update(status='output_truncated',reason='provider_finish_reason_length');break
                    calls=message.get('tool_calls') or []
                    if calls:
                        assistant={k:message[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in message}
                        assistant.setdefault('role','assistant');messages.append(assistant)
                        for call in calls:
                            if state['tool_calls']>=cfg['max_tool_calls_per_trace']:
                                raise RunStopped('budget_exhausted','tool_budget_exhausted')
                            name=call['function']['name']
                            try:
                                args=json.loads(call['function']['arguments'])
                                schema=next(t['function']['parameters'] for t in native.TOOLS if t['function']['name']==name)
                                jsonschema.validate(args,schema);result=safe.invoke(name,args)
                            except (ValueError,KeyError,StopIteration,jsonschema.ValidationError) as exc:
                                args=None;result={'status':'invalid_tool_arguments','error_type':type(exc).__name__}
                            safe.note_read(result);state['tool_calls']+=1
                            atomic_save(folder/'tool_reads'/f"{state['tool_calls']:03}.json",{'round':round_no,'name':name,'arguments':args,'result':result})
                            messages.append({'role':'tool','tool_call_id':call['id'],'content':native.encode(result)})
                        continue
                    text=message.get('content') or ''
                    if not isinstance(text,str):
                        state.update(status='invalid_json',reason='native_output_is_not_text');break
                    (folder/'native_final.txt').write_text(text)
                    acceptance=validate_diagnosis_text(text,index.steps[rid]);atomic_save(folder/'mechanical_acceptance.json',acceptance)
                    try:data=json.loads(text)
                    except ValueError:state.update(status='invalid_json',reason='native_output_is_not_json')
                    else:
                        atomic_save(folder/'diagnosis.json',data)
                        state['status']='mechanically_passed' if acceptance['passed'] else 'mechanically_failed'
                    break
                else:state.update(status='round_budget_exhausted',reason='no_final_diagnosis_within_round_budget')
            except RunStopped as exc:
                state.update(status='tool_budget_exhausted' if exc.status=='budget_exhausted' else exc.status,reason=exc.reason)
            except KeyboardInterrupt:state.update(status='paused',reason='interrupted_by_user')
            except Exception as exc:state.update(status='execution_failed',reason='Local execution stopped; inspect retained outputs.',error_type=type(exc).__name__)
            atomic_save(folder/'read_ranges.json',safe.read_ranges)
            atomic_save(folder/'registered_extra_sources.json',[{k:v for k,v in e.items() if k!='text'} for e in safe.extra.values()])
            atomic_save(folder/'result.json',state);results.append(state)
            summary={**diagnosis_summary(results),'traces_total':len(index.traces),**journal.stats()}
            atomic_save(out/'progress.json',{'traces':results,**summary})
            atomic_save(out/'run_summary.json',summary)
            if state['status'] not in ('mechanically_passed','mechanically_failed'):break
        return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/diagnosis/reproduction.json')
    p.add_argument('--env-file',type=Path,required=True);p.add_argument('--allow-network',action='store_true')
    p.add_argument('--resume',action='store_true');p.add_argument('--retry-incomplete-calls',action='store_true')
    args=p.parse_args()
    if not args.allow_network:p.error('Live model calls require --allow-network')
    if args.retry_incomplete_calls and not args.resume:p.error('--retry-incomplete-calls requires --resume')
    try:
        results=diagnose(args.bundle,args.output,args.config,args.env_file,resume=args.resume,
                         retry_incomplete=args.retry_incomplete_calls)
        summary=read(args.output/'run_summary.json')
        summary['results']=results
    except RunStopped as exc:summary={'status':exc.status,'reason':exc.reason,'exit_code':EXIT_CODES[exc.status]}
    except (OSError,ValueError,KeyError) as exc:
        summary={'status':'invalid_input','reason':'Check input paths, configuration and saved records.',
                 'error_type':type(exc).__name__,'exit_code':2}
    print(json.dumps(summary,ensure_ascii=False));raise SystemExit(summary['exit_code'])

if __name__=='__main__':main()
