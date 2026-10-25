"""Check recorded message continuity and novel source coverage, without model calls."""
import copy
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('rerun', Path(__file__).with_name('run.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def pages(value):
    if isinstance(value, dict):
        if value.get('status') == 'source_excerpt' and isinstance(value.get('text'), str):
            yield value
        for x in value.values():
            yield from pages(x)
    elif isinstance(value, list):
        for x in value:
            yield from pages(x)


def add_pages(seen, value):
    novel = 0
    for p in pages(value):
        chars = set(range(p['block_char_start'], p['block_char_end']))
        known = seen.setdefault(p['evidence_id'], set())
        novel += len(chars - known)
        known.update(chars)
    return novel


def audit():
    plan = r.verify()
    records = []
    for job in plan['jobs']:
        rid = job['record_id']
        folder = r.ROOT / 'results' / rid
        assert (folder / 'result.json').exists(), 'Only audit terminal records'
        messages = [
            {'role': 'system', 'content': (r.b.CONTRACT / 'diagnosis.prompt.md').read_text()},
            {'role': 'user', 'content': 'CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n' +
             r.b.encode(r.b.read(r.ROOT / 'inputs' / (rid + '.json')))}]
        reads = [r.b.read(p) for p in sorted((folder / 'tool_reads').glob('*.json'))]
        calls = sorted((folder / 'calls').iterdir())
        returned = 0
        for call in calls:
            req = r.b.read(call / 'request.json')
            assert req['messages'] == messages, f'{rid}/{call.name}: message continuity'
            assert req['tools'] == r.b.TOOLS and req['tool_choice'] == 'auto'
            for k in ['model', 'max_tokens', 'thinking_token_budget', 'temperature']:
                assert req[k] == plan[k], (rid, call.name, k)
            assert 'response_format' not in req
            if not (call / 'response.json').exists():
                assert call == calls[-1]
                break
            returned += 1
            response = r.b.read(call / 'response.json')
            assert response['model'] == plan['expected_returned_model']
            native = response['choices'][0]['message']
            tools = native.get('tool_calls') or []
            if not tools:
                assert call == calls[-1]
                break
            message = {k: copy.deepcopy(native[k]) for k in
                       ['role', 'content', 'tool_calls', 'reasoning_content', 'reasoning'] if k in native}
            message.setdefault('role', 'assistant')
            messages.append(message)
            actual_reads = [x for x in reads if x['round'] == int(call.name)]
            # At a tool-budget failure the runner can stop partway through a batch.
            assert len(actual_reads) <= len(tools)
            for tool, entry in zip(tools, actual_reads):
                assert tool['function']['name'] == entry['name']
                messages.append({'role': 'tool', 'tool_call_id': tool['id'],
                                 'content': r.b.encode(entry['result'])})
        seen = {}
        add_pages(seen, r.b.read(r.ROOT / 'inputs' / (rid + '.json')))
        efficiency = []
        for entry in reads:
            count = len(list(pages(entry['result'])))
            novel = add_pages(seen, entry['result'])
            efficiency.append({'round': entry['round'], 'ordinal': entry['ordinal'],
                               'name': entry['name'], 'arguments': entry['arguments'],
                               'source_pages': count, 'new_source_characters': novel,
                               'fully_repeated_source_read': bool(count and novel == 0)})
        records.append({'record_id': rid, 'task_id': job['task_id'],
                        'requests_verified': len(calls), 'responses_verified': returned,
                        'message_continuity_verified': True, 'tool_returns_included': True,
                        'runtime_parameters_unchanged': True,
                        'fully_repeated_source_reads': sum(x['fully_repeated_source_read'] for x in efficiency),
                        'read_efficiency': efficiency})
    r.b.save(r.ROOT / 'message_and_read_audit.json', {'records': records, 'new_model_calls': 0})
    print(r.b.encode([{k: v for k, v in row.items() if k != 'read_efficiency'} for row in records]))


if __name__ == '__main__':
    audit()
