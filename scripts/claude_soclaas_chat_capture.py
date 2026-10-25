"""Versioned external Anthropic/Chat bridge and source-bound capture, v1.

Stock CLI, prompts and tools are unchanged. No Responses-shaped upstream
records, invented tool IDs/results, retries, or hidden-reasoning claims.
"""
import hashlib
import copy
import json
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler

from anthropic_soclaas_relay import _RelayServer as BaseServer, _RelayHandler as BaseHandler
from anthropic_soclaas_relay import _ssl_context, _json_bytes, _sse_event
from curate_complete_traces import records, digest, dump, read
from probe_soclaas_chat_provider import NoRedirect, ENDPOINT
from probe_trace_processor_provider import safe_error_metadata

VERSION = 'claude-chat-capture-v11-qualified-edit-result-context'
MODEL = 'x-test-2'
ACTUAL_MODEL = 'deepseek-v4.1-flash-1017'
# Offline verification only. Live conversion always defaults to ACTUAL_MODEL;
# historical names are never live fallbacks and each attempt has one identity.
HISTORICAL_MODELS = ('deepseek-v4.1-flash',)
WIRE_KEYS = ('incoming_anthropic_request', 'outgoing_soclaas_chat_request',
             'incoming_soclaas_chat_response', 'outgoing_anthropic_response')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def content_blocks(value):
    if isinstance(value, str):
        return [{'type': 'text', 'text': value}]
    if not isinstance(value, list):
        raise ValueError('Unsupported content representation')
    return value


def chat_content(blocks):
    result = []
    for b in blocks:
        if b.get('type') == 'text' and isinstance(b.get('text'), str):
            result.append({'type': 'text', 'text': b['text']})
        elif b.get('type') == 'image':
            s = b['source']
            if s.get('type') == 'base64':
                url = 'data:' + s['media_type'] + ';base64,' + s['data']
            elif s.get('type') == 'url':
                url = s['url']
            else:
                raise ValueError('Unsupported image source; no placeholder substituted')
            result.append({'type': 'image_url', 'image_url': {'url': url}})
        else:
            raise ValueError('Unsupported content block; no silent omission')
    if all(b['type'] == 'text' for b in result):
        return ''.join(b['text'] for b in result)
    return result


def anthropic_response(body, requested_model, *, expected_model=None):
    expected_model = ACTUAL_MODEL if expected_model is None else expected_model
    if expected_model not in (ACTUAL_MODEL, *HISTORICAL_MODELS):
        raise ValueError('Unapproved exact model identity')
    if body.get('model') != expected_model or not body.get('id'):
        raise ValueError('Unexpected upstream model or absent response identity')
    choices = body.get('choices', [])
    if len(choices) != 1:
        raise ValueError('Expected one complete model choice')
    choice = choices[0]; message = choice['message']; finish = choice.get('finish_reason')
    if message.get('role') != 'assistant' or finish not in ('stop', 'tool_calls'):
        raise ValueError('Incomplete/refused model response')
    out = []
    text = message.get('content')
    if text is not None:
        if not isinstance(text, str):
            raise ValueError('Unsupported assistant content')
        if text:
            out.append({'type': 'text', 'text': text})
    seen = set()
    for call in message.get('tool_calls') or []:
        if call.get('type') != 'function' or not call.get('id') or call['id'] in seen:
            raise ValueError('Missing/duplicate/nonfunction tool identity')
        seen.add(call['id']); fn = call['function']
        arguments = json.loads(fn['arguments'])
        if not isinstance(arguments, dict) or not fn.get('name'):
            raise ValueError('Invalid tool arguments; no fallback values')
        out.append({'type': 'tool_use', 'id': call['id'], 'name': fn['name'], 'input': arguments})
    if bool(seen) != (finish == 'tool_calls') or not out:
        raise ValueError('Finish reason/content mismatch or reasoning-only output')
    usage = body['usage']
    for name in ('prompt_tokens', 'completion_tokens'):
        if not isinstance(usage.get(name), int) or isinstance(usage[name], bool) or usage[name] < 0:
            raise ValueError('Missing recorded usage; no invented token counts')
    # Message-envelope identity is translated, not an invented execution ID.
    # Every tool execution ID above comes verbatim from the actual Chat body.
    return {'id': 'msg_' + sha(body['id'].encode())[:32], 'type': 'message', 'role': 'assistant',
            'model': requested_model, 'content': out, 'stop_reason': 'tool_use' if seen else 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': usage['prompt_tokens'],
                                          'output_tokens': usage['completion_tokens']}}


def remember(cache, body, translated, tools=()):
    ids = tuple(b['id'] for b in translated['content'] if b['type'] == 'tool_use')
    if ids:
        if ids in cache:
            raise ValueError('Repeated upstream tool-call identity')
        cache[ids] = (translated['content'], body['choices'][0]['message'], tool_schemas(tools), translated['id'])


def tool_schemas(tools):
    result = {}
    for tool in tools:
        if tool['name'] in result:
            raise ValueError('Duplicate tool schema identity')
        result[tool['name']] = copy.deepcopy(tool['input_schema'])
    return result


def exact_json(a, b):
    # Python equality conflates false/0 and true/1; wire arguments must not.
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def replay_matches(original, replayed, schemas=None):
    """Comparison only: allow added optional scalar defaults explicitly declared
    by the original request's tool schema. Never add values to outgoing arguments,
    mutate evidence, infer defaults, or normalize nested/substantive differences.
    Complex/ambiguous schemas fail closed until separately supported and tested.
    """
    old = replay_content(original); new = replay_content(replayed)
    if len(old) != len(new): return False
    for a, b in zip(old, new):
        if exact_json(a, b): continue
        if a.get('type') != 'tool_use' or b.get('type') != 'tool_use': return False
        if not exact_json({k: v for k, v in a.items() if k != 'input'},
                          {k: v for k, v in b.items() if k != 'input'}): return False
        ai = a.get('input'); bi = b.get('input')
        if not isinstance(ai, dict) or not isinstance(bi, dict) or not set(ai) < set(bi): return False
        if any(not exact_json(v, bi.get(k)) or k not in bi for k, v in ai.items()): return False
        schema = (schemas or {}).get(a.get('name'), {})
        if (schema.get('type') != 'object' or set(schema) - {
                'type', 'properties', 'required', 'additionalProperties', 'description', 'title', '$schema'}):
            return False
        for key in set(bi) - set(ai):
            field = schema.get('properties', {}).get(key, {})
            if (key in schema.get('required', []) or 'default' not in field
                    or set(field) - {'type', 'default', 'description', 'title', 'enum', 'const'}): return False
            value = bi[key]; kind = field.get('type')
            valid = ((kind == 'boolean' and type(value) is bool)
                     or (kind == 'string' and type(value) is str)
                     or (kind == 'integer' and type(value) is int)
                     or (kind == 'number' and type(value) in (int, float))
                     or (kind == 'null' and value is None))
            if not valid or not exact_json(value, field['default']): return False
            if 'enum' in field and not any(exact_json(value, x) for x in field['enum']): return False
            if 'const' in field and not exact_json(value, field['const']): return False
    return True


def replay_content(blocks):
    """Compare semantics independently of validated top-level cache hints.

Never edit originals or recursively remove keys from tool input/results. Cache
hints belong to the client protocol, not to the tool name, identity or argument.
Unknown metadata still fails closed and remains verbatim in the raw request.
"""
    result = []
    for block in blocks:
        copied = dict(block)
        if 'cache_control' in copied:
            hint = copied['cache_control']
            if (not isinstance(hint, dict) or set(hint) - {'type', 'ttl'}
                    or hint.get('type') != 'ephemeral'
                    or ('ttl' in hint and hint['ttl'] not in ('5m', '1h'))):
                raise ValueError('Unsupported cache metadata; no silent omission')
            del copied['cache_control']
        result.append(copied)
    return result


def chat_request(p, model, cache, *, cli_events=None, adoptions=None):
    if model != MODEL:
        raise ValueError('Unapproved model alias')
    messages = []
    if p.get('system'):
        messages.append({'role': 'system', 'content': chat_content(content_blocks(p['system']))})
    for msg in p['messages']:
        role = msg['role']; blocks = content_blocks(msg['content'])
        if role == 'assistant':
            ids = tuple(b['id'] for b in blocks if b.get('type') == 'tool_use')
            if ids:
                if ids not in cache:
                    raise ValueError('Assistant replay not bound to original upstream output')
                saved = cache[ids]
                schemas = saved[2]
                changed = []
                if not replay_matches(saved[0], blocks):
                    current = tool_schemas(p.get('tools', []))
                    if any(not exact_json(current.get(name), schema) for name, schema in schemas.items()):
                        raise ValueError('Assistant replay not bound to original upstream output')
                    if not replay_matches(saved[0], blocks, schemas):
                        if cli_events is None:
                            raise ValueError('Assistant replay not bound to original upstream output')
                        from claude_cli_unicode_adoption import bind_replay, validate_replayed_results
                        changed = bind_replay(saved[0], blocks, saved[3], cli_events, schemas)
                        validate_replayed_results(changed, p, cli_events)
                original = cache[ids][1]
                # Preserve exact argument strings and exposed reasoning across
                # tool turns; do not regenerate reasoning or discard it from raw.
                replay_message = {k: v for k, v in original.items() if k in
                                  ('role', 'content', 'tool_calls', 'reasoning_content', 'reasoning')}
                if changed:
                    replay_message = copy.deepcopy(replay_message)
                    by_id = {a['tool_use_id']: a for a in changed}
                    for call in replay_message['tool_calls']:
                        if call['id'] in by_id:
                            # Actual CLI history, not a rewritten model response or
                            # execution command. Both original sides remain raw.
                            call['function']['arguments'] = json.dumps(by_id[call['id']]['cli_adopted_input'],
                                                                      ensure_ascii=False, sort_keys=True)
                    if adoptions is not None: adoptions.extend(changed)
                messages.append(replay_message)
            else:
                messages.append({'role': role, 'content': chat_content(blocks)})
        elif role == 'user':
            pending = []
            for b in blocks:
                if b.get('type') == 'tool_result':
                    if pending:
                        messages.append({'role': 'user', 'content': chat_content(pending)}); pending = []
                    if not b.get('tool_use_id'):
                        raise ValueError('Missing actual tool-result identity')
                    messages.append({'role': 'tool', 'tool_call_id': b['tool_use_id'],
                                     'content': chat_content(content_blocks(b.get('content', '')))})
                else:
                    pending.append(b)
            if pending:
                messages.append({'role': role, 'content': chat_content(pending)})
        elif role in ('system', 'developer'):
            messages.append({'role': role, 'content': chat_content(blocks)})
        else:
            raise ValueError('Unsupported message role')
    maximum = p.get('max_tokens')
    if not isinstance(maximum, int) or maximum < 1 or maximum > 25000:
        raise ValueError('Unexpected CLI output budget; no silent retuning')
    result = {'model': model, 'messages': messages, 'max_tokens': maximum, 'stream': False}
    tools = []
    for t in p.get('tools', []):
        if set(t) - {'name', 'description', 'input_schema', 'cache_control'} or not t.get('name'):
            raise ValueError('Unsupported tool definition; no replacement tool')
        tools.append({'type': 'function', 'function': {'name': t['name'],
                      'description': t.get('description', ''), 'parameters': t['input_schema']}})
    if tools:
        result['tools'] = tools
    for name in ('temperature', 'top_p'):
        if name in p:
            result[name] = p[name]
    if 'stop_sequences' in p:
        result['stop'] = p['stop_sequences']
    if p.get('output_config', {}).get('effort'):
        result['reasoning_effort'] = p['output_config']['effort']
    if p.get('thinking', {}).get('type') not in (None, 'adaptive'):
        raise ValueError('Unsupported thinking control; no silent retuning')
    if p.get('tool_choice'):
        choice = p['tool_choice']
        kind = choice['type'] if isinstance(choice, dict) else choice
        if kind == 'tool':
            result['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
        elif kind in ('auto', 'any', 'none'):
            result['tool_choice'] = {'any': 'required'}.get(kind, kind)
        else:
            raise ValueError('Unsupported tool choice')
        if isinstance(choice, dict) and 'disable_parallel_tool_use' in choice:
            result['parallel_tool_calls'] = not choice['disable_parallel_tool_use']
    unknown = set(p) - {'model', 'system', 'messages', 'tools', 'max_tokens', 'stream',
                       'temperature', 'top_p', 'stop_sequences', 'tool_choice', 'thinking',
                       'output_config', 'context_management', 'metadata'}
    if unknown:
        raise ValueError('Unknown Anthropic request control')
    return result


def stream_body(message):
    chunks = [_sse_event('message_start', {'type': 'message_start', 'message': {
        **message, 'content': [], 'stop_reason': None,
        'usage': {'input_tokens': message['usage']['input_tokens'], 'output_tokens': 0}}})]
    for i, block in enumerate(message['content']):
        start = {**block, ('text' if block['type'] == 'text' else 'input'): ('' if block['type'] == 'text' else {})}
        chunks.append(_sse_event('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': start}))
        delta = ({'type': 'text_delta', 'text': block['text']} if block['type'] == 'text' else
                 {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'], ensure_ascii=False)})
        chunks.append(_sse_event('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}))
        chunks.append(_sse_event('content_block_stop', {'type': 'content_block_stop', 'index': i}))
    chunks.append(_sse_event('message_delta', {'type': 'message_delta',
        'delta': {'stop_reason': message['stop_reason'], 'stop_sequence': None},
        'usage': {'output_tokens': message['usage']['output_tokens']}}))
    chunks.append(_sse_event('message_stop', {'type': 'message_stop'}))
    return b''.join(chunks)


def parsed_sse(raw):
    """Check complete SSE events without depending on JSON object key order.

The original emitted bytes are still retained and SHA256-verified separately.
"""
    frames = raw.decode('utf-8').split('\n\n')
    if frames[-1] != '': raise ValueError('Truncated CLI SSE')
    events = []
    for frame in frames[:-1]:
        lines = frame.splitlines()
        if len(lines) != 2 or not lines[0].startswith('event: ') or not lines[1].startswith('data: '):
            raise ValueError('Unsupported/truncated SSE frame')
        value = json.loads(lines[1][6:])
        # This adapter emits exactly one complete JSON argument delta per tool.
        # The recorder sorts object keys; compare the nested JSON value, not the
        # field order inside its serialized partial_json string. Incomplete JSON
        # still fails. The original emitted bytes/hash are never reconstructed.
        delta = value.get('delta', {})
        if value.get('type') == 'content_block_delta' and delta.get('type') == 'input_json_delta':
            arguments = json.loads(delta['partial_json'])
            if not isinstance(arguments, dict): raise ValueError('Non-object SSE tool input')
            value = {**value, 'delta': {**delta, 'partial_json': arguments}}
        events.append((lines[0][7:], value))
    return events


class ChatServer(BaseServer):
    def __init__(self, upstream_base, model, api_key, event_log, **kwargs):
        if upstream_base.rstrip('/') + '/chat/completions' != ENDPOINT or model != MODEL:
            raise ValueError('Unexpected upstream or model')
        super().__init__(upstream_base, model, api_key, event_log, **kwargs)
        self.RequestHandlerClass = ChatHandler
        self.exchange_lock = threading.Lock()
        self.cache = {}
        self.stopped = False
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=_ssl_context()))


class ChatHandler(BaseHandler):
    def do_POST(self):
        server = self.server
        if not urlsplit(self.path).path.endswith('/messages'):
            self._reply_json(400, {'error': {'type': 'invalid_request_error',
                                          'message': 'Unsupported path; no guessed token counts'}})
            return
        with server.exchange_lock:
            if server.stopped:
                self._reply_json(503, {'error': {'type': 'api_error', 'message': 'Relay stopped after failure; no retry'}})
                return
            tid = uuid.uuid4().hex
            def record(event, p, raw, **meta):
                server.record({'event': event, 'trace_id': tid, 'payload': p,
                               'wire_body': raw.decode('utf-8'), 'wire_sha256': sha(raw), **meta})
            try:
                raw = self.rfile.read(int(self.headers['Content-Length']))
                p = json.loads(raw)
                record(WIRE_KEYS[0], p, raw)
                from claude_cli_unicode_adoption import cli_snapshot, qualification
                attempt = server.event_log.parent
                adopted = []
                outgoing = chat_request(p, server.model, server.cache, cli_events=cli_snapshot(attempt), adoptions=adopted)
                extra = {}
                if adopted:
                    extra = {'cli_input_adoptions': adopted, 'cli_qualification_sha256': qualification(attempt, adopted)}
                request_raw = _json_bytes(outgoing)
                record(WIRE_KEYS[1], outgoing, request_raw, upstream_url=ENDPOINT, **extra)
                req = Request(ENDPOINT, method='POST', data=request_raw, headers={
                    'Content-Type': 'application/json', 'Authorization': 'Bearer ' + server.api_key})
                with server.opener.open(req, timeout=900) as response:
                    response_raw = response.read(); status = response.status
                value = json.loads(response_raw)
                record(WIRE_KEYS[2], value, response_raw, http_status=status)
                converted = anthropic_response(value, p['model'])
                remember(server.cache, value, converted, p.get('tools', []))
                rendered = stream_body(converted) if p.get('stream') else _json_bytes(converted)
                record(WIRE_KEYS[3], converted, rendered, stream=bool(p.get('stream')))
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if p.get('stream') else 'application/json')
                self.send_header('Content-Length', str(len(rendered)))
                self.send_header('Connection', 'close'); self.end_headers()
                self.wfile.write(rendered); self.wfile.flush(); self.close_connection = True
                server.record({'event': 'request_complete', 'trace_id': tid, 'http_status': status})
            except HTTPError as exc:
                server.stopped = True
                meta = safe_error_metadata(exc)
                server.record({'event': 'upstream_error', 'trace_id': tid, 'http_status': exc.code, **meta})
                self._reply_json(exc.code, {'error': {'type': 'api_error',
                    'message': meta.get('provider_error_code', 'Provider HTTP error; inspect retained evidence')}})
            except Exception as exc:
                server.stopped = True
                server.record({'event': 'relay_error', 'trace_id': tid, 'http_status': 502,
                               'error_type': type(exc).__name__})
                self._reply_json(502, {'error': {'type': 'api_error', 'message': 'Chat transport/capture failed; no retry'}})


def audit(wire, ledger):
    grouped = {}; order = []
    for e in records(wire):
        if e.get('event') not in WIRE_KEYS:
            continue
        tid = e['trace_id']
        if tid not in grouped:
            grouped[tid] = {}; order.append(tid)
        if e['event'] in grouped[tid]:
            raise ValueError('Duplicate wire side')
        grouped[tid][e['event']] = e
    rows = []
    for tid in order:
        sides = grouped[tid]
        rows.append({'native_capture_type': 'claude_chat_four_side_v1',
            'trace_id': tid, **{k: v['payload'] for k, v in sides.items()},
            'metadata': {'http_status': sides.get(WIRE_KEYS[2], {}).get('http_status'), 'wire_protocol': 'chat'}})
    dump(ledger, rows, lines=True)
    return {'model_requests': len(rows), 'turns_with_both_sides': sum(all(k in r for k in WIRE_KEYS) for r in rows),
            'upstream_wire_protocol': 'chat', 'relay': True}


def completed_cli_result(cli):
    """One normal result may be followed only by bound lifecycle teardown.

    Never skip model/tool output or an unknown event after completion. A real
    tool-result remains required by the independent execution coverage gate.
    """
    positions = [i for i, e in enumerate(cli) if e.get('type') == 'result']
    if len(positions) != 1: raise ValueError('CLI did not complete normally: result count')
    index = positions[0]; result = cli[index]; session = result.get('session_id')
    if (not session or result.get('is_error') is not False or result.get('terminal_reason') != 'completed'):
        raise ValueError('CLI did not complete normally')
    if {e.get('session_id') for e in cli if e.get('session_id')} != {session}:
        raise ValueError('Mixed CLI session')
    known_tasks = {e.get('task_id') for e in cli[:index] if e.get('task_id')}
    calls = {b.get('id') for e in cli[:index] if e.get('type') == 'assistant'
             for b in content_blocks(e.get('message', {}).get('content', [])) if b.get('type') == 'tool_use'}
    for e in cli[index + 1:]:
        if e.get('type') != 'system' or e.get('session_id') != session:
            raise ValueError('Unexpected post-result event')
        kind = e.get('subtype')
        envelope = {'type', 'subtype', 'session_id', 'uuid'}
        if kind == 'background_tasks_changed':
            if set(e) - (envelope | {'tasks'}) or e.get('tasks') != []:
                raise ValueError('Post-result background work still active or unknown payload')
        elif kind == 'task_updated':
            if (set(e) - (envelope | {'task_id', 'patch'})
                    or set(e.get('patch', {})) - {'status', 'end_time'}
                    or e.get('task_id') not in known_tasks
                    or e.get('patch', {}).get('status') not in ('killed', 'completed', 'failed', 'stopped')):
                raise ValueError('Unbound/nonterminal post-result task update')
        elif kind == 'task_notification':
            if (set(e) - (envelope | {'task_id', 'tool_use_id', 'status', 'output_file', 'summary'})
                    or e.get('task_id') not in known_tasks or e.get('tool_use_id') not in calls
                    or e.get('status') not in ('stopped', 'completed', 'failed')):
                raise ValueError('Unbound/nonterminal post-result task notification')
        else:
            raise ValueError('Unknown post-result lifecycle event')
    return result


def verify_capture(attempt, save=True):
    attempt = __import__('pathlib').Path(attempt); ledger = attempt / 'turn_model_io.jsonl'
    provider = read(attempt / 'provider.json')
    expected_model = provider.get('model') if isinstance(provider, dict) else ACTUAL_MODEL
    if expected_model not in (ACTUAL_MODEL, *HISTORICAL_MODELS):
        raise ValueError('Unapproved recorded model identity')
    if isinstance(provider, dict) and provider.get('request_model_alias') != MODEL:
        raise ValueError('Recorded model alias is not approved')
    raw = {}; completions = set()
    for e in records(attempt / 'relay_wire.jsonl'):
        if e.get('event') in WIRE_KEYS:
            key = (e['trace_id'], e['event'])
            if key in raw:
                raise ValueError('Duplicate raw side')
            body = e['wire_body'].encode()
            if sha(body) != e['wire_sha256']:
                raise ValueError('Wire body hash mismatch')
            if e['event'] != WIRE_KEYS[3] or not e.get('stream'):
                if json.loads(body) != e['payload']:
                    raise ValueError('Wire body differs from parsed body')
            elif parsed_sse(body) != parsed_sse(stream_body(e['payload'])):
                raise ValueError('CLI SSE differs from actual recorded response')
            raw[key] = e
        elif e.get('event') == 'request_complete':
            if e['trace_id'] in completions or e.get('http_status') != 200:
                raise ValueError('Duplicate/non-successful completion')
            completions.add(e['trace_id'])
        elif e.get('event') in ('upstream_error', 'relay_error'):
            raise ValueError('Observed failed model exchange')
    cli = list(records(attempt / 'events.jsonl'))
    adopted_history = {}; seen = set(); responses = {}; response_schemas = {}; generated = set(); replayed = set(); replay_results = set(); cache = {}
    for row in records(ledger):
        if row.get('native_capture_type') != 'claude_chat_four_side_v1':
            raise ValueError('Unexpected recording schema')
        tid = row['trace_id']
        if tid in seen or not all(k in row for k in WIRE_KEYS):
            raise ValueError('Duplicate/incomplete model exchange')
        seen.add(tid)
        for k in WIRE_KEYS:
            if row[k] != raw.get((tid, k), {}).get('payload'):
                raise ValueError('Ledger not bound to raw wire')
        if raw[(tid, WIRE_KEYS[1])]['upstream_url'] != ENDPOINT or raw[(tid, WIRE_KEYS[2])]['http_status'] != 200:
            raise ValueError('Unexpected upstream endpoint/status')
        incoming, outgoing, upstream, translated = [row[k] for k in WIRE_KEYS]
        adopted = []
        if chat_request(incoming, MODEL, cache, cli_events=cli, adoptions=adopted) != outgoing or anthropic_response(
                upstream, incoming['model'], expected_model=expected_model) != translated:
            raise ValueError('Protocol transformation is not source-reproducible')
        meta = raw[(tid, WIRE_KEYS[1])]
        if adopted:
            from claude_cli_unicode_adoption import qualification
            if meta.get('cli_input_adoptions') != adopted or meta.get('cli_qualification_sha256') != qualification(attempt, adopted):
                raise ValueError('CLI adoption is not bound to recorded dispatch and qualified binary')
            for item in adopted:
                old = adopted_history.setdefault(item['tool_use_id'], item)
                if old != item: raise ValueError('Inconsistent CLI adoption across replay history')
        elif 'cli_input_adoptions' in meta or 'cli_qualification_sha256' in meta:
            raise ValueError('Unexpected adoption metadata on unchanged input')
        remember(cache, upstream, translated, incoming.get('tools', []))
        if translated['id'] in responses:
            raise ValueError('Duplicate model response identity')
        responses[translated['id']] = translated
        response_schemas[translated['id']] = tool_schemas(incoming.get('tools', []))
        generated.update(b['id'] for b in translated['content'] if b['type'] == 'tool_use')
        for msg in incoming['messages']:
            for b in content_blocks(msg['content']):
                if b['type'] == 'tool_use': replayed.add(b['id'])
                if b['type'] == 'tool_result': replay_results.add(b['tool_use_id'])
    if not seen or {tid for tid, _ in raw} != seen or completions != seen or len(raw) != 4 * len(seen):
        raise ValueError('Not every model request has four completed recorded sides')
    completed_cli_result(cli)
    sessions = set(); accepted = set(); results = set()
    for e in cli:
        if e.get('session_id'): sessions.add(e['session_id'])
        message = e.get('message', {})
        if e.get('type') == 'assistant':
            r = responses.get(message.get('id'))
            if r is None:
                raise ValueError('CLI output absent from captured exchanges')
            for b in content_blocks(message.get('content', [])):
                if b.get('type') == 'tool_use':
                    if not any(replay_matches([original], [b], response_schemas[message['id']]) for original in r['content']):
                        from claude_cli_unicode_adoption import adoption, qualification
                        matches = [original for original in r['content'] if original.get('id') == b['id']]
                        if len(matches) != 1: raise ValueError('Executed tool body differs from recorded generation')
                        try:
                            actual = adoption(matches[0], b, message['id'], cli, response_schemas[message['id']])
                            qualification(attempt, [actual])
                        except ValueError as exc:
                            raise ValueError('Executed tool body differs from recorded generation: ' + str(exc)) from exc
                        old = adopted_history.setdefault(b['id'], actual)
                        if old != actual: raise ValueError('Conflicting actual adopted tool input')
                    accepted.add(b['id'])
        if e.get('type') == 'user':
            for b in content_blocks(message.get('content', [])):
                if b.get('type') == 'tool_result': results.add(b['tool_use_id'])
    if len(sessions) != 1:
        raise ValueError('Mixed or absent CLI session')
    executed = accepted | replayed; outputs = results | replay_results
    if executed != outputs or not executed <= generated:
        raise ValueError('Actual execution/results missing or unbound; do not fabricate')
    proof = {'schema': 'claude-chat-wire-cli-proof-v1', 'cli_session_id': next(iter(sessions)),
             'source_sha256': {n: digest(attempt / n) for n in ('events.jsonl', 'relay_wire.jsonl', ledger.name)},
             'generated_tool_ids': sorted(generated), 'execution_observed_tool_ids': sorted(executed),
             'recorded_tool_result_ids': sorted(outputs),
             'generated_tool_ids_not_observed_as_execution': sorted(generated - executed),
             'hidden_sdk_execution_certified': False, 'model_input_modified': False}
    if adopted_history:
        proof.update(schema='claude-chat-wire-cli-adoption-proof-v2',
            cli_tool_input_adoptions=[adopted_history[k] for k in sorted(adopted_history)],
            generated_and_executed_arguments_kept_separately=True, execution_argument_semantic_equivalence_certified=False,
            cli_actual_history_reencoded_for_upstream=True)
    context_proofs = []
    if adopted_history:
        from claude_cli_unicode_adoption import result_context_proof
        actual_results = {b['tool_use_id']: b for e in cli if e.get('type') == 'user'
            for b in content_blocks(e.get('message', {}).get('content', [])) if b.get('type') == 'tool_result'}
        for (tid, kind), event in raw.items():
            if kind != WIRE_KEYS[0]: continue
            for message in event['payload']['messages']:
                if message['role'] != 'user': continue
                for block in content_blocks(message['content']):
                    tool_id = block.get('tool_use_id')
                    if tool_id not in adopted_history: continue
                    actual = actual_results.get(tool_id)
                    if actual is not None and not exact_json(replay_content([actual]), replay_content([block])):
                        context_proofs.append({'trace_id': tid, **result_context_proof(actual, block, cli)})
    if context_proofs:
        proof['cli_result_context_augmentations'] = context_proofs
    capture = {'representation': 'anthropic_chat_four_side_wire_and_cli_execution', 'model_requests': len(seen),
               'tool_calls': len(executed), 'tool_results': len(outputs), 'errors': [],
               'upstream_model': expected_model, 'request_model_alias': MODEL, 'wire_protocol': 'chat',
               'four_side_raw_body_hashes_verified': True, 'all_cli_sessions': len(sessions),
               'hidden_reasoning_available': False, 'exposed_reasoning_retained_in_raw': True,
               'semantic_quality_certified': False, 'generated_tool_ids_not_observed_as_execution': sorted(generated - executed)}
    if adopted_history:
        capture.update(cli_adopted_input_changes=len(adopted_history),
            execution_argument_semantic_equivalence_certified=False, generated_and_executed_arguments_kept_separately=True)
    if context_proofs:
        capture['cli_result_context_augmentations'] = len(context_proofs)
    if save:
        dump(attempt / 'wire_execution_proof.json', proof)
        dump(attempt / 'capture-check.json', capture)
    elif read(attempt / 'wire_execution_proof.json') != proof:
        raise ValueError('Stored execution proof differs from actual recorded sources')
    return capture
