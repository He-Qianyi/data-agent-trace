#!/usr/bin/env python3
"""Offline, reversible corpus curation; never invent semantic trace content.

The quality gate concerns recording integrity, not whether the agent was right.
All existing raw sources stay recoverable. Frozen annotation inputs are not edited.
"""
import argparse
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

import build_trace_processor_v2_manifest as inventory

PROJECT = Path(__file__).resolve().parents[1]
RUNS = PROJECT / 'runs'
VERSION = 'recording-integrity-v3-20261013'
WIRE_KEYS = ('incoming_anthropic_request', 'outgoing_soclaas_responses_request',
             'incoming_soclaas_responses_response', 'outgoing_anthropic_response')


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                value = json.loads(line)
                for row in value if isinstance(value, list) else [value]:
                    if not isinstance(row, dict):
                        raise ValueError('Non-object record')
                    yield row


def dump(path, value, lines=False):
    path = Path(path)
    mount = Path(__file__).resolve().parents[1]
    if (path == mount or mount in path.parents) and not mount.is_dir():
        raise OSError('External recording disk unavailable; no replacement path created')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.writing')
    with tmp.open('w') as f:
        if lines:
            for row in value:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
    os.replace(tmp, path)


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def evaluation(row):
    """Decode explicit evaluator schemas only; score zero is valid."""
    for p in row['evaluation_refs']:
        d = read(p)
        if not isinstance(d, dict):
            continue
        if d.get('status') == 'evaluated' and number(d.get('score')):
            return {'path': p, 'score': d['score']}
        if d.get('evaluator_exit_code') == 0 and number(d.get('score')):
            return {'path': p, 'score': d['score']}
        if d.get('status') == 'completed' and number(d.get('average_score')):
            return {'path': p, 'score': d['average_score']}
        for t in d.get('tasks', []) + d.get('results', []):
            if t.get('task_id', t.get('id')) != row['task_id']:
                continue
            score = t.get('score', t.get('total_score'))
            if number(score) and t.get('finished', True) is not False:
                return {'path': p, 'score': score}
    return None


def location(row):
    p = Path(row['sources'][0]['path'])
    if row['benchmark'] == 'longds':
        return p.parents[2], p.parents[2]
    if p.parent.name == 'dabench':
        return p.parents[1], p.parents[1]
    if p.parent.name.startswith('attempt-'):
        return p.parent, p.parents[2] if p.parents[1].name=='attempts' else p.parents[1]
    if p.parent.name == 'run_1':
        return p.parent, p.parents[1]
    return p.parent, p.parent


def terminal(attempt, task, row):
    for p in (attempt/'result.json', attempt/'status.json', attempt/'run.json', task/'task_state.json'):
        d = read(p)
        if not isinstance(d, dict):
            continue
        if row['benchmark'] == 'longds' and p.name == 'task_state.json':
            return d.get('status') == 'completed'
        if 'return_code' in d or 'exit_code' in d:
            return (d.get('return_code', d.get('exit_code')) == 0
                    and not d.get('timed_out', False)
                    and not d.get('provider_error_status')
                    and d.get('run_status', d.get('status')) not in
                    {'interrupted', 'gateway_error_stopped', 'paused_provider_error',
                     'paused_infrastructure_error', 'failed_timeout'})
    # Legacy benchmark result.finished means evaluator completion, NOT CLI completion.
    return False


def tool_pair_error(calls, results):
    return bool(calls - results or results - calls)


def check_ledger(path):
    """Structural source adapters, not benchmark-specific semantic heuristics."""
    count = 0
    errors = set()
    calls, outputs = set(), set()
    representation = None
    assistant_ids = set()
    native_traces=set()
    for r in records(path):
        count += 1
        if r.get('native_capture_type') == 'claude_chat_four_side_v1':
            # The Chat adapter validates all four actual wire bodies, exact
            # conversion, every exchange, one normal CLI session and executed
            # tool-result pairing. It does not masquerade as Responses data.
            try:
                from claude_soclaas_chat_capture import verify_capture
                return verify_capture(Path(path).parent, save=False)
            except (ValueError, KeyError, TypeError, OSError, AttributeError):
                return {'representation': 'anthropic_chat_four_side_wire_and_cli_execution',
                        'errors': ['chat_wire_cli_source_proof_invalid']}
        if r.get('native_capture_type')=='direct_serialized_fetch':
            representation='direct_serialized_request_response'
            req=r.get('model_input',{});res=r.get('model_output',{})
            if not req.get('model') or not req.get('input'):
                errors.add('native_input_context_missing')
            if r.get('metadata',{}).get('http_status')!=200:
                errors.add('provider_failure')
            if res.get('status')!='completed' or not res.get('output'):
                errors.add('native_response_not_complete')
            inputs=req.get('input',[])
            for e in (inputs if isinstance(inputs,list) else [])+res.get('output',[]):
                if e.get('type') in ('function_call','custom_tool_call'):calls.add(e.get('call_id'))
                if e.get('type') in ('function_call_output','custom_tool_call_output'):outputs.add(e.get('call_id'))
        elif r.get('native_capture_type')=='codex_rollout_trace':
            representation='native_serialized_request_ledger'
            req=r.get('model_input',{});res=r.get('model_output',{})
            if r.get('recording_provenance',{}).get('trace'):
                native_traces.add(r['recording_provenance']['trace'])
            if not req.get('input') or 'tools' not in req or not req.get('model'):
                errors.add('native_input_context_missing')
            if not res.get('response_id') or not res.get('output_items'):
                errors.add('native_response_not_complete')
            for e in req.get('input',[])+res.get('output_items',[]):
                if e.get('type') in ('function_call','custom_tool_call'):calls.add(e.get('call_id'))
                if e.get('type') in ('function_call_output','custom_tool_call_output'):outputs.add(e.get('call_id'))
        elif all(k in r for k in WIRE_KEYS):
            representation = 'full_wire_exchange'
            req, res = r[WIRE_KEYS[0]], r[WIRE_KEYS[3]]
            up = r[WIRE_KEYS[2]]
            if not all(isinstance(r[k], dict) and r[k] for k in WIRE_KEYS):
                errors.add('wire_side_missing'); continue
            if not isinstance(req.get('messages'), list) or not req.get('system') or not req.get('tools'):
                errors.add('wire_input_context_missing')
            if up.get('status') not in (None, 'completed') or res.get('stop_reason') in ('max_tokens', 'pause_turn'):
                errors.add('model_response_not_complete')
            if r.get('metadata', {}).get('http_status', 200) != 200:
                errors.add('provider_failure')
            if not isinstance(res.get('content'), list):
                errors.add('wire_response_body_missing')
            if res.get('id'): assistant_ids.add(res['id'])
            for msg in req.get('messages', []) + [res]:
                content = msg.get('content', [])
                if not isinstance(content, list): continue
                for c in content:
                    if not isinstance(c, dict): continue
                    if c.get('type') == 'tool_use': calls.add(c.get('id'))
                    if c.get('type') == 'tool_result': outputs.add(c.get('tool_use_id'))
        elif 'request' in r and 'response_events' in r:
            representation = 'native_request_context_ledger'
            mi = r.get('model_input', {})
            req = r.get('request', {})
            # Native loop context contains one assistant message per completed
            # step plus one input message per tool result. Base messages alone
            # are not the full request message count; preserve all loop events.
            prior = [x.get('event', {}) for x in mi.get('prior_loop_events', [])]
            replay_count = len(mi.get('messages', [])) + sum(
                e.get('type') in ('step.end', 'tool.result') for e in prior)
            if (not mi.get('system_prompt') or not mi.get('tools') or not mi.get('messages')
                    or req.get('messageCount') != replay_count):
                errors.add('native_input_context_missing_or_count_mismatch')
            ends = []
            visible_output = False
            for e in r['response_events']:
                e = e.get('event', {}) if e.get('type') == 'context.append_loop_event' else e
                if e.get('type') == 'step.end': ends.append(e)
                if e.get('type') in ('content.part', 'tool.call'): visible_output = True
                if e.get('type') == 'tool.call': calls.add(e.get('toolCallId'))
                if e.get('type') == 'tool.result': outputs.add(e.get('toolCallId'))
            if len(ends) != 1 or not visible_output: errors.add('native_response_not_complete')
            if any(e.get('providerFinishReason') == 'length' for e in ends):
                errors.add('model_response_not_complete')
        elif 'step_start' in r and 'model_output' in r:
            representation = 'native_request_context_ledger'
            header = (r.get('request_header') or {}).get('header', {})
            mi = r.get('model_input')
            if not header.get('tools') or not mi or not any(e.get('type') == 'system/message' for e in mi):
                errors.add('native_input_context_missing')
            if not r.get('model_output') or not any(e.get('type') == 'step/end' for e in r.get('response_events', [])):
                errors.add('native_response_not_complete')
            for e in r.get('response_events', []):
                d = e.get('data', {})
                msg = d.get('message', {})
                if e.get('type') == 'tool/call':
                    # Call IDs are explicit fields; no description/text interpretation.
                    cid = d.get('toolCallId', d.get('callId', d.get('id', (d.get('call') or {}).get('id'))))
                    if cid: calls.add(cid)
                if e.get('type') in ('tool/result', 'tool/output'):
                    if msg.get('toolCallId'): outputs.add(msg['toolCallId'])
            out = (r.get('model_output') or {}).get('data', {}).get('message', {})
            for c in out.get('content', []) if isinstance(out.get('content'), list) else []:
                if isinstance(c, dict) and c.get('type') in ('tool-call', 'tool_call', 'tool_use'):
                    cid = c.get('toolCallId', c.get('id'))
                    if cid: calls.add(cid)
        else:
            errors.add('no_request_scoped_full_input_output')
    replay_outputs=set(outputs)
    native_outputs=set()
    generated_not_observed=[]
    wire_proof=Path(path).with_name('wire_execution_proof.json')
    if representation=='full_wire_exchange' and wire_proof.is_file():
        try:
            from claude_execution_capture import verify
            calls,outputs,generated_not_observed=verify(path)
        except (ValueError,KeyError,TypeError,OSError):
            errors.add('wire_cli_execution_source_proof_invalid')
    tool_sidecar=Path(path).with_name('tool_execution_io.jsonl')
    if representation=='native_serialized_request_ledger' and tool_sidecar.is_file():
        from codex_complete_capture import verify_tool_ledger
        native_calls,native_outputs,tool_errors=verify_tool_ledger(tool_sidecar,native_traces)
        calls.update(native_calls);outputs.update(native_outputs);errors.update(tool_errors)
    if not count: errors.add('empty_ledger')
    if tool_pair_error(calls, outputs): errors.add('tool_call_result_pairing_unverified')
    return {'representation': representation, 'model_requests': count,
            'errors': sorted(errors), 'assistant_ids': sorted(assistant_ids),
            'tool_calls': len(calls), 'tool_results': len(outputs),
            'tool_results_observed_natively':len(native_outputs),
            'native_result_ids_absent_from_model_replay':sorted(native_outputs-replay_outputs),
            'generated_tool_ids_not_observed_as_execution':generated_not_observed,
            'wire_exact': representation == 'full_wire_exchange',
            'hidden_reasoning_available': False, 'semantic_quality_certified': False}


def longds_index():
    """Discover task-bound full relay records; never bind on fuzzy text or time."""
    result = {}
    for name in ('longds-claude-soclaas-qwen36-v1', 'longds-claude-soclaas-qwen36-v1.1-remaining'):
        path = RUNS/name/'runtime/soclaas_anthropic_relay.jsonl'
        groups = {}
        if path.is_file():
            for line, r in enumerate(records(path), 1):
                if r.get('trace_id') and r.get('task_id') and r.get('event') in WIRE_KEYS:
                    k = (r['task_id'], r['trace_id'])
                    groups.setdefault(k, {})[r['event']] = {'line': line, 'payload': r.get('payload')}
        for (task, tid), sides in groups.items():
            result.setdefault((name, task), []).append({'trace_id': tid, 'sides': sides, 'source_path': str(path)})
    return result


def audit(row, relay_index, output):
    attempt, task = location(row)
    answer = {'trace_id': row['trace_id'], 'benchmark': row['benchmark'], 'agent_id': row['agent_id'],
              'task_id': row['task_id'], 'run_id': row['run_id'], 'attempt': str(attempt),
              'task_directory': str(task), 'reasons': [], 'original': row,
              'sources_added': [], 'decision': 'quarantine'}
    if RUNS not in task.parents:
        answer['reasons'].append('outside_project_formal_runs'); return answer
    ev = evaluation(row)
    answer['evaluation'] = ev
    if not ev: answer['reasons'].append('valid_evaluation_not_found')
    if not terminal(attempt, task, row): answer['reasons'].append('normal_terminal_not_verified')
    sources = list(row['sources'])
    for root in dict.fromkeys([attempt, task]):
        for name, role in [('prompt.txt', 'task_prompt'), ('prompt.md', 'task_prompt'),
                           ('session_raw.jsonl', 'raw_native_session'), ('result.json', 'run_result'),
                           ('status.json', 'run_status'), ('provider.json', 'provider_metadata')]:
            p = root/name
            if p.is_file() and p.stat().st_size and str(p) not in [x['path'] for x in sources]:
                sources.append({'path': str(p), 'role': role}); answer['sources_added'].append(str(p))
    ledger = attempt/'turn_model_io.jsonl'
    if row['benchmark'] == 'longds':
        groups = relay_index.get((row['run_id'], row['task_id']), [])
        recovered = []
        for n, group in enumerate(groups, 1):
            recovered.append({'turn': n, 'trace_id': group['trace_id'],
                              **{k: v['payload'] for k, v in group['sides'].items()},
                              'recovery_provenance': {'source_path': group['source_path'],
                                                      'source_lines': {k: v['line'] for k, v in group['sides'].items()}}})
        if recovered:
            ledger = output/'recovered_sources'/row['trace_id']/'turn_model_io.jsonl'
            dump(ledger, recovered, lines=True)
            answer['sources_added'].append(str(ledger))
        for s in row['sources']:
            p = Path(s['path']).parent/'prompt.md'
            if p.is_file(): sources.append({'path': str(p), 'role': 'task_prompt'})
    if not any(s['role'] == 'task_prompt' for s in sources): answer['reasons'].append('task_prompt_not_registered_or_found')
    try:
        if ledger.is_file():
            capture = check_ledger(ledger)
            answer['capture'] = capture
            answer['reasons'].extend(capture['errors'])
            sources.append({'path': str(ledger), 'role': 'model_io'})
            if capture['representation'] == 'full_wire_exchange':
                native_ids = set()
                for s in row['sources']:
                    if s['role'] != 'transcript': continue
                    for e in records(s['path']):
                        if e.get('type') == 'assistant' and isinstance(e.get('message'), dict):
                            if e['message'].get('id'): native_ids.add(e['message']['id'])
                answer['assistant_output_ids_count'] = len(native_ids)
                if not native_ids or native_ids - set(capture['assistant_ids']):
                    answer['reasons'].append('wire_not_covering_all_recorded_assistant_outputs')
            # Cross-check request counts when supplied by the original runner.
            meta = read(attempt/'result.json') or {}
            expected = (meta.get('trace_counts') or {}).get('model_requests')
            if expected is not None and expected != capture['model_requests']:
                answer['reasons'].append('request_count_mismatch')
        else: answer['reasons'].append('full_request_scoped_capture_missing')
        for s in sources:
            p = Path(s['path'])
            if not p.is_file(): answer['reasons'].append('registered_source_missing'); continue
            if p.suffix == '.jsonl':
                for _ in records(p): pass
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        answer['reasons'].append('source_parse_or_adapter_failure:' + type(exc).__name__)
    answer['reasons'] = sorted(set(answer['reasons']))
    if not answer['reasons']:
        answer['decision'] = 'retain_recording_integrity_passed'
    unique = {s['path']: s for s in sources}
    answer['assembled_sources'] = list(unique.values())
    return answer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--plan-moves', action='store_true')
    args = ap.parse_args()
    if not Path(__file__).resolve().parents[1].is_dir() or not RUNS.is_dir():
        raise RuntimeError('Expected external drive is not mounted')
    out = args.output.resolve()
    if out != PROJECT/'data'/VERSION: raise RuntimeError('Unexpected output target')
    if args.plan_moves:
        plan=prepare_moves(out)
        print(json.dumps({'targets':len(plan['targets']), 'outside_project_not_moved':plan['outside_project_traces_not_moved'],
                          'unindexed_recorded_tasks':plan['unindexed_recorded_tasks']},ensure_ascii=False));return
    if args.apply:
        apply(out); return
    if out.exists(): raise RuntimeError('Audit version already exists; do not overwrite')
    out.mkdir()
    dump(out/'policy.json', {'version': VERSION, 'concurrency': 0, 'model_calls': 0,
        'scope': 'formal runs only; repro/demo/diagnostics excluded',
        'required': ['normal runner termination', 'finite valid evaluation including zero',
                     'task prompt', 'request-scoped recorded full context and output',
                     'tool-call/result pairing', 'parseable source files'],
        'native_limit': 'Native context ledgers are not exact HTTPS payloads; hidden reasoning is not reconstructed.',
        'no_semantic_certification': True, 'no_imputation': True,
        'historical_frozen_manifests_preserved': True, 'action': 'recoverable quarantine, never permanent deletion'})
    rows, missing = inventory.build(use_current=False)
    relay = longds_index()
    audited = []
    for i, row in enumerate(rows, 1):
        audited.append(audit(row, relay, out))
        if i % 50 == 0:
            dump(out/'progress.json', {'status': 'auditing', 'completed': i, 'total': len(rows)})
            print(f'audited {i}/{len(rows)}', flush=True)
    kept = []
    for r in audited:
        if r['decision'] != 'retain_recording_integrity_passed': continue
        row = {**r['original'], 'sources': r['assembled_sources'], 'recording_quality': r['capture'],
               'integrity_version': VERSION, 'evaluation_score': r['evaluation']['score']}
        row['source_hashes'] = {s['path']: digest(s['path']) for s in row['sources']}
        kept.append(row)
    dump(out/'audit.jsonl', audited, lines=True)
    dump(out/'manifest.jsonl', kept, lines=True)
    dump(out/'quarantine_manifest.jsonl', [r for r in audited if r['decision'] == 'quarantine'], lines=True)
    dump(out/'inventory_missing.json', missing)
    counts = collections.Counter((r['benchmark'], r['agent_id'], r['decision']) for r in audited)
    dump(out/'summary.json', {'status': 'audit_complete_not_applied', 'audited': len(audited),
        'retained': len(kept), 'quarantined_index': len(audited)-len(kept), 'missing_from_inventory': len(missing),
        'groups': [{'benchmark': k[0], 'agent': k[1], 'decision': k[2], 'count': n} for k,n in sorted(counts.items())],
        'reasons': dict(collections.Counter(reason for r in audited for reason in r['reasons'])),
        'retained_zero_score': sum(r['evaluation_score'] == 0 for r in kept),
        'retained_nonzero_score': sum(r['evaluation_score'] != 0 for r in kept),
        'model_calls': 0, 'semantic_quality_certified': False})
    dump(out/'progress.json', {'status': 'audit_complete_not_applied', 'completed': len(audited), 'total': len(audited)})
    print(json.dumps(read(out/'summary.json'), ensure_ascii=False), flush=True)


def apply(out):
    plan = read(out/'move-plan.json')
    if not plan or plan.get('version') != VERSION or plan.get('applied'):
        raise RuntimeError('Missing reviewed plan, or already applied')
    if plan.get('manifest_sha256') != digest(out/'manifest.jsonl'):
        raise RuntimeError('Retained manifest changed')
    assert_no_runners()
    for row in records(out/'manifest.jsonl'):
        for path, h in row['source_hashes'].items():
            if digest(path) != h: raise RuntimeError('Retained source changed')
    targets = [Path(x['source']) for x in plan['targets']]
    for p in targets: validate_target(p)
    if any(a in b.parents for a in targets for b in targets if a != b):
        raise RuntimeError('Overlapping targets')
    archive = PROJECT/'quarantined-incomplete-traces'/VERSION
    if archive.exists(): raise RuntimeError('Archive exists; inspect journal, never blindly restart')
    # fcntl protects against the shared trace-processor runner while applying.
    import fcntl
    lock_path = Path('${PROJECT_ROOT}/.trace-processor-v2/job.lock')
    with lock_path.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_no_runners()
        archive.mkdir(parents=True)
        dump(archive/'move-plan.json', plan)
        journal = archive/'moves.jsonl'
        with journal.open('a') as j:
            for item in plan['targets']:
                src = Path(item['source']); validate_target(src)
                dst = archive/src.relative_to(RUNS)
                dst.parent.mkdir(parents=True, exist_ok=True)
                entry = {**item, 'destination': str(dst), 'state':'prepared'}
                j.write(json.dumps(entry,ensure_ascii=False)+'\n'); j.flush();os.fsync(j.fileno())
                os.rename(src, dst)
                entry['state']='moved'
                j.write(json.dumps(entry,ensure_ascii=False)+'\n'); j.flush();os.fsync(j.fileno())
        # Verify retained sources still resolve after physical isolation.
        for row in records(out/'manifest.jsonl'):
            for path,h in row['source_hashes'].items():
                if digest(path) != h: raise RuntimeError('Retained source verification failed after moves')
        plan['applied']=True; plan['archive']=str(archive)
        dump(out/'move-plan.json',plan)
        summary=read(out/'summary.json');summary['status']='completed_recoverable_quarantine'
        summary['moved_directories']=len(plan['targets']);summary['archive']=str(archive)
        summary['permanent_deletions']=0
        summary['unindexed_recorded_tasks_quarantined']=plan['unindexed_recorded_tasks']
        dump(out/'summary.json',summary)
        dump(out/'progress.json',{'status':summary['status'],'completed':summary['audited'],'total':summary['audited']})
        dump(PROJECT/'data/CURRENT_TRACE_CORPUS.json', {'version':VERSION,'manifest':str(out/'manifest.jsonl'),
            'manifest_sha256':digest(out/'manifest.jsonl'),'trace_count':summary['retained'],
            'recording_integrity_only':True,'semantic_quality_certified':False,
            'old_frozen_inputs_must_not_be_resumed':True})
        print(json.dumps(summary,ensure_ascii=False))


def validate_target(p):
    p = Path(p)
    if not p.is_dir() or p.is_symlink() or RUNS not in p.parents:
        raise RuntimeError('Unsafe target: '+str(p))
    rel = p.relative_to(RUNS)
    formal = set(inventory_run_names())
    if len(rel.parts)<3 or rel.parts[0] not in formal:
        raise RuntimeError('Not a formal task/attempt directory: '+str(p))


def assert_no_runners():
    needles=('run_trace_protocol_cycle','run_trace_processor_v2_job','run_trace_source_first.py',
             'run_dataagentbench_soclaas.py','run_longds_claude_soclaas_serial.py',
             'run_dabstep_codex_soclaas_direct.py','run_dabstep_kimi_soclaas_direct.py',
             'run_dabstep_deepseek_soclaas_direct.py','run_dabstep_claude_soclaas_relay.py')
    text=subprocess.check_output(['ps','-axo','pid=,command='],text=True)
    if any(any(n in line for n in needles) for line in text.splitlines()
           if 'curate_complete_traces.py' not in line):
        raise RuntimeError('An experiment/processor may be active; no files moved')


def inventory_run_names():
    return ('dataspace-codex-soclaas-qwen36-20260917-v1',
            'dataspace-codex-soclaas-qwen36-20260918-v2-prepared',
            'dataspace-claude-soclaas-qwen36-20260917-v1',
            'agenticdatabench-codex-soclaas-qwen36-20260917-v8',
            'agenticdatabench-kimi-soclaas-qwen36-20260920-v1',
            'agenticdatabench-claude-soclaas-qwen36-20260918-v1',
            'longds-claude-soclaas-qwen36-v1',
            'longds-claude-soclaas-qwen36-v1.1-remaining',
            'dataagentbench-soclaas-20261004',
            *(f'dabstep-{a}-soclaas-{m}-qwen36-20261004-v1' for a,m in
              [('codex','direct'),('claude','relay'),('kimi','direct'),('deepseek','direct')]))


def prepare_moves(out):
    audited=list(records(out/'audit.jsonl'))
    kept=[r for r in audited if r['decision']=='retain_recording_integrity_passed']
    kept_tasks={r['task_directory'] for r in kept}
    targets={}
    for r in audited:
        if r['decision']!='quarantine' or 'outside_project_formal_runs' in r['reasons']:continue
        p=Path(r['task_directory']);validate_target(p)
        if str(p) in kept_tasks:raise RuntimeError('Task has both keep and quarantine entries')
        targets[str(p)]={'source':str(p),'trace_ids':[r['trace_id']],'reason':r['reasons']}
    indexed_tasks={r['task_directory'] for r in audited}
    unindexed=[]
    for name in inventory_run_names():
        root=RUNS/name
        if name.startswith('longds-'):
            paths=(root/'tasks').glob('*/*/*')
        elif name.startswith('agenticdatabench-claude-'):
            paths=(root/'output').glob('*/*')
        elif name.startswith('agenticdatabench-kimi-'):
            paths=(root/'attempts').glob('*')
        elif name=='dataagentbench-soclaas-20261004':
            paths=(root).glob('*/tasks/*')
        elif name.startswith('dabstep-codex-'):
            paths=(root/'tasks/dev').glob('*')
        else:
            paths=(root/'tasks').glob('*')
        for p in paths:
            if not p.is_dir() or p.name.startswith('.') or str(p) in indexed_tasks:continue
            # Do not remove data-only workspaces or future, never-started tasks.
            has_trace=any((p/n).is_file() for n in ('events.jsonl','trajectory.jsonl','run_1/stdout.log','dabench/result.json'))
            has_trace=has_trace or any((p/'attempts').glob('attempt-*/*.jsonl')) or any((p/'detail').glob('turn_*/claude_stdout.jsonl'))
            if not has_trace:continue
            validate_target(p)
            unindexed.append(str(p))
            targets[str(p)]={'source':str(p),'trace_ids':[],
                            'reason':['recorded_task_without_completed_evaluated_inventory_entry']}
    # Remove unsuccessful historical attempts of retained tasks only. The chosen
    # completed attempt and all its source/score artifacts remain in place.
    kept_attempts={r['attempt'] for r in kept}
    for r in kept:
        for p in (Path(r['task_directory'])/'attempts').glob('attempt-*'):
            if str(p) in kept_attempts:continue
            d=read(p/'result.json')
            if d and d.get('return_code')==0 and isinstance(d.get('evaluation'),dict):
                # A second evaluated attempt is not automatically a bad trace.
                continue
            validate_target(p)
            targets[str(p)]={'source':str(p),'trace_ids':[],'reason':['unselected_incomplete_attempt']}
    # Confirm no selected source or evaluation reference lies under a target.
    for r in kept:
        paths=[Path(s['path']) for s in r['assembled_sources']]+[Path(p) for p in r['original']['evaluation_refs']]
        if any(t==p or t in p.parents for t in map(Path,targets) for p in paths):
            raise RuntimeError('Quarantine target contains retained evidence')
    plan={'version':VERSION,'applied':False,'manifest_sha256':digest(out/'manifest.jsonl'),
          'targets':list(targets.values()),'outside_project_traces_not_moved':sum(
              'outside_project_formal_runs' in r['reasons'] for r in audited),
          'unindexed_recorded_tasks':len(unindexed), 'unindexed_paths':unindexed,
          'frozen_annotation_results_untouched':True,'repro_demo_diagnostics_untouched':True}
    dump(out/'move-plan.json',plan)
    return plan


if __name__ == '__main__': main()
