"""Source-bound stock-CLI Unicode adoption, not semantic equivalence.

Only the empirically qualified Bash command transport normalization is supported.
Never rewrite execution, infer a result, decode double escapes or accept arbitrary
argument differences. Actual CLI adoption and real results remain mandatory.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from curate_complete_traces import digest, read, records

VERSION = 'stock-cli-unicode-adoption-v1'
EDIT_VERSION = 'stock-cli-edit-unicode-adoption-v2'
EDIT_WHITESPACE_VERSION = 'stock-cli-edit-new-string-trailing-whitespace-v3'
PROJECT = Path(__file__).resolve().parents[1]
QUALIFICATION = PROJECT / '.complete-trace-reruns/cli-unicode-qualification-v1.json'
EDIT_QUALIFICATION = PROJECT / '.complete-trace-reruns/cli-edit-unicode-qualification-v2.json'
EDIT_WHITESPACE_QUALIFICATION = PROJECT / '.complete-trace-reruns/cli-edit-whitespace-qualification-v3.json'
RESULT_CONTEXT_QUALIFICATION = PROJECT / '.complete-trace-reruns/cli-result-context-qualification-v1.json'
PATTERN = re.compile(r'(?<!\\)\\u([0-9a-fA-F]{4})')


def normalized_command(value):
    if not isinstance(value, str): raise ValueError('Original command is not a string')
    def replace(match):
        code = int(match.group(1), 16)
        if code < 32 or code == 127: raise ValueError('Control-character adoption is not qualified')
        return chr(code)
    value = PATTERN.sub(replace, value)
    # Combine only valid UTF-16 surrogate pairs; lone surrogates fail closed.
    return value.encode('utf-16-le', 'surrogatepass').decode('utf-16-le')


def qualification(attempt, changes=()):
    whitespace = any(c.get('normalization') == EDIT_WHITESPACE_VERSION for c in changes)
    if whitespace:
        qualification(attempt, [{'normalization': EDIT_VERSION}])
        if any(c.get('normalization') == VERSION for c in changes):
            qualification(attempt, [{'normalization': VERSION}])
    edit = any(c.get('normalization') == EDIT_VERSION for c in changes)
    if edit and any(c.get('normalization') == VERSION for c in changes):
        qualification(attempt, [c for c in changes if c.get('normalization') == VERSION])
    source = EDIT_WHITESPACE_QUALIFICATION if whitespace else EDIT_QUALIFICATION if edit else QUALIFICATION
    q = read(source) or {}
    manifest = read(Path(attempt) / 'isolation/manifest.json') or {}
    if (q.get('version') != (EDIT_WHITESPACE_VERSION if whitespace else EDIT_VERSION if edit else VERSION) or q.get('real_provider_requests') != 0
            or q.get('qualified_cases') != (11 if whitespace else 6 if edit else 8) or q.get('semantic_equivalence_claimed') is not False
            or not q.get('cli_sha256') or q['cli_sha256'] != manifest.get('cli_sha256')):
        raise ValueError('Stock CLI normalization qualification/binary binding missing')
    root = Path(q['artifact_root'])
    if root.parent != PROJECT / 'outputs': raise ValueError('Qualification evidence outside project outputs')
    for name, sha in q['source_sha256'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or digest(root / relative) != sha:
            raise ValueError('Qualification raw evidence changed')
    return digest(source)


def cli_snapshot(attempt):
    path = Path(attempt) / 'events.jsonl'
    if not path.is_file(): return []
    raw = path.read_bytes()
    # Concurrent progress may be mid-line. Use only complete, already-flushed
    # source events; final capture independently re-reads the entire raw session.
    raw = raw[:raw.rfind(b'\n') + 1]
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def adoption(original, replayed, response_id, cli, schemas=None):
    """Return exact bound adoption evidence or fail; never assert equivalence."""
    from claude_soclaas_chat_capture import exact_json, replay_content, replay_matches
    old = replay_content([original])[0]; new = replay_content([replayed])[0]
    if (old.get('type') != 'tool_use' or old.get('name') not in ('Bash', 'Edit')
            or not exact_json({k: v for k, v in old.items() if k != 'input'},
                              {k: v for k, v in new.items() if k != 'input'})):
        raise ValueError('Not a bound stock Bash Unicode normalization')
    a = old.get('input'); b = new.get('input')
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise ValueError('Not the qualified exact Unicode command transformation')
    expected = copy.deepcopy(a); fields = []; whitespace_fields = []
    candidates = ('command',) if old['name'] == 'Bash' else ('old_string', 'new_string')
    for field in candidates:
        if field in a and field in b and not exact_json(a[field], b[field]):
            decoded = normalized_command(a[field])
            if decoded != b[field]:
                if (old['name'] != 'Edit' or field != 'new_string'
                        or re.sub(r'[ \t]+(?=\r?\n|\Z)', '', decoded) != b[field]):
                    raise ValueError('Not the qualified exact Unicode command transformation')
                whitespace_fields.append(field)
            expected[field] = b[field]; fields.append(field)
    if (not fields or not replay_matches([{**old, 'input': expected}], [new], schemas)):
        raise ValueError('Not the qualified exact Unicode command transformation')
    sessions = {e['session_id'] for e in cli if e.get('session_id')}
    if len(sessions) != 1: raise ValueError('Unicode adoption needs one actual CLI session')
    session = next(iter(sessions)); accepted = []; results = []
    for index, event in enumerate(cli):
        msg = event.get('message', {})
        content = msg.get('content', [])
        if not isinstance(content, list): continue
        for block in content:
            if event.get('type') == 'assistant' and block.get('id') == old['id']:
                if (event.get('session_id') != session or msg.get('id') != response_id
                        or not exact_json(replay_content([block])[0], new)):
                    raise ValueError('CLI adopted body/response differs from actual replay')
                accepted.append(index)
            if event.get('type') == 'user' and block.get('tool_use_id') == old['id']:
                if event.get('session_id') != session: raise ValueError('Mixed execution result session')
                results.append((index, block))
    if len(accepted) != 1 or len(results) != 1 or accepted[0] >= results[0][0]:
        raise ValueError('Actual CLI adoption and ordered real result required')
    proof = {'normalization': VERSION if old['name'] == 'Bash' else EDIT_VERSION, 'response_id': response_id, 'tool_use_id': old['id'],
        'cli_session_id': session, 'assistant_event_index': accepted[0], 'result_event_index': results[0][0],
        'model_generated_input': copy.deepcopy(a), 'cli_adopted_input': copy.deepcopy(b),
        'actual_cli_result_sha256': hashlib.sha256(json.dumps(results[0][1], sort_keys=True,
            ensure_ascii=True).encode()).hexdigest(), 'semantic_equivalence_certified': False}
    if old['name'] == 'Edit':
        proof.update(normalized_fields=fields, schema_defaults_added=sorted(set(b) - set(a)))
        if whitespace_fields:
            proof.update(normalization=EDIT_WHITESPACE_VERSION, trailing_whitespace_fields=whitespace_fields)
    return proof


def bind_replay(original, replayed, response_id, cli, schemas=None):
    from claude_soclaas_chat_capture import exact_json, replay_content, replay_matches
    a = replay_content(original); b = replay_content(replayed)
    if len(a) != len(b): raise ValueError('Changed assistant block count')
    changes = []
    for old, new in zip(a, b):
        if replay_matches([old], [new], schemas): continue
        changes.append(adoption(old, new, response_id, cli, schemas))
    if not changes: raise ValueError('No observed Unicode adoption')
    return changes


def validate_replayed_results(changes, payload, cli):
    from claude_soclaas_chat_capture import content_blocks, exact_json, replay_content
    context_proofs = []
    for item in changes:
        tid = item['tool_use_id']
        actual = [b for e in cli if e.get('type') == 'user'
            for b in content_blocks(e.get('message', {}).get('content', [])) if b.get('tool_use_id') == tid]
        replayed = [b for m in payload['messages'] if m['role'] == 'user'
            for b in content_blocks(m['content']) if b.get('tool_use_id') == tid]
        if len(actual) != 1 or len(replayed) != 1:
            raise ValueError('Unicode replay result differs from the actual CLI result')
        if not exact_json(replay_content(actual), replay_content(replayed)):
            context_proofs.append(result_context_proof(actual[0], replayed[0], cli))
    return context_proofs


def result_context_proof(actual, replayed, cli):
    """Bind one native compact reminder, never erase or fabricate a tool result.

    Only the exact default unmanaged-attribution suffix observed on stock Edit
    is qualified. Both raw strings remain distinct; no semantic equivalence.
    """
    from claude_soclaas_chat_capture import content_blocks, exact_json, replay_content
    a = replay_content([actual])[0]; b = replay_content([replayed])[0]
    q = read(RESULT_CONTEXT_QUALIFICATION) or {}
    if (q.get('version') != 'stock-cli-edit-result-compact-attribution-v1'
            or q.get('real_provider_requests') != 0 or q.get('qualified_cases') != 1
            or q.get('semantic_equivalence_claimed') is not False
            or q.get('original_cli_framework_or_tools_modified') is not False
            or a.get('type') != 'tool_result' or a.get('is_error')
            or not isinstance(a.get('content'), str) or not a['content']
            or not isinstance(q.get('exact_suffix'), str)
            or not q['exact_suffix'].startswith('\n\n<system-reminder>\nAttribution for git commits')
            or not exact_json({**a, 'content': a['content'] + q['exact_suffix']}, b)):
        raise ValueError('Unicode replay result differs from the actual CLI result; unqualified context suffix')
    sessions = {e['session_id'] for e in cli if e.get('session_id')}
    init = [e for e in cli if e.get('type') == 'system' and e.get('subtype') == 'init']
    if len(sessions) != 1 or len(init) != 1 or not init[0].get('cwd'):
        raise ValueError('Context suffix needs one native CLI session and initialization')
    manifest = read(Path(init[0]['cwd']).parent / 'isolation/manifest.json') or {}
    if not q.get('cli_sha256') or manifest.get('cli_sha256') != q['cli_sha256']:
        raise ValueError('Context suffix stock binary qualification missing')
    root = Path(q['artifact_root'])
    if root.parent != PROJECT / 'outputs' or root.is_symlink():
        raise ValueError('Context qualification outside project outputs')
    for name, expected in q['source_sha256'].items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or digest(root / rel) != expected:
            raise ValueError('Context qualification raw evidence changed')
    session = next(iter(sessions)); tid = a.get('tool_use_id')
    calls = []; results = []; boundaries = []
    for i, event in enumerate(cli):
        message = event.get('message', {})
        for block in content_blocks(message.get('content', []) if isinstance(message, dict) else []):
            if event.get('type') == 'assistant' and block.get('id') == tid:
                if block.get('name') != 'Edit': raise ValueError('Only native Edit result suffix qualified')
                calls.append(i)
            if event.get('type') == 'user' and block.get('tool_use_id') == tid:
                if not exact_json(replay_content([block])[0], a):
                    raise ValueError('Context source differs from actual tool result')
                results.append((i, event))
        if event.get('type') == 'system' and event.get('subtype') == 'compact_boundary':
            boundaries.append((i, event))
    if (len(calls) != 1 or len(results) != 1 or calls[0] >= results[0][0]
            or not results[0][1].get('uuid')):
        raise ValueError('Context requires ordered actual Edit execution/result')
    candidates = [(i, e) for i, e in boundaries if i > results[0][0]
        and e.get('session_id') == session and e.get('compact_metadata', {}).get('trigger') == 'auto'
        and results[0][1]['uuid'] in e.get('compact_metadata', {}).get('preserved_messages', {}).get('all_uuids', [])]
    if not candidates: raise ValueError('No native auto compact boundary preserving actual result')
    def hashed(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {'qualification': 'stock-cli-edit-result-compact-attribution-v1',
        'qualification_sha256': digest(RESULT_CONTEXT_QUALIFICATION), 'tool_use_id': tid,
        'cli_session_id': session, 'actual_result_event_index': results[0][0],
        'compact_boundary_event_index': candidates[0][0], 'actual_result_sha256': hashed(a),
        'model_visible_result_block_sha256': hashed(b),
        'added_context_sha256': hashlib.sha256(q['exact_suffix'].encode()).hexdigest(),
        'added_context_is_not_execution_output': True, 'semantic_equivalence_certified': False}
