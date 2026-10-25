"""Paired round-12 intervention: only tool_choice auto -> none.

Earlier recorded responses are reused, not resampled. No prompt edits, data
execution, output repair, or transport retry. Baseline outputs remain read-only.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = Path(__file__).resolve().parents[2]
ROOT = BASE / 'outputs/diagnosis_termination_control_v1_20261018'
OLD = BASE / 'outputs/diagnosis_eight_native_tools_20261018'
CONTRACT = BASE / 'outputs/diagnosis_prompt_v1_20261018'
BUNDLE = BASE / 'outputs/stage_d_ten_20261018'
MIRROR = Path('/Users/research/Documents/Codex/2026-10-17/referenced-chatgpt-conversation-this-is-an/outputs/diagnosis_termination_control_v1_20261018')
sys.path.insert(0, str(BASE))
spec = importlib.util.spec_from_file_location('frozen_diagnosis_baseline', BASE / 'work/diagnosis_eight_native_tools_20261018/run.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signature(value):
    return hashlib.sha256(baseline.encode(value).encode()).hexdigest()


def save(path, value):
    baseline.save(path, value)


def termination_payload(payload, round_number, total_rounds=12):
    if type(round_number) is not int or not 1 <= round_number <= total_rounds:
        raise ValueError('round_outside_budget')
    result = copy.deepcopy(payload)
    if round_number == total_rounds:
        result['tool_choice'] = 'none'
    return result


def verify():
    plan = read(ROOT / 'plan.json')
    assert sha(Path(__file__)) == plan['runner_sha256']
    assert sha(CONTRACT / 'diagnosis.prompt.md') == plan['prompt_sha256']
    assert sha(CONTRACT / 'diagnosis.schema.json') == plan['schema_sha256']
    assert sha(OLD / 'plan.json') == plan['baseline_plan_sha256']
    assert sha(BASE / 'work/diagnosis_eight_native_tools_20261018/run.py') == plan['baseline_runner_sha256']
    assert sha(BASE / 'pipeline/stages/evidence_builder/index.py') == plan['evidence_index_sha256']
    for relative, digest in plan['preserved_baseline_files'].items():
        assert sha(OLD / relative) == digest, relative
    for job in plan['jobs']:
        assert sha(OLD / 'inputs' / (job['record_id'] + '.json')) == job['input_sha256']
        assert sha(BUNDLE / 'traces' / (job['record_id'] + '.json')) == job['trace_sha256']
    return baseline.verify_processor()


def restore_prefix(index, rid, cap):
    safe = baseline.SafeEvidence(index, rid)
    initial = safe.initial()
    assert initial == read(OLD / 'inputs' / (rid + '.json'))
    old = OLD / 'results' / rid
    # Source registrations do not imply inspection. Ranges are reconstructed
    # from initial pages and tool results with round < final submission round.
    for extra in read(old / 'registered_extra_sources.json'):
        if extra['evidence_id'] == 'FINAL0001':
            continue
        raw = index.registry(rid).resolve(extra['original_source_ref'])
        safe.extra[extra['evidence_id']] = {**extra, 'text': raw if isinstance(raw, str) else baseline.encode(raw)}
    reused_reads = []
    for path in sorted((old / 'tool_reads').glob('*.json')):
        item = read(path)
        if item['round'] < cap:
            safe.note_read(item['result'])
            reused_reads.append({'path': str(path.relative_to(OLD)), 'sha256': sha(path),
                                 'round': item['round'], 'ordinal': item['ordinal']})
    return safe, reused_reads


def prepare():
    assert not (ROOT / 'plan.json').exists(), 'experiment_already_prepared'
    old = read(OLD / 'plan.json')
    assert baseline.sha(BASE / 'work/diagnosis_eight_native_tools_20261018/run.py') == old['runner_sha256']
    assert sha(CONTRACT / 'diagnosis.prompt.md') == old['prompt_sha256']
    index = baseline.EvidenceIndex(BUNDLE)
    jobs = []
    keep = {'plan.json': sha(OLD / 'plan.json'), 'semantic_review.json': sha(OLD / 'semantic_review.json')}
    for job in old['jobs']:
        rid = job['record_id']
        folder = OLD / 'results' / rid
        result = read(folder / 'result.json')
        item = {**job, 'baseline_status': result['status'], 'baseline_model_rounds': result['model_rounds'],
                'baseline_tool_calls': result['tool_calls']}
        target = ROOT / 'results' / rid
        target.mkdir(parents=True)
        keep[str((folder / 'result.json').relative_to(OLD))] = sha(folder / 'result.json')
        if (folder / 'diagnosis.json').exists() and result['model_rounds'] < old['max_model_rounds_per_trace']:
            item['treatment'] = 'unchanged_early_terminal_reuse'
            for name in ['diagnosis.json', 'native_final.txt', 'mechanical_validation.json', 'result.json', 'read_ranges.json']:
                shutil.copy2(folder / name, target / name)
                keep[str((folder / name).relative_to(OLD))] = sha(folder / name)
            save(target / 'reuse.json', {'reason': 'policy changes round 12 only; prior terminal output is unaffected',
                                         'diagnosis_sha256': sha(folder / 'diagnosis.json'), 'new_model_calls': 0})
        elif (folder / 'calls' / '12' / 'request.json').exists():
            item['treatment'] = 'paired_final_round_tool_choice_none'
            control_path = folder / 'calls' / '12' / 'request.json'
            control = read(control_path)
            treatment = termination_payload(control, 12)
            assert control['tool_choice'] == 'auto'
            assert {k: v for k, v in treatment.items() if k != 'tool_choice'} == {k: v for k, v in control.items() if k != 'tool_choice'}
            assert treatment['messages'][0]['content'] == (CONTRACT / 'diagnosis.prompt.md').read_text()
            safe, reads = restore_prefix(index, rid, 12)
            save(target / 'control_request.json', control)
            save(target / 'treatment_request.json', treatment)
            save(target / 'prefix_reads.json', reads)
            save(target / 'prefix_read_ranges.json', safe.read_ranges)
            save(target / 'prefix_check.json', {
                'same_pre_round12_messages': treatment['messages'] == control['messages'],
                'only_changed_key': 'tool_choice', 'before': 'auto', 'after': 'none',
                'prompt_changed': False, 'new_user_or_system_messages_added': False,
                'rounds_reused': 11, 'tool_calls_reused': len(reads),
                'baseline_round12_tool_results_excluded': True})
            for path in (folder / 'calls').glob('*/request.json'):
                keep[str(path.relative_to(OLD))] = sha(path)
            for path in (folder / 'tool_reads').glob('*.json'):
                keep[str(path.relative_to(OLD))] = sha(path)
            keep[str((folder / 'calls/12/response.json').relative_to(OLD))] = sha(folder / 'calls/12/response.json')
        else:
            item['treatment'] = 'transport_censored_before_intervention'
            save(target / 'result.json', {**result, 'comparison_status': 'not_reached_round12_transport_censored',
                                         'new_model_calls': 0})
        jobs.append(item)
    plan = {**{k: old[k] for k in ['model', 'expected_returned_model', 'provider', 'endpoint', 'max_tokens',
            'thinking_token_budget', 'temperature', 'response_format', 'concurrency', 'max_model_rounds_per_trace',
            'max_tool_calls_per_trace', 'prompt_sha256', 'schema_sha256', 'evidence_index_sha256']},
            'schema': 'paired-final-round-termination-control-v1', 'prepared_at': baseline.now(),
            'runner_sha256': sha(Path(__file__)), 'baseline_plan_sha256': sha(OLD / 'plan.json'),
            'baseline_runner_sha256': old['runner_sha256'], 'preserved_baseline_files': keep, 'jobs': jobs,
            'single_changed_factor': 'tool_choice=none only at round 12; no reminder/prompt/schema/example/evidence change',
            'comparison_design': 'paired continuation of identical recorded pre-intervention histories; not eight independent reruns',
            'new_request_count_planned': sum(j['treatment'] == 'paired_final_round_tool_choice_none' for j in jobs),
            'automatic_retries': 0, 'repair_calls': 0, 'review_model_calls': 0,
            'service_interruptions_separately_censored': True,
            'evaluation': ['within-budget final JSON', 'strict schema and actually inspected evidence IDs',
                           'unsupported cause or facts under forced submission',
                           'identical early-terminal outputs; no independent repeatability estimate'],
            'independent_semantic_gold': False,
            'outbound_exclusions': old['outbound_exclusions']}
    save(ROOT / 'plan.json', plan)
    shutil.copytree(CONTRACT, ROOT / 'contract_v1', dirs_exist_ok=True)
    (ROOT / 'method').mkdir(exist_ok=True)
    shutil.copy2(Path(__file__), ROOT / 'method/run.py')
    for name in ['plan.json', 'semantic_review.json', 'summary.json']:
        (ROOT / 'baseline').mkdir(exist_ok=True)
        shutil.copy2(OLD / name, ROOT / 'baseline' / name)
    frozen = verify()
    save(ROOT / 'offline_checks.json', {'frozen_processor_files_unchanged': frozen,
         'five_early_terminal_outputs_identical': sum(j['treatment'] == 'unchanged_early_terminal_reuse' for j in jobs),
         'transport_censored': sum(j['treatment'] == 'transport_censored_before_intervention' for j in jobs),
         'paired_final_round_requests': plan['new_request_count_planned'],
         'prefix_excludes_round12_reads': True, 'new_model_calls_so_far': 0})
    print(json.dumps(read(ROOT / 'offline_checks.json'), ensure_ascii=False))


def run():
    verify()
    plan = read(ROOT / 'plan.json')
    index = baseline.EvidenceIndex(BUNDLE)
    client = baseline.NativeClient()  # Reads existing credentials in memory only.
    for job in plan['jobs']:
        if job['treatment'] != 'paired_final_round_tool_choice_none':
            continue
        rid = job['record_id']
        target = ROOT / 'results' / rid
        folder = target / 'call_round12'
        folder.mkdir(exist_ok=False)
        payload = read(target / 'treatment_request.json')
        assert baseline.redact_credentials(payload, client.key) == payload
        assert payload['tool_choice'] == 'none' and 'response_format' not in payload
        save(folder / 'request.json', payload)
        safe, reads = restore_prefix(index, rid, 12)
        assert safe.read_ranges == read(target / 'prefix_read_ranges.json')
        receipt = {'started_at': baseline.now(), 'requested_model': plan['model'], 'round': 12,
                   'request_sha256': signature(payload), 'automatic_retries': 0, 'http_attempts': 1,
                   'tool_choice': 'none', 'prompt_sha256': plan['prompt_sha256']}
        request = urllib.request.Request(client.base + '/chat/completions', data=baseline.encode(payload).encode(),
            method='POST', headers={'Authorization': 'Bearer ' + client.key, 'Content-Type': 'application/json'})
        state = {'task_id': job['task_id'], 'status': 'requesting_final_round', 'model_rounds': 12,
                 'reused_model_rounds': 11, 'new_model_requests': 1, 'tools_executed_in_round12': 0,
                 'tool_calls_before_round12': len(reads)}
        save(ROOT / 'progress.json', state)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600, context=client.context) as response:
                body = json.load(response)
                receipt['http_status'] = response.status
            body = baseline.redact_credentials(body, client.key)
            save(folder / 'response.json', body)
            receipt.update(returned_model=body.get('model'), usage=body.get('usage'), response_id=body.get('id'),
                           elapsed_seconds=round(time.monotonic()-started, 3))
            assert body['model'] == plan['expected_returned_model'], 'unexpected_model_identity'
            choice = body['choices'][0]
            receipt['finish_reason'] = choice.get('finish_reason')
            message = choice['message']
            if message.get('tool_calls'):
                state.update(status='provider_disobeyed_no_tools', returned_tool_calls=len(message['tool_calls']))
            elif choice.get('finish_reason') == 'length':
                state['status'] = 'output_truncated'
            else:
                content = message.get('content') or ''
                (target / 'native_final.txt').write_text(content)
                try:
                    diagnosis = json.loads(content)
                except (ValueError, TypeError):
                    state['status'] = 'invalid_json'
                else:
                    save(target / 'diagnosis.json', diagnosis)
                    validation = baseline.validate_diagnosis(diagnosis, safe)
                    save(target / 'mechanical_validation.json', validation)
                    state['status'] = 'mechanically_passed' if validation['passed'] else 'mechanically_failed'
        except urllib.error.HTTPError as exc:
            receipt.update(status='provider_http_failure', http_status=exc.code, **baseline.safe_error_metadata(exc))
            state['status'] = 'transport_censored_at_intervention'
        except (OSError, ValueError, KeyError, AssertionError) as exc:
            receipt.update(status='provider_transport_or_identity_failure', error_type=type(exc).__name__)
            state['status'] = 'transport_or_identity_censored_at_intervention'
        receipt['elapsed_seconds'] = round(time.monotonic()-started, 3)
        state['finished_at'] = baseline.now()
        save(folder / 'receipt.json', receipt)
        save(target / 'result.json', state)
        save(target / 'read_ranges.json', safe.read_ranges)
        save(ROOT / 'progress.json', {**state, 'experiment_status': 'finished', 'frozen_files_verified': verify()})
        print(json.dumps(state, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.run:
        run()
