#!/usr/bin/env python3
"""Index existing evaluated sources; no event/stage/error semantics are inferred."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / 'runs'
OLD_MANIFEST = RUNS.parent / 'data/trace_cleaning_global_v1/clean_candidate_manifest.jsonl'


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def dirs(path):
    return sorted(x for x in path.iterdir() if x.is_dir() and not x.name.startswith('.')) if path.is_dir() else []


def build(use_current=True):
    # New jobs must not silently reintroduce quarantined historical sources.
    # Existing frozen experiment manifests remain unchanged for provenance.
    current_path = RUNS.parent / 'data/CURRENT_TRACE_CORPUS.json'
    if use_current and current_path.is_file():
        current = read(current_path)
        manifest = Path(current.get('manifest', ''))
        if RUNS.parent / 'data' not in manifest.parents or not manifest.is_file():
            raise RuntimeError('Current curated manifest is missing or outside project data')
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != current.get('manifest_sha256'):
            raise RuntimeError('Current curated manifest hash changed')
        rows = [json.loads(line) for line in manifest.read_text().splitlines() if line.strip()]
        if len(rows) != current.get('trace_count') or len({r['trace_id'] for r in rows}) != len(rows):
            raise RuntimeError('Curated corpus identity/count mismatch')
        for row in rows:
            if not all(Path(s['path']).is_file() for s in row['sources']):
                raise RuntimeError('Curated source is missing')
            if not all(Path(p).is_file() for p in row['evaluation_refs']):
                raise RuntimeError('Curated evaluation is missing')
        return rows, []
    rows = []
    missing = []

    def add(root, task_id, agent, source_paths, evaluation_refs, **metadata):
        sources = [{'path': str(path), 'role': role} for path, role in source_paths if path.is_file() and path.stat().st_size]
        evaluation_refs = [str(path) for path in evaluation_refs if path.is_file()]
        if not sources or not evaluation_refs:
            missing.append({'run': root.name, 'task_id': task_id, 'reason': 'no_source_or_evaluation'})
            return
        benchmark = metadata.pop('benchmark')
        identity = f'{root.name}/{agent}/{task_id}'
        rows.append({'schema': 'trace-processor-input-v2',
                     'trace_id': 'trace-' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                     'benchmark': benchmark, 'agent_id': agent, 'task_id': task_id,
                     'run_id': root.name, 'sources': sources, 'evaluation_refs': evaluation_refs,
                     'evaluation_status': 'evaluated', **metadata})

    # Exact runner completion/official score checks repeat the cleanup contract.
    # They select finished inputs; they never classify events inside those inputs.
    for agent, suffix in [('codex', '20260917-v1'), ('codex', '20260918-v2-prepared'), ('claude_code', '20260917-v1')]:
        prefix = 'claude' if agent == 'claude_code' else 'codex'
        root = RUNS / f'dataspace-{prefix}-soclaas-qwen36-{suffix}'
        evaluator = root / ('evaluation_summary.json' if prefix == 'claude' else 'evaluations/evaluation_summary.json')
        for task in dirs(root / 'tasks'):
            source = task / ('run_1/stdout.log' if prefix == 'claude' else 'trajectory.jsonl')
            add(root, task.name, agent, [(source, 'transcript')], [evaluator], benchmark='dataspace')

    root = RUNS / 'agenticdatabench-codex-soclaas-qwen36-20260917-v8'
    for task in dirs(root / 'tasks'):
        add(root, task.name, 'codex', [(task / 'events.jsonl', 'transcript')],
            [root / 'evaluation' / task.name / 'evaluation_record.json'], benchmark='agenticdatabench')
    root = RUNS / 'agenticdatabench-kimi-soclaas-qwen36-20260920-v1'
    for task in dirs(root / 'attempts'):
        attempts = [a for a in dirs(task) if read(a / 'status.json').get('return_code') == 0]
        if attempts:
            add(root, task.name, 'kimi_code', [(attempts[-1] / 'events.jsonl', 'transcript')],
                [root / 'evaluation' / task.name / 'evaluation_record.json'], benchmark='agenticdatabench')
    root = RUNS / 'agenticdatabench-claude-soclaas-qwen36-20260918-v1'
    for output in dirs(root / 'output'):
        for task in dirs(output):
            evaluations = [p for p in (root / 'evaluation' / task.name / 'result').glob('*.json') if not p.name.startswith('.')]
            add(root, task.name, 'claude_code', [(task / 'dabench/result.json', 'transcript')],
                evaluations, benchmark='agenticdatabench')

    for name in ('longds-claude-soclaas-qwen36-v1', 'longds-claude-soclaas-qwen36-v1.1-remaining'):
        root = RUNS / name
        for state_path in sorted((root / 'tasks').glob('*/*/*/task_state.json')):
            task = state_path.parent
            if read(state_path).get('status') != 'completed' or read(task / 'results_eval.json').get('status') != 'completed':
                continue
            sources = [(p, 'transcript') for p in sorted((task / 'detail').glob('turn_*/claude_stdout.jsonl'))]
            add(root, str(task.relative_to(root / 'tasks')), 'claude_code', sources,
                [task / 'results_eval.json'], benchmark='longds')

    for agent, mode in [('codex', 'direct'), ('claude', 'relay'), ('kimi', 'direct'), ('deepseek', 'direct')]:
        root = RUNS / f'dabstep-{agent}-soclaas-{mode}-qwen36-20261004-v1'
        task_root = root / 'tasks/dev' if agent == 'codex' else root / 'tasks'
        for task in dirs(task_root):
            result = read(task / 'result.json')
            attempt = Path(result['attempt']) if result.get('attempt') else task
            if not (attempt / 'turn_model_io.jsonl').is_file():
                attempts = dirs(task / 'attempts')
                attempt = attempts[-1] if attempts else task
            evaluator = attempt / 'evaluation.json'
            if not evaluator.is_file():
                evaluator = attempt / 'result.json'
            add(root, task.name, {'claude': 'claude_code', 'kimi': 'kimi_code'}.get(agent, agent),
                [(attempt / {'codex': 'codex_events.jsonl', 'claude': 'claude.stream.jsonl',
                             'kimi': 'stream_events.jsonl', 'deepseek': 'stream_events.jsonl'}[agent], 'transcript'),
                 (attempt / 'turn_model_io.jsonl', 'model_io')], [evaluator], benchmark='dabstep')

    root = RUNS / 'dataagentbench-soclaas-20261004'
    for agent in ('codex', 'claude', 'kimi', 'deepseek'):
        for task in dirs(root / agent / 'tasks'):
            result = read(task / 'result.json')
            attempt = Path(result['attempt']) if result.get('attempt') else task
            add(root, task.name, {'claude': 'claude_code', 'kimi': 'kimi_code'}.get(agent, agent),
                [(attempt / 'events.jsonl', 'transcript'), (attempt / 'turn_model_io.jsonl', 'model_io')],
                [attempt / 'evaluation.json'], benchmark='dataagentbench')

    # Previously adjudicated public DataSpace baselines remain eligible. Only
    # their raw path/identity/evaluation provenance enters v2; no v1 labels do.
    if OLD_MANIFEST.is_file():
        for line in OLD_MANIFEST.read_text(encoding='utf-8').splitlines():
            old = json.loads(line)
            if old.get('source_group') != 'dataspace_prior_three_agents' or old.get('decision') != 'retain_confirmed':
                continue
            bundle = Path(old['source_path'])
            bundle_metadata = read(bundle / 'bundle.json')
            raw_root = Path(bundle_metadata['raw_run_dir']) if bundle_metadata.get('raw_run_dir') else None
            path = raw_root / 'events.jsonl' if raw_root else Path(old['trace_path'])
            if not path.is_file():
                missing.append({'trace_id': old['trace_id'], 'reason': 'prior_source_missing'})
                continue
            rows.append({'schema': 'trace-processor-input-v2', 'trace_id': old['trace_id'],
                         'benchmark': old.get('benchmark'), 'agent_id': old.get('agent_id'),
                         'model_id': old.get('model_id'), 'task_id': old.get('task_id'),
                         'run_id': old.get('run_id'), 'sources': [{'path': str(path), 'role': 'transcript'}],
                         'evaluation_refs': [str(OLD_MANIFEST)],
                         'evaluation_selector': {'trace_id': old['trace_id']},
                         'evaluation_status': old.get('evaluation_status')})
    ids = [row['trace_id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate trace identity')
    return rows, missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, missing = build()
    (args.output / 'manifest.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
    groups = Counter((row['benchmark'], row['agent_id'], row['run_id']) for row in rows)
    summary = {'trace_count': len(rows), 'missing': missing,
               'groups': [{'benchmark': k[0], 'agent_id': k[1], 'run_id': k[2], 'count': v}
                          for k, v in sorted(groups.items())]}
    (args.output / 'manifest_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    # One trace from each group, selected by stable hash without inspecting
    # content/score. These inputs are for a format smoke test, not accuracy.
    selected = []
    for group in sorted(groups):
        candidates = [row for row in rows if (row['benchmark'], row['agent_id'], row['run_id']) == group]
        selected.append(min(candidates, key=lambda row: hashlib.sha256(row['trace_id'].encode()).hexdigest()))
    (args.output / 'pilot_manifest.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in selected))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
