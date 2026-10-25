"""Separate recorded model tool generation from CLI-observed execution.

Never classify request intent from prompt keywords, invent tool outputs, or
edit model I/O. Auxiliary responses remain in the full exchange ledger.
"""
from pathlib import Path
from curate_complete_traces import records, digest, dump, WIRE_KEYS


def collect(ledger):
    ledger=Path(ledger);parent=ledger.parent
    events=parent/'events.jsonl';wire=parent/'relay_wire.jsonl'
    raw={}
    for e in records(wire):
        if e.get('event') in WIRE_KEYS:
            key=(e.get('trace_id'),e['event'])
            if key in raw:raise ValueError('Duplicate relay side')
            raw[key]=e['payload']
    responses={};generated=set();replayed=set();replayed_outputs=set();seen=set()
    for row in records(ledger):
        tid=row['trace_id']
        if tid in seen:raise ValueError('Duplicate exchange')
        seen.add(tid)
        for key in WIRE_KEYS:
            if key not in row:raise ValueError('Recorded relay exchange missing response; original capture incomplete')
            if row[key]!=raw.get((tid,key)):raise ValueError('Exchange not bound to raw relay')
        response=row['outgoing_anthropic_response'];rid=response['id']
        if rid in responses:raise ValueError('Duplicate response identity')
        responses[rid]=response
        for c in response['content']:
            if c.get('type')=='tool_use':generated.add(c['id'])
        for msg in row['incoming_anthropic_request']['messages']:
            content=msg.get('content',[])
            for c in content if isinstance(content,list) else []:
                if c.get('type')=='tool_use':replayed.add(c['id'])
                if c.get('type')=='tool_result':replayed_outputs.add(c['tool_use_id'])
    if {tid for tid,_ in raw}!=seen:raise ValueError('Raw request not all covered')
    cli=list(records(events))
    if not cli or cli[-1].get('type')!='result' or cli[-1].get('is_error') is not False or cli[-1].get('terminal_reason')!='completed':
        raise ValueError('CLI not normally completed')
    accepted=set();observed_outputs=set();sessions=set()
    for e in cli:
        if e.get('session_id'):sessions.add(e['session_id'])
        msg=e.get('message',{});content=msg.get('content',[])
        if e.get('type')=='assistant':
            response=responses.get(msg.get('id'))
            if response is None:raise ValueError('CLI assistant missing from full exchanges')
            for c in content if isinstance(content,list) else []:
                if c.get('type')=='tool_use':
                    if c not in response['content']:raise ValueError('CLI tool call body mismatch')
                    accepted.add(c['id'])
        if e.get('type')=='user':
            for c in content if isinstance(content,list) else []:
                if c.get('type')=='tool_result':observed_outputs.add(c['tool_use_id'])
    if len(sessions)!=1:raise ValueError('Mixed or absent CLI session')
    executed=accepted|replayed
    outputs=observed_outputs|replayed_outputs
    # These are explicitly "not observed", not proof of hidden SDK behavior
    # or claims about why a request occurred. Their original payloads stay put.
    unobserved=generated-executed
    hashes={n:digest(parent/n) for n in ('events.jsonl','relay_wire.jsonl',ledger.name)}
    return {'schema':'claude-wire-cli-execution-proof-v1','source_sha256':hashes,
        'cli_session_id':next(iter(sessions)),
        'generated_tool_ids':sorted(generated),'execution_observed_tool_ids':sorted(executed),
        'cli_tool_result_ids':sorted(observed_outputs),'recorded_tool_result_ids':sorted(outputs),
        'generated_tool_ids_not_observed_as_execution':sorted(unobserved),
        'hidden_sdk_execution_certified':False,'model_input_modified':False}


def extract(ledger):
    proof=collect(ledger)
    dump(Path(ledger).with_name('wire_execution_proof.json'),proof)
    return proof


def verify(ledger):
    from curate_complete_traces import read
    proof=collect(ledger)
    if proof!=read(Path(ledger).with_name('wire_execution_proof.json')):
        raise ValueError('Execution proof differs from original sources')
    return (set(proof['execution_observed_tool_ids']),set(proof['recorded_tool_result_ids']),
            proof['generated_tool_ids_not_observed_as_execution'])
