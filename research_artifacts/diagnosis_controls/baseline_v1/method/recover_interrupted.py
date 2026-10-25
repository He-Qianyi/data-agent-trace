"""One explicitly audited continuation of each transport-interrupted conversation.

No completed model response is regenerated; the original failed HTTP attempt stays in
place. This is a reported replay of an unreturned request, not a zero-retry claim.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request

spec = importlib.util.spec_from_file_location('pilot', Path(__file__).with_name('run.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def explicit_replay(client, original, directory):
    directory.mkdir(parents=True, exist_ok=False)
    m.save(directory / 'request.json', original)
    receipt = {'started_at': m.now(), 'request_sha256': m.signature(original),
               'requested_model': original['model'], 'explicit_transport_replay': True,
               'http_attempts': 1, 'automatic_retries': 0,
               'reason': 'prior_attempt_disconnected_without_response; no completed result repeated'}
    request = urllib.request.Request(client.base + '/chat/completions',
        data=m.encode(original).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + client.key, 'Content-Type': 'application/json'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=600, context=client.context) as response:
            body = json.load(response)
            receipt['http_status'] = response.status
        body = m.redact_credentials(body, client.key)
        m.save(directory / 'response.json', body)
        receipt.update(returned_model=body.get('model'), usage=body.get('usage'), response_id=body.get('id'),
                       finish_reason=body['choices'][0].get('finish_reason'),
                       elapsed_seconds=round(time.monotonic()-started, 3))
        assert body['model'] == m.read(m.ROOT / 'plan.json')['expected_returned_model']
        m.save(directory / 'receipt.json', receipt)
        return body['choices'][0]['message'], body['choices'][0].get('finish_reason'), receipt
    except urllib.error.HTTPError as exc:
        receipt.update(status='provider_failure', http_status=exc.code, **m.safe_error_metadata(exc))
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        receipt.update(status='provider_or_identity_failure', error_type=type(exc).__name__)
    receipt['elapsed_seconds'] = round(time.monotonic()-started, 3)
    m.save(directory / 'receipt.json', receipt)
    raise RuntimeError('explicit_replay_failed_no_further_retry')


def one(index, job, states):
    rid = job['record_id']
    directory = m.ROOT / 'results' / rid
    previous = m.read(directory / 'result.json')
    assert previous['status'] == 'failed' and previous['reason'] == 'provider_failure_no_retry'
    interrupted = sorted((directory / 'calls').glob('*/receipt.json'))[-1]
    original_receipt = m.read(interrupted)
    assert original_receipt.get('error_type') == 'RemoteDisconnected'
    assert not (interrupted.parent / 'response.json').exists()
    assert not (directory / 'result_before_explicit_recovery.json').exists()
    shutil.copy2(directory / 'result.json', directory / 'result_before_explicit_recovery.json')
    safe = m.SafeEvidence(index, rid)
    for extra in m.read(directory / 'registered_extra_sources.json'):
        if extra['evidence_id'] == 'FINAL0001':
            continue
        raw = index.registry(rid).resolve(extra['original_source_ref'])
        text = raw if isinstance(raw, str) else m.encode(raw)
        safe.extra[extra['evidence_id']] = {**extra, 'text': text}
    safe.read_ranges = m.read(directory / 'read_ranges.json')
    payload = m.read(interrupted.parent / 'request.json')
    messages = payload['messages']
    plan = m.verify_plan()
    assert messages[0]['content'] == (m.CONTRACT / 'diagnosis.prompt.md').read_text()
    first_round = int(interrupted.parent.name)
    client = m.NativeClient()
    state = {**previous, 'status': 'running', 'explicit_transport_replays': 1}
    state.pop('reason', None)
    states[rid] = state
    m.progress(states, 'explicitly_recovering_two_interrupted_conversations')
    try:
        for round_no in range(first_round, plan['max_model_rounds_per_trace'] + 1):
            if round_no == first_round:
                native, finish, receipt = explicit_replay(client, payload,
                    directory / 'calls' / (f'{round_no:02}' + '-explicit-recovery1'))
            else:
                native, finish, receipt = client.ask(messages, directory / 'calls' / f'{round_no:02}')
            state['model_rounds'] = round_no
            if finish == 'length':
                state.update(status='output_truncated', reason='provider_finish_reason_length')
                break
            calls = native.get('tool_calls') or []
            if calls:
                messages.append({k: native[k] for k in ['role', 'content', 'tool_calls', 'reasoning_content', 'reasoning'] if k in native})
                for call in calls:
                    if state['tool_calls'] >= plan['max_tool_calls_per_trace']:
                        raise RuntimeError('tool_budget_exceeded')
                    name = call['function']['name']
                    try:
                        args = json.loads(call['function']['arguments'])
                        schema = next(t['function']['parameters'] for t in m.TOOLS if t['function']['name'] == name)
                        m.jsonschema.validate(args, schema)
                        result = safe.invoke(name, args)
                    except (ValueError, KeyError, StopIteration, m.jsonschema.ValidationError) as exc:
                        args = None
                        result = {'status': 'invalid_tool_arguments', 'error_type': type(exc).__name__}
                    safe.note_read(result)
                    state['tool_calls'] += 1
                    m.save(directory / 'tool_reads' / f"{state['tool_calls']:03}.json",
                           {'ordinal': state['tool_calls'], 'round': round_no, 'name': name,
                            'arguments': args, 'result': result, 'after_explicit_transport_recovery': True})
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': m.encode(result)})
                m.progress(states, 'explicitly_recovering_two_interrupted_conversations')
                continue
            content = native.get('content') or ''
            (directory / 'native_final.txt').write_text(content)
            try:
                diagnosis = json.loads(content)
            except (ValueError, TypeError):
                state.update(status='invalid_json', reason='no_parseable_final_JSON_no_repair')
                break
            m.save(directory / 'diagnosis.json', diagnosis)
            validation = m.validate_diagnosis(diagnosis, safe)
            m.save(directory / 'mechanical_validation.json', validation)
            state['status'] = 'mechanically_passed' if validation['passed'] else 'mechanically_failed'
            break
        else:
            state.update(status='round_budget_exhausted', reason='no_final_diagnosis_within_original_budget')
    except Exception as exc:
        state.update(status='recovery_failed', reason=str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__)
    state['finished_at'] = m.now()
    m.save(directory / 'read_ranges.json', safe.read_ranges)
    m.save(directory / 'registered_extra_sources.json', [{k: v for k, v in e.items() if k != 'text'} for e in safe.extra.values()])
    m.save(directory / 'result.json', state)
    m.progress(states, 'explicitly_recovering_two_interrupted_conversations')


def main():
    progress = m.read(m.ROOT / 'progress.json')
    assert progress['status'] in ['finished', 'stopped'], 'wait_for_noninterrupted_traces_first'
    plan = m.verify_plan()
    jobs = [j for j in plan['jobs'] if progress['traces'][j['record_id']]['status'] == 'failed']
    assert len(jobs) == 2
    m.save(m.ROOT / 'explicit_recovery_protocol.json', {
        'created_at': m.now(), 'scope': [j['record_id'] for j in jobs],
        'one_explicit_replay_per_disconnected_unreturned_request': True,
        'no_completed_response_regenerated': True, 'prompt_or_tools_or_sampling_changed': False,
        'original_round_budget_unchanged': True, 'automatic_retries': 0,
        'explicit_replay_count_planned': 2, 'runner_sha256': m.sha(Path(__file__))})
    states = progress['traces']
    index = m.EvidenceIndex(m.BUNDLE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, index, job, states) for job in jobs]
        for future in futures:
            future.result()
    m.progress(states, 'finished')
    summary = m.summary()
    print(m.encode({k: v for k, v in summary.items() if k != 'traces'}))


if __name__ == '__main__':
    main()
