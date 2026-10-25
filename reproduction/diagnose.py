"""Portable fresh diagnosis using the recorded native tool/evidence implementation.

This is a release adapter, not a reconstruction of an unavailable model output.
Historical paired-continuation runners remain in research_runners/diagnosis.
"""
import argparse
import copy
import json
from pathlib import Path
import jsonschema
from .common import ROOT,read,save,digest,load_native
from pipeline.stages.evidence_builder.index import EvidenceIndex
from pipeline.stages.diagnosis.validation import validate_diagnosis_text


def diagnose(bundle,output,config,env_file=None,client_factory=None):
    cfg=read(config);out=Path(output).resolve()
    index=EvidenceIndex(bundle)
    native=load_native();native.ROOT=out
    # Original credential parsing and native request/receipt logic are retained.
    if client_factory is None:
        loader=native.provider_config
        native.provider_config=lambda _:loader(Path(env_file))
        client_factory=native.NativeClient
    out.mkdir(parents=True,exist_ok=False)
    save(out/'plan.json',{**cfg,'config_sha256':digest(config),
        'runtime_kind':'portable fresh native-tool loop','historical_prefix_reused':False})
    client=client_factory()
    prompt=(ROOT/'configs/diagnosis/diagnosis.prompt.md').read_text()
    results=[]
    for rid in index.traces:
        folder=out/'results'/rid;folder.mkdir(parents=True,exist_ok=False)
        safe=native.SafeEvidence(index,rid)
        initial=safe.initial();save(folder/'initial_input.json',initial)
        messages=[{'role':'system','content':prompt},{'role':'user','content':'CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n'+native.encode(initial)}]
        state={'record_id':rid,'status':'running','model_rounds':0,'tool_calls':0,'automatic_retries':0}
        try:
            for round_no in range(1,cfg['max_model_rounds_per_trace']+1):
                if (out/'pause.requested').exists():raise InterruptedError('pause_requested')
                message,finish,receipt=client.ask(messages,folder/'calls'/f'{round_no:02}')
                state['model_rounds']=round_no
                if finish=='length':state['status']='output_truncated';break
                calls=message.get('tool_calls') or []
                if calls:
                    assistant={k:message[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in message}
                    assistant.setdefault('role','assistant');messages.append(assistant)
                    for call in calls:
                        if state['tool_calls']>=cfg['max_tool_calls_per_trace']:raise RuntimeError('tool_budget_exhausted')
                        name=call['function']['name']
                        try:
                            args=json.loads(call['function']['arguments'])
                            schema=next(t['function']['parameters'] for t in native.TOOLS if t['function']['name']==name)
                            jsonschema.validate(args,schema);result=safe.invoke(name,args)
                        except (ValueError,KeyError,StopIteration,jsonschema.ValidationError) as exc:
                            args=None;result={'status':'invalid_tool_arguments','error_type':type(exc).__name__}
                        safe.note_read(result);state['tool_calls']+=1
                        save(folder/'tool_reads'/f"{state['tool_calls']:03}.json",{'round':round_no,'name':name,'arguments':args,'result':result})
                        messages.append({'role':'tool','tool_call_id':call['id'],'content':native.encode(result)})
                    continue
                text=message.get('content') or ''
                (folder/'native_final.txt').write_text(text)
                acceptance=validate_diagnosis_text(text,index.steps[rid]);save(folder/'mechanical_acceptance.json',acceptance)
                try:data=json.loads(text)
                except ValueError:state['status']='invalid_json'
                else:
                    save(folder/'diagnosis.json',data)
                    save(folder/'strict_schema_and_read_audit.json',native.validate_diagnosis(data,safe))
                    state['status']='mechanically_passed' if acceptance['passed'] else 'mechanically_failed'
                break
            else:state['status']='round_budget_exhausted'
        except Exception as exc:
            state.update(status='stopped',error_type=type(exc).__name__)
        save(folder/'read_ranges.json',safe.read_ranges)
        save(folder/'registered_extra_sources.json',[{k:v for k,v in e.items() if k!='text'} for e in safe.extra.values()])
        save(folder/'result.json',state);results.append(state)
        save(out/'progress.json',{'traces':results,'semantic_accuracy_measured':False})
        if state['status'] not in ('mechanically_passed','mechanically_failed'):break
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/diagnosis/reproduction.json')
    p.add_argument('--env-file',type=Path,required=True);p.add_argument('--allow-network',action='store_true')
    a=p.parse_args()
    if not a.allow_network:p.error('Live model calls require --allow-network')
    print(json.dumps(diagnose(a.bundle,a.output,a.config,a.env_file),ensure_ascii=False))
if __name__=='__main__':main()
