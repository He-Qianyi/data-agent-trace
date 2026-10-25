"""Stock Codex native request/response bundle adapter; no relay or inference."""
import json
from pathlib import Path
from curate_complete_traces import records,dump,digest


def payload_source(trace, ref):
    q=(trace.parent/ref['path']).resolve()
    if trace.parent.resolve() not in q.parents:
        raise ValueError('Unsafe native payload pointer')
    return q,json.loads(q.read_text())


def extract(root, output):
    steps=[];unresolved=[];traces=[];tools=[];unresolved_tools=[]
    for path in sorted(Path(root).glob('trace-*/trace.jsonl')):
        starts={};ends={};tool_starts={};tool_ends={};traces.append(str(path))
        for e in records(path):
            p=e.get('payload',{});kind=p.get('type');cid=p.get('inference_call_id')
            if kind=='inference_started':
                if cid in starts:raise ValueError('Duplicate inference ID')
                starts[cid]=(e,p)
            elif kind=='inference_completed':ends[cid]=(e,p)
            elif kind=='tool_call_started':
                tid=p['tool_call_id']
                if tid in tool_starts:raise ValueError('Duplicate native tool start')
                tool_starts[tid]=(e,p)
            elif kind=='tool_call_ended':
                tid=p['tool_call_id']
                if tid in tool_ends:raise ValueError('Duplicate native tool end')
                tool_ends[tid]=(e,p)
        for cid,(e,p) in starts.items():
            if cid not in ends:unresolved.append(cid);continue
            re,rp=ends[cid]
            req_path,req=payload_source(path,p['request_payload'])
            res_path,res=payload_source(path,rp['response_payload'])
            steps.append({'step':len(steps)+1,'native_capture_type':'codex_rollout_trace',
                'inference_call_id':cid,'model_input':req,'model_output':res,
                'recording_provenance':{'trace':str(path),'request_seq':e['seq'],'response_seq':re['seq'],
                    'request_path':str(req_path),'response_path':str(res_path),
                    'request_sha256':digest(req_path),'response_sha256':digest(res_path)}})
        unresolved.extend(cid for cid in ends if cid not in starts)
        for tid,(event,start) in tool_starts.items():
            if tid not in tool_ends:
                unresolved_tools.append(tid);continue
            end_event,end=tool_ends[tid]
            ip,invocation=payload_source(path,start['invocation_payload'])
            op,result=payload_source(path,end['result_payload'])
            tools.append({'schema':'codex-native-tool-execution-v1','tool_call_id':tid,
                'model_visible_call_id':start.get('model_visible_call_id'),
                'requester':start.get('requester'),'status':end.get('status'),
                'invocation':invocation,'result':result,
                'recording_provenance':{'trace':str(path),'start_seq':event['seq'],'end_seq':end_event['seq'],
                    'invocation_path':str(ip),'invocation_sha256':digest(ip),
                    'result_path':str(op),'result_sha256':digest(op)}})
        unresolved_tools.extend(tid for tid in tool_ends if tid not in tool_starts)
    dump(output,steps,lines=True)
    tool_output=Path(output).with_name('tool_execution_io.jsonl')
    dump(tool_output,tools,lines=True)
    return {'model_requests':len(steps),'unresolved_inference_ids':unresolved,'native_traces':traces,
            'native_tool_executions':len(tools),'unresolved_native_tool_ids':unresolved_tools,
            'tool_execution_ledger':str(tool_output),
            'request_body_recorded':True,'transport_relay':False,
            'request_output_budget_values':[r['model_input'].get('max_output_tokens') for r in steps]}


def verify_tool_ledger(path, allowed_traces):
    """Validate exact native start/end IDs and raw payloads, not model replay.

Never insert an execution result into a model request. A result can be fully
recorded even when compaction removes it before another inference request.
"""
    calls=set();outputs=set();errors=set();cache={}
    for r in records(path):
        try:
            if r.get('schema')!='codex-native-tool-execution-v1':raise ValueError('Wrong schema')
            proof=r['recording_provenance'];trace=Path(proof['trace'])
            if str(trace) not in allowed_traces:raise ValueError('Unbound native session')
            if str(trace) not in cache:
                events=list(records(trace))
                if len({e['seq'] for e in events})!=len(events):raise ValueError('Duplicate native sequence')
                cache[str(trace)]={e['seq']:e['payload'] for e in events}
            start=cache[str(trace)][proof['start_seq']];end=cache[str(trace)][proof['end_seq']]
            if proof['start_seq']>=proof['end_seq']:raise ValueError('Invalid event order')
            if start['type']!='tool_call_started' or end['type']!='tool_call_ended':raise ValueError('Wrong event types')
            if start['tool_call_id']!=r['tool_call_id'] or end['tool_call_id']!=r['tool_call_id']:
                raise ValueError('Native tool ID mismatch')
            if (start.get('model_visible_call_id')!=r.get('model_visible_call_id')
                    or start.get('requester')!=r.get('requester') or end.get('status')!=r.get('status')):
                raise ValueError('Native metadata mismatch')
            for label,ref in (('invocation',start['invocation_payload']),('result',end['result_payload'])):
                p,raw=payload_source(trace,ref)
                if str(p)!=proof[label+'_path'] or digest(p)!=proof[label+'_sha256'] or raw!=r[label]:
                    raise ValueError('Native body/provenance mismatch')
            if r['status'] not in ('completed','failed','cancelled'):
                raise ValueError('Nonterminal tool execution')
            # Host/internal runtime tools remain in the raw ledger, but are not
            # counted as model-visible API function calls without an explicit ID.
            cid=r.get('model_visible_call_id')
            if cid:
                calls.add(cid)
                result=r['result']
                if result.get('type')=='error':
                    # A failed runtime invocation has a typed error payload,
                    # not a response_item. Its call identity is proved by the
                    # exact native start/end and source hashes above. Count
                    # recorded terminal executions, not invented API messages.
                    if r['status']!='failed' or not isinstance(result.get('error'),str) or not result['error'].strip():
                        raise ValueError('Invalid native error terminal')
                else:
                    item=result.get('response_item',{})
                    if item.get('type') not in ('function_call_output','custom_tool_call_output') or item.get('call_id')!=cid:
                        raise ValueError('Model-visible native result mismatch')
                outputs.add(cid)
        except (KeyError,ValueError,TypeError,OSError,AttributeError):
            errors.add('native_tool_source_proof_invalid')
    return calls,outputs,sorted(errors)
