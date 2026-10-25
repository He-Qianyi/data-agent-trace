"""Frozen-prompt, native-tool diagnosis pilot. No execution of recorded SQL/code."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

import jsonschema

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
from pipeline.stages.evidence_builder.index import EvidenceIndex
from pipeline.stages.trace_processor.merge_layer.build import bundled_sources
from trace_processor_v2 import provider_config, redact_credentials
from probe_trace_processor_provider import safe_error_metadata

ROOT = BASE / 'outputs/diagnosis_confirmation_gate_v1_20261018'
CONTRACT = ROOT / 'contract'
BUNDLE = BASE / 'outputs/stage_d_ten_20261018'
MIRROR = BASE / 'work/diagnosis_confirmation_gate_v1_20261018/live'
EXCLUDED = {'rawio-baba471b324aa7141b29', 'rawio-d1c5eff3a0eeef033955'}
LOCK = threading.Lock()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signature(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def verify_processor():
    files = read(BASE / 'outputs/trace_processor_frozen_v1_20261018/frozen_manifest.json')['files']
    assert all(sha(BASE / p) == digest for p, digest in files.items()), 'processor_freeze_changed'
    return len(files)


def function(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': required, 'additionalProperties': False}}}


TOOLS = [
    function('search_evidence', 'Search this trace only. Results are unverified navigation, not inspected evidence. No official evaluations or snapshots are searchable.',
             {'question': {'type': 'string'}, 'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 6}}, ['question']),
    function('retrieve_evidence', 'Search and read original evidence pages for this trace. Returns page boundaries and explicit deferred pages. A hit is not proof; follow relevant missing pages.',
             {'question': {'type': 'string'}, 'top_k': {'type': 'integer', 'minimum': 1, 'maximum': 4}}, ['question']),
    function('read_evidence', 'Read an original evidence block or its next page. Copy the returned evidence_id when citing. FINAL0001 is the source-verified recorded final message. Does not execute anything.',
             {'evidence_id': {'type': 'string'}, 'start': {'type': 'integer', 'minimum': 0},
              'characters': {'type': 'integer', 'minimum': 1, 'maximum': 12000}}, ['evidence_id']),
    function('read_steps', 'Read original arguments, returns and recording signals for up to six existing tool steps. Pages may be partial; follow next_char_start. Does not execute the submitted code.',
             {'step_ids': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1, 'maxItems': 6}}, ['step_ids']),
    function('read_exchange', 'Read a bounded page of a fully recorded model request or response for this trace. The response is a source, not a new execution; repeated historical messages are not extra tool calls. Registered evidence ID is exchange_id plus _REQUEST or _RESPONSE.',
             {'exchange_id': {'type': 'string'}, 'surface': {'type': 'string', 'enum': ['request', 'response']},
              'start': {'type': 'integer', 'minimum': 0},
              'characters': {'type': 'integer', 'minimum': 1, 'maximum': 12000}}, ['exchange_id', 'surface']),
]


class SafeEvidence:
    def __init__(self, index, rid):
        self.index, self.rid = index, rid
        self.trace = index.traces[rid]
        self.extra, self.read_ranges = {}, {}
        final = self.trace['facts']['final_response']
        raw = index.registry(rid).resolve(final['source_ref'])
        final_ref = copy.deepcopy(final['source_ref'])
        if isinstance(raw, list):
            # Some recorders reference an Anthropic content array. Select the
            # exact recorded text block, not an unverified re-rendered summary.
            matches = [(i, x['text']) for i, x in enumerate(raw)
                       if isinstance(x, dict) and x.get('text') == final['text']]
            assert len(matches) == 1, 'ambiguous_final_content_block'
            position, raw = matches[0]
            final_ref['json_pointer'] += f'/{position}/text'
        assert raw == final['text'], 'final_source_mismatch'
        self.extra['FINAL0001'] = {'evidence_id': 'FINAL0001', 'step_id': None,
            'role': 'recorded_final_response', 'text': raw,
            'fact_pointer': '/facts/final_response/text', 'original_source_ref': final_ref,
            'representation': 'text'}

    def page(self, eid, start=0, characters=4000):
        if type(start) is not int or type(characters) is not int or start < 0 or not 1 <= characters <= 12000:
            return {'status': 'invalid_range'}
        if eid in self.extra:
            e = self.extra[eid]
            if start > len(e['text']):
                return {'status': 'invalid_range'}
            end = min(start + characters, len(e['text']))
            return {**{k: v for k, v in e.items() if k != 'text'}, 'status': 'source_excerpt',
                    'text': e['text'][start:end], 'block_char_start': start, 'block_char_end': end,
                    'block_total_characters': len(e['text']),
                    'next_char_start': end if end < len(e['text']) else None,
                    'literal_text_unchanged': True}
        try:
            return self.index.evidence_page(self.rid, eid, start, characters)
        except ValueError:
            return {'status': 'invalid_range'}

    def note_read(self, value):
        if isinstance(value, dict):
            if value.get('status') == 'source_excerpt' and 'text' in value:
                self.read_ranges.setdefault(value['evidence_id'], []).append(
                    [value['block_char_start'], value['block_char_end']])
            for v in value.values():
                self.note_read(v)
        elif isinstance(value, list):
            for v in value:
                self.note_read(v)

    def step_pages(self, step_ids, budget=32000):
        if not isinstance(step_ids, list) or not 1 <= len(step_ids) <= 6:
            return {'status': 'invalid_step_selection'}
        result = {'status': 'recorded_step_evidence', 'pages': [], 'deferred_pages': [],
                  'step_order_is_not_causal_order': True}
        for sid in step_ids:
            if sid not in self.index.steps[self.rid]:
                result['deferred_pages'].append({'step_id': sid, 'status': 'step_not_found'})
                continue
            for eid, e in self.index.catalogs[self.rid].items():
                if e.get('step_id') != sid:
                    continue
                page = self.page(eid, characters=5000)
                result['pages'].append(page)
                if len(encode(result).encode()) > budget - 1000:
                    result['pages'].pop()
                    result['deferred_pages'].append({'evidence_id': eid, 'next_char_start': 0})
                elif page.get('next_char_start') is not None:
                    result['deferred_pages'].append({'evidence_id': eid,
                                                     'next_char_start': page['next_char_start']})
        result['payload_bytes'] = len(encode(result).encode())
        return result

    def invoke(self, name, args):
        if name == 'search_evidence':
            return self.index.search(self.rid, str(args['question']), min(6, max(1, int(args.get('top_k', 3)))))
        if name == 'retrieve_evidence':
            return self.index.package(self.rid, str(args['question']), min(4, max(1, int(args.get('top_k', 3)))),
                                      32000, 2000)
        if name == 'read_evidence':
            return self.page(args['evidence_id'], args.get('start', 0), args.get('characters', 4000))
        if name == 'read_steps':
            return self.step_pages(args['step_ids'])
        if name == 'read_exchange':
            xid, surface = args['exchange_id'], args['surface']
            if surface not in ['request', 'response']:
                return {'status': 'invalid_surface'}
            exchange = next((x for x in self.trace['facts']['model_exchanges'] if x['exchange_id'] == xid), None)
            if not exchange:
                return {'status': 'exchange_not_found'}
            eid = xid + '_' + surface.upper()
            if eid not in self.extra:
                source = self.index.registry(self.rid).resolve(exchange[surface + '_ref'])
                text = source if isinstance(source, str) else encode(source)
                self.extra[eid] = {'evidence_id': eid, 'step_id': None, 'role': 'recorded_model_' + surface,
                                  'text': text, 'fact_pointer': None,
                                  'original_source_ref': exchange[surface + '_ref'],
                                  'representation': 'text' if isinstance(source, str) else 'compact_json',
                                  'history_is_not_additional_execution': True}
            return self.page(eid, args.get('start', 0), args.get('characters', 4000))
        return {'status': 'unknown_tool'}

    def initial(self):
        pages = []
        for eid, e in self.index.catalogs[self.rid].items():
            if e['role'] == 'original_task_instruction':
                # Never cut task text silently; unusually long blocks have explicit continuation.
                for start in range(0, max(1, len(e['text'])), 12000):
                    pages.append(self.page(eid, start, 12000))
        pages.append(self.page('FINAL0001', characters=12000))
        steps = self.trace['facts']['steps']
        selected = list(dict.fromkeys([steps[0]['step_id']] + [s['step_id'] for s in steps[-2:]]))
        for sid in selected:
            for eid, e in self.index.catalogs[self.rid].items():
                if e.get('step_id') == sid:
                    pages.append(self.page(eid, characters=1000))
        nav = []
        for s in steps:
            nav.append({'step_id': s['step_id'], 'model_exchange_ref': s['model_exchange_ref'],
                        'tool_name': s['action']['tool_name'],
                        'available_evidence_ids': [eid for eid, e in self.index.catalogs[self.rid].items()
                                                   if e.get('step_id') == s['step_id']],
                        'unverified_operation_summaries': [o['description']
                            for u in self.trace['annotations']['units'] if u['step_id'] == s['step_id']
                            for o in u['operations']]})
        metadata = self.trace['facts']['trace_metadata']
        result = {'trace_metadata': {k: metadata[k] for k in ['record_id', 'benchmark', 'task_id', 'agent_id']},
                  'task_evidence_ids': [eid for eid, e in self.index.catalogs[self.rid].items()
                                        if e['role'] == 'original_task_instruction'],
                  'task_requirements': [{k: r[k] for k in ['requirement_id', 'kind', 'description']}
                                        for r in self.trace['annotations']['task']['requirements']],
                  'navigation_index': nav, 'stage_ranges': [], 'evidence': pages,
                  'coverage': {'tool_evidence': 'initial first and last two tool steps, first 1000 characters per block; other evidence must be read through declared tools',
                               'final_response': 'source-verified original final text, with explicit continuation if needed',
                               'navigation_is_not_inspection': True,
                               'excluded': ['official_evaluation', 'reference_answers', 'evaluator_feedback', 'post_run_artifact_snapshots'],
                               'full_model_request_response': 'available through read_exchange; histories do not create extra execution steps'},
                  'available_retrieval': [{'name': t['function']['name'],
                                           'description': t['function']['description']} for t in TOOLS]}
        self.note_read(result)
        return result


def validate_diagnosis(data, safe):
    errors = []
    schema = read(CONTRACT / 'diagnosis.schema.json')
    for e in jsonschema.Draft202012Validator(schema).iter_errors(data):
        errors.append({'kind': 'schema', 'path': '/'.join(map(str, e.absolute_path)), 'message': e.message})
    if errors:
        return {'passed': False, 'errors': errors, 'citation_references': 0}
    citations = []
    for section in ['root_cause_chain', 'critical_decision_points']:
        for i, item in enumerate(data[section]):
            step_ids = item.get('step_ids', [item.get('step_id')])
            for sid in step_ids:
                if sid not in safe.index.steps[safe.rid]:
                    errors.append({'kind': 'unknown_step_id', 'section': section, 'index': i, 'step_id': sid})
            for eid in item['evidence_ids']:
                if eid not in safe.index.catalogs[safe.rid] and eid not in safe.extra:
                    errors.append({'kind': 'unknown_evidence_id', 'evidence_id': eid})
                elif eid not in safe.read_ranges:
                    errors.append({'kind': 'evidence_not_actually_read', 'evidence_id': eid})
                citations.append({'section': section, 'index': i, 'evidence_id': eid,
                                  'read_ranges': safe.read_ranges.get(eid, [])})
    if data['stage_labels']:
        errors.append({'kind': 'invented_stages', 'reason': 'No trusted stage spans were provided'})
    return {'passed': not errors, 'errors': errors, 'citation_references': len(citations),
            'citation_checks': citations, 'semantic_entailment_verified_by_scripts': False}


class NativeClient:
    def __init__(self):
        self.base, self.key = provider_config(BASE / '.env.soclaas')
        assert self.base.rstrip('/') == 'https://soclaas-api.comp.nus.edu.sg/v1'
        assert self.key, 'missing_provider_credential'
        ca_file = os.environ.get('SSL_CERT_FILE')
        if not ca_file:
            try:
                import certifi
                ca_file = certifi.where()
            except ImportError:
                pass
        self.context = ssl.create_default_context(cafile=ca_file)

    def ask(self, messages, directory):
        plan = read(ROOT / 'plan.json')
        directory.mkdir(parents=True, exist_ok=False)
        payload = {'model': plan['model'], 'messages': messages, 'tools': TOOLS, 'tool_choice': ('none' if int(directory.name) == 12 else 'auto'),
                   'max_tokens': plan['max_tokens'], 'thinking_token_budget': plan['thinking_token_budget'],
                   'temperature': 0}
        payload = redact_credentials(payload, self.key)
        text = encode(payload)
        sig = signature(payload)
        reserve = ROOT / 'request_reservations' / sig
        reserve.parent.mkdir(exist_ok=True)
        with reserve.open('x') as f:
            f.write(str(directory.relative_to(ROOT)))
        save(directory / 'request.json', payload)
        receipt = {'started_at': now(), 'request_sha256': sig, 'requested_model': plan['model'],
                   'automatic_retries': 0, 'http_attempts': 1}
        request = urllib.request.Request(self.base + '/chat/completions', data=text.encode(),
                    method='POST', headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=600, context=self.context) as response:
                body = json.load(response)
                receipt['http_status'] = response.status
            body = redact_credentials(body, self.key)
            save(directory / 'response.json', body)
            receipt.update(returned_model=body.get('model'), usage=body.get('usage'),
                           response_id=body.get('id'), elapsed_seconds=round(time.monotonic()-started, 3))
            assert body.get('model') == plan['expected_returned_model'], 'returned_model_mismatch'
            choice = body['choices'][0]
            receipt['finish_reason'] = choice.get('finish_reason')
            save(directory / 'receipt.json', receipt)
            return choice['message'], choice.get('finish_reason'), receipt
        except urllib.error.HTTPError as exc:
            receipt.update(status='provider_failure', http_status=exc.code, **safe_error_metadata(exc))
        except (OSError, ValueError, KeyError, AssertionError, IndexError) as exc:
            receipt.update(status='provider_or_identity_failure', error_type=type(exc).__name__)
        receipt['elapsed_seconds'] = round(time.monotonic()-started, 3)
        save(directory / 'receipt.json', receipt)
        raise RuntimeError('provider_failure_no_retry')


def prepare():
    assert not (ROOT / 'plan.json').exists(), 'plan_already_exists'
    frozen = verify_processor()
    index = EvidenceIndex(BUNDLE)
    jobs = []
    for rid, trace in index.traces.items():
        if rid in EXCLUDED:
            continue
        safe = SafeEvidence(index, rid)
        inp = safe.initial()
        save(ROOT / 'inputs' / (rid + '.json'), inp)
        jobs.append({'record_id': rid, 'task_id': trace['facts']['trace_metadata']['task_id'],
                     'agent_id': trace['facts']['trace_metadata']['agent_id'],
                     'input_sha256': sha(ROOT / 'inputs' / (rid + '.json')),
                     'trace_sha256': sha(BUNDLE / 'traces' / (rid + '.json'))})
    assert len(jobs) == 8
    plan = {'schema': 'diagnosis-eight-frozen-prompt-pilot-v1', 'prepared_at': now(),
            'model': 'x-test-2', 'expected_returned_model': 'deepseek-v4.1-flash-1017',
            'provider': 'SoCLaaS', 'endpoint': 'https://soclaas-api.comp.nus.edu.sg/v1/chat/completions',
            'max_tokens': 48000, 'thinking_token_budget': 16000, 'temperature': 0,
            'response_format': None, 'concurrency': 2,
            'transport_correction': 'omit JSON mode; native evidence tools remain enabled; output schema unchanged',
            'probe_reuse': 'outputs/diagnosis_eight_validation_20261018/protocol_probe_native_tools',
            'max_model_rounds_per_trace': 12, 'max_tool_calls_per_trace': 60,
            'automatic_retries': 0, 'repair_calls': 0, 'review_model_calls': 0,
            'prompt_path': str(CONTRACT / 'diagnosis.prompt.md'),
            'prompt_sha256': sha(CONTRACT / 'diagnosis.prompt.md'),
            'prompt_transformation': 'none; actual packet supplied in user message, template placeholder retained as-is',
            'schema_sha256': sha(CONTRACT / 'diagnosis.schema.json'),
            'runner_sha256': sha(Path(__file__)),
            'evidence_index_sha256': sha(BASE / 'pipeline/stages/evidence_builder/index.py'),
            'excluded_prompt_examples': sorted(EXCLUDED), 'jobs': jobs,
            'evaluation_protocol': {'mechanical': ['JSON schema', 'existing step IDs', 'existing and actually read evidence IDs', 'no invented stages'],
                'semantic': ['evidence supports claim', 'task/metric/population fidelity', 'recovery considered',
                             'no unsupported causal link', 'delivery and file existence separated', 'coverage limits honored'],
                'independent_gold': False, 'official_scores_do_not_identify_root_causes': True,
                'eight_traces_are_non_examples_but_previously_inspected_in_retrieval_pilot': True,
                'semantic_review_is_local_author_review_after_results_not_blinded_independent_scoring': True},
            'outbound_exclusions': ['official_evaluation', 'reference_answers', 'evaluator_feedback', 'post_run_artifact_snapshots'],
            'frozen_processor_files_verified': frozen}
    save(ROOT / 'plan.json', plan)
    save(ROOT / 'tools.json', TOOLS)
    save(ROOT / 'offline_checks.json', offline_checks(index))
    (ROOT / 'method').mkdir(exist_ok=True)
    shutil.copy2(Path(__file__), ROOT / 'method/run.py')
    shutil.copy2(CONTRACT / 'diagnosis.prompt.md', ROOT / 'method/diagnosis.prompt.md')
    shutil.copy2(CONTRACT / 'diagnosis.schema.json', ROOT / 'method/diagnosis.schema.json')
    print(json.dumps({'prepared_traces': len(jobs), 'prompt_unchanged': True,
                      'offline_checks': read(ROOT / 'offline_checks.json')}, ensure_ascii=False))


def offline_checks(index):
    results = []
    for rid in index.traces:
        if rid in EXCLUDED:
            continue
        safe = SafeEvidence(index, rid)
        inp = safe.initial()
        # Outcome is intentionally never called: it exposes post-run evaluation and snapshots.
        assert not any(k in inp for k in ['evaluation', 'deliverables', 'gold_answer', 'score'])
        assert not safe.invoke('read_evidence', {'evidence_id': '__missing__'}).get('text')
        assert safe.invoke('read_exchange', {'exchange_id': '__missing__', 'surface': 'request'})['status'] == 'exchange_not_found'
        assert safe.invoke('read_evidence', {'evidence_id': 'FINAL0001', 'start': -1})['status'] == 'invalid_range'
        first = safe.trace['facts']['steps'][0]['step_id']
        p = safe.invoke('read_steps', {'step_ids': [first]})
        for e in p['pages']:
            assert e['text'] == index.catalogs[rid][e['evidence_id']]['text'][e['block_char_start']:e['block_char_end']]
        candidate = {'root_cause_chain': [], 'critical_decision_points': [], 'correct_strategy': '',
                     'stage_labels': [], 'summary': '证据不足，未确定根因。'}
        assert validate_diagnosis(candidate, safe)['passed']
        unread = next(eid for eid in index.catalogs[rid] if eid not in safe.read_ranges)
        candidate['critical_decision_points'] = [{'step_id': first, 'decision': 'test', 'reasoning': 'test',
            'should_have': 'test', 'evidence_ids': [unread]}]
        assert not validate_diagnosis(candidate, safe)['passed']
        results.append({'record_id': rid, 'projection_allowlist': 'passed', 'source_pages': 'passed',
                        'unknown_ids': 'passed', 'negative_range': 'passed',
                        'unread_citation_rejected': 'passed', 'final_source_resolved': 'passed'})
    return results


def verify_plan():
    plan = read(ROOT / 'plan.json')
    assert sha(Path(__file__)) == plan['runner_sha256']
    assert sha(CONTRACT / 'diagnosis.prompt.md') == plan['prompt_sha256']
    assert sha(CONTRACT / 'diagnosis.schema.json') == plan['schema_sha256']
    assert sha(BASE / 'pipeline/stages/evidence_builder/index.py') == plan['evidence_index_sha256']
    verify_processor()
    return plan


def progress(states, status='running'):
    with LOCK:
        data = {'status': status, 'updated_at': now(), 'worker_pid': os.getpid(),
                'finished_traces': sum(s.get('status') not in ['pending', 'running'] for s in states.values()),
                'total_traces': 8, 'traces': copy.deepcopy(states)}
        save(ROOT / 'progress.json', data)
        MIRROR.mkdir(parents=True, exist_ok=True)
        save(MIRROR / 'progress.json', data)
        lines = ['第二轮诊断验证 · 仅增加已确认问题门槛', '', f"状态：{status}；已结束：{data['finished_traces']}/8", '',
                 '官方评估、标准答案与运行后快照不发送给诊断模型。', '',
                 '|轨迹|状态|模型调用|只读取证次数|', '|---|---|---:|---:|']
        for s in states.values():
            lines.append(f"|{s['task_id']}|{s.get('status')}|{s.get('model_rounds',0)}|{s.get('tool_calls',0)}|")
        (MIRROR / 'status.md').write_text('\n'.join(lines) + '\n')


def run_one(index, client, job, states):
    rid = job['record_id']
    directory = ROOT / 'results' / rid
    if directory.exists():
        raise RuntimeError('existing_result_not_resubmitted')
    directory.mkdir(parents=True)
    safe = SafeEvidence(index, rid)
    generated = safe.initial()
    inp = read(ROOT / 'inputs' / (rid + '.json'))
    assert generated == inp
    messages = [{'role': 'system', 'content': (CONTRACT / 'diagnosis.prompt.md').read_text()},
                {'role': 'user', 'content': 'CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n' + encode(inp)}]
    plan = verify_plan()
    state = {'task_id': job['task_id'], 'status': 'running', 'model_rounds': 0, 'tool_calls': 0}
    states[rid] = state
    progress(states)
    tool_log = []
    try:
        for round_no in range(1, plan['max_model_rounds_per_trace'] + 1):
            verify_plan()
            native, finish, receipt = client.ask(messages, directory / 'calls' / f'{round_no:02}')
            state['model_rounds'] = round_no
            if finish == 'length':
                state.update(status='output_truncated', reason='provider_finish_reason_length')
                break
            calls = native.get('tool_calls') or []
            if calls and round_no == 12:
                state.update(status='provider_disobeyed_no_tools', reason='tools_requested_at_submission_round')
                break
            if calls:
                # Preserve native reasoning_content on tool continuation when returned by provider.
                message = {k: native[k] for k in ['role', 'content', 'tool_calls', 'reasoning_content', 'reasoning'] if k in native}
                message.setdefault('role', 'assistant')
                messages.append(message)
                for call in calls:
                    if state['tool_calls'] >= plan['max_tool_calls_per_trace']:
                        raise RuntimeError('tool_budget_exceeded')
                    name = call['function']['name']
                    try:
                        args = json.loads(call['function']['arguments'])
                        schema = next(t['function']['parameters'] for t in TOOLS if t['function']['name'] == name)
                        jsonschema.validate(args, schema)
                        result = safe.invoke(name, args)
                    except (ValueError, KeyError, StopIteration, jsonschema.ValidationError) as exc:
                        args = None
                        result = {'status': 'invalid_tool_arguments', 'error_type': type(exc).__name__}
                    safe.note_read(result)
                    state['tool_calls'] += 1
                    log = {'ordinal': state['tool_calls'], 'round': round_no, 'name': name,
                           'arguments': args, 'result': result}
                    tool_log.append(log)
                    save(directory / 'tool_reads' / f"{state['tool_calls']:03}.json", log)
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': encode(result)})
                progress(states)
                continue
            content = native.get('content')
            (directory / 'native_final.txt').write_text(content or '')
            try:
                diagnosis = json.loads(content or '')
            except (ValueError, TypeError):
                state.update(status='invalid_json', reason='no_parseable_final_JSON_no_repair')
                break
            save(directory / 'diagnosis.json', diagnosis)
            validation = validate_diagnosis(diagnosis, safe)
            save(directory / 'mechanical_validation.json', validation)
            state.update(status='mechanically_passed' if validation['passed'] else 'mechanically_failed')
            break
        else:
            state.update(status='round_budget_exhausted', reason='no_final_diagnosis_within_frozen_budget')
    except RuntimeError as exc:
        state.update(status='failed', reason=str(exc))
    except Exception as exc:
        state.update(status='runner_failed', reason=type(exc).__name__)
    save(directory / 'read_ranges.json', safe.read_ranges)
    save(directory / 'registered_extra_sources.json', [{k: v for k, v in e.items() if k != 'text'}
                                                      for e in safe.extra.values()])
    state['finished_at'] = now()
    save(directory / 'result.json', state)
    progress(states)
    return state


def summary():
    plan = read(ROOT / 'plan.json')
    records = []
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    for job in plan['jobs']:
        directory = ROOT / 'results' / job['record_id']
        row = {**job, **(read(directory / 'result.json') if (directory / 'result.json').exists()
                         else {'status': 'not_finished'})}
        if (directory / 'mechanical_validation.json').exists():
            row['mechanical_validation'] = read(directory / 'mechanical_validation.json')
        records.append(row)
        for p in (directory / 'calls').glob('*/receipt.json'):
            for k in usage:
                usage[k] += (read(p).get('usage') or {}).get(k, 0) or 0
    result = {'traces': records, 'model_calls': sum(r.get('model_rounds', 0) for r in records),
              'tool_calls': sum(r.get('tool_calls', 0) for r in records),
              'mechanically_passed': sum(r['status'] == 'mechanically_passed' for r in records),
              'usage': usage, 'semantic_accuracy_measured': False,
              'prepared_prompt_unchanged_during_run': sha(CONTRACT / 'diagnosis.prompt.md') == plan['prompt_sha256'],
              'frozen_processor_files_verified': verify_processor(),
              'official_evaluations_sent': False, 'benchmark_reexecutions': 0,
              'automatic_retries': 0, 'repair_calls': 0}
    save(ROOT / 'summary.json', result)
    shutil.copytree(ROOT, MIRROR, dirs_exist_ok=True)
    return result


def run():
    lock = (ROOT / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = verify_plan()
    index, client = EvidenceIndex(BUNDLE), NativeClient()
    states = {j['record_id']: {'task_id': j['task_id'], 'status': 'pending'} for j in plan['jobs']}
    pending = []
    for job in plan['jobs']:
        directory = ROOT / 'results' / job['record_id']
        if (directory / 'result.json').exists():
            states[job['record_id']] = read(directory / 'result.json')
        elif directory.exists():
            raise RuntimeError('uncommitted_existing_calls_require_offline_recovery_not_resubmission')
        else:
            pending.append(job)
    progress(states)
    stop = False
    with ThreadPoolExecutor(max_workers=plan['concurrency']) as pool:
        futures = {}
        while pending or futures:
            while pending and len(futures) < plan['concurrency'] and not stop:
                job = pending.pop(0)
                futures[pool.submit(run_one, index, client, job, states)] = job
            if not futures:
                break
            done, _ = wait(futures, timeout=20, return_when=FIRST_COMPLETED)
            for future in done:
                job = futures.pop(future)
                try:
                    result = future.result()
                    if result.get('reason') in ['provider_failure_no_retry', 'returned_model_mismatch']:
                        stop = True
                except Exception as exc:
                    states[job['record_id']] = {'task_id': job['task_id'], 'status': 'runner_failed',
                                                'reason': type(exc).__name__}
                    stop = True
            progress(states, 'stopping_after_failure' if stop else 'running')
    progress(states, 'stopped' if stop else 'finished')
    result = summary()
    print(encode({k: v for k, v in result.items() if k != 'traces'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.run:
        run()
