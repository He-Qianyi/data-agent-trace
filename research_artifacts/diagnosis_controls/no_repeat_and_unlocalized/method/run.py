"""Fresh rerun of the two v1/24 non-submitting cases with the latest candidate prompt."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))
spec = importlib.util.spec_from_file_location('original_v1', BASE / 'work/diagnosis_eight_native_tools_20261018/run.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
from pipeline.stages.diagnosis.validation import validate_diagnosis, POLICY_ID
from revalidate_diagnosis_outputs import reference_audit

ROOT = BASE / 'outputs/diagnosis_no_repeat_unknown_two_20261018'
OLD = BASE / 'outputs/diagnosis_eight_native_tools_20261018'
IDS = ['rawio-bcb49010f78ccd4af446', 'rawio-b4aa2e6b636cb50f6643']
CANDIDATE = BASE / 'outputs/diagnosis_prompt_v1_no_repeat_unknown_candidate_20261018'
SCHEMA = BASE / 'outputs/diagnosis_prompt_v1_20261018/diagnosis.schema.json'
PREVIOUS = {IDS[0]: BASE / 'outputs/diagnosis_v1_round24_20261018', IDS[1]: BASE / 'outputs/diagnosis_v1_service_rerun_two_20261018'}
b.ROOT = ROOT
b.CONTRACT = CANDIDATE


def verify():
    plan = b.read(ROOT / 'plan.json')
    for path, digest in plan['guarded_method_files'].items():
        assert b.sha(BASE / path) == digest, path
    assert b.sha(Path(__file__)) == plan['runner_sha256']
    b.verify_processor()
    return plan


def progress(states, status='running'):
    with b.LOCK:
        data = {'status': status, 'updated_at': b.now(), 'worker_pid': os.getpid(),
                'total_traces': 2, 'traces': copy.deepcopy(states)}
        b.save(ROOT / 'progress.json', data)
        print(b.encode(data), flush=True)


def prepare():
    assert not ROOT.exists(), 'Use a new experiment directory; never overwrite a recorded run'
    runtime = b.read(BASE / 'configs/05_diagnosis/CURRENT_RUNTIME.json')
    assert runtime['prompt_version'] == 'original-v1'
    assert runtime['max_model_rounds_per_trace'] == 24
    assert runtime['tool_choice'] == 'auto' and not runtime['forced_final_round']
    assert runtime['automatic_retries'] == 0
    old = b.read(OLD / 'plan.json')
    candidate_manifest = b.read(CANDIDATE / 'change_manifest.json')
    assert b.sha(CANDIDATE / 'diagnosis.prompt.md') == candidate_manifest['candidate_sha256']
    runtime = {**runtime, 'prompt_path': str(CANDIDATE / 'diagnosis.prompt.md'),
               'prompt_version': 'v1-no-repeat-and-unlocalized-candidate',
               'prompt_sha256': candidate_manifest['candidate_sha256']}
    index = b.EvidenceIndex(b.BUNDLE)
    ROOT.mkdir(parents=True)
    jobs = []
    for rid in IDS:
        job = next(x for x in old['jobs'] if x['record_id'] == rid)
        previous = b.read(PREVIOUS[rid] / 'results' / rid / 'result.json')
        assert previous['status'] == 'round_budget_exhausted'
        assert previous['model_rounds'] == 24
        assert not (PREVIOUS[rid] / 'results' / rid / 'diagnosis.json').exists()
        safe = b.SafeEvidence(index, rid)
        packet = safe.initial()
        assert packet == b.read(OLD / 'inputs' / (rid + '.json'))
        b.save(ROOT / 'inputs' / (rid + '.json'), packet)
        assert b.sha(ROOT / 'inputs' / (rid + '.json')) == job['input_sha256']
        jobs.append({**job, 'historical_status': previous['status'],
                     'mode': 'fresh_from_round1_no_old_responses_reused',
                     'comparison_source': str(PREVIOUS[rid].relative_to(BASE)),
                     'baseline_model_rounds': previous['model_rounds'],
                     'baseline_tool_calls': previous['tool_calls']})
    paths = [Path(runtime['prompt_path']), CANDIDATE / 'change_manifest.json', SCHEMA,
             BASE / 'pipeline/stages/evidence_builder/index.py',
             BASE / 'pipeline/stages/diagnosis/validation.py',
             BASE / 'configs/05_diagnosis/CURRENT_RUNTIME.json',
             BASE / 'configs/05_diagnosis/CURRENT_ACCEPTANCE.json',
             BASE / 'work/diagnosis_eight_native_tools_20261018/run.py']
    paths += [ROOT / 'inputs' / (rid + '.json') for rid in IDS]
    paths += [b.BUNDLE / 'traces' / (rid + '.json') for rid in IDS]
    historical = {str(p.relative_to(BASE)): b.sha(p)
                  for folder in [OLD, BASE / 'outputs/diagnosis_v1_round24_20261018', BASE / 'outputs/diagnosis_v1_service_rerun_two_20261018']
                  for p in folder.rglob('*') if p.is_file()}
    plan = {**{k: old[k] for k in ['provider', 'endpoint', 'expected_returned_model',
                                 'schema_sha256', 'outbound_exclusions']},
            **runtime, 'schema': 'diagnosis-no-repeat-and-unlocalized-two',
            'prepared_at': b.now(), 'jobs': jobs, 'concurrency': 2,
            'runner_sha256': b.sha(Path(__file__)),
            'guarded_method_files': {str(p.relative_to(BASE)): b.sha(p) for p in paths},
            'acceptance_standard': POLICY_ID,
            'output_repairs': 0, 'review_model_calls': 0,
            'fresh_start': True, 'historical_responses_reused': 0,
            'comparison_note': 'Two full fresh reruns of v1/24 non-submitters. Candidate combines duplicate-read avoidance and explicit inability explanation; no single-factor attribution. Other six are not rerun.'}
    b.save(ROOT / 'plan.json', plan)
    b.save(ROOT / 'historical_hashes.json', historical)
    b.save(ROOT / 'current_runtime.json', runtime)
    (ROOT / 'method').mkdir()
    for p in [Path(__file__), b.CONTRACT / 'diagnosis.prompt.md',
              BASE / 'pipeline/stages/diagnosis/validation.py']:
        shutil.copy2(p, ROOT / 'method' / p.name)
    b.save(ROOT / 'offline_checks.json', {
        'two_exact_non_submitting_cases': [j['task_id'] for j in jobs],
        'initial_packets_byte_identical_to_original': True,
        'candidate_prompt_exact': b.sha(Path(runtime['prompt_path'])) == candidate_manifest['candidate_sha256'],
        'original_v1_baseline_unchanged': b.sha(OLD / 'method/diagnosis.prompt.md') == old['prompt_sha256'],
        'max_model_rounds': 24, 'force_final': False,
        'official_outcome_not_used_in_packet': True,
        'processor_files_verified': b.verify_processor()})
    verify()
    print(b.encode({'prepared': len(jobs), 'fresh_start': True, 'max_rounds': 24}), flush=True)


def summarize():
    plan = verify()
    rows, usage = [], {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    request_count = response_count = service_failures = 0
    for job in plan['jobs']:
        folder = ROOT / 'results' / job['record_id']
        row = {**job, **b.read(folder / 'result.json')}
        row['has_final_json'] = (folder / 'diagnosis.json').exists()
        row['accepted'] = False
        if (folder / 'mechanical_validation.json').exists():
            row['acceptance'] = b.read(folder / 'mechanical_validation.json')
            row['accepted'] = row['acceptance']['passed']
        if row['has_final_json']:
            data = b.read(folder / 'diagnosis.json')
            trace = b.read(b.BUNDLE / 'traces' / (job['record_id'] + '.json'))
            audit = reference_audit(data, trace, folder, folder)
            b.save(folder / 'reference_audit.json', audit)
            row['reference_errors'] = len(audit['errors'])
        receipts = list((folder / 'calls').glob('*/receipt.json'))
        request_count += len(list((folder / 'calls').glob('*/request.json')))
        response_count += len(list((folder / 'calls').glob('*/response.json')))
        for p in receipts:
            receipt = b.read(p)
            service_failures += int(receipt.get('status', '').endswith('failure'))
            for k in usage:
                usage[k] += (receipt.get('usage') or {}).get(k, 0) or 0
        rows.append(row)
    historical = b.read(ROOT / 'historical_hashes.json')
    assert all(b.sha(BASE / p) == h for p, h in historical.items()), 'Historical experiment changed'
    result = {'finished_at': b.now(), 'traces': rows,
              'accepted': sum(r['accepted'] for r in rows), 'total': 2,
              'new_requests': request_count, 'new_responses': response_count,
              'service_failures': service_failures, 'usage': usage,
              'prompt_version': plan['prompt_version'], 'max_model_rounds': 24,
              'acceptance_standard': POLICY_ID,
              'historical_files_unchanged': len(historical),
              'processor_files_unchanged': b.verify_processor(),
              'official_evaluations_sent': False, 'benchmark_reexecutions': 0,
              'automatic_retries': 0, 'output_repairs': 0,
              'semantic_accuracy_measured': False}
    b.save(ROOT / 'summary.json', result)
    lines = ['限制重复取证并允许说明无法定位根因：两条重跑', '',
             '两条均从第1轮重新运行；使用最新候选prompt，输入、模型参数和工具保持v1对照配置。最多24轮、60次取证，无强制最终轮。',
             '仅重跑Claude Code agriculture_16和Kimi DABstep 2697；其余六条不重跑。此前两条均到24轮未提交JSON。',
             '验收沿用CodeTracer detailed兼容标准，忽略额外字段。引用检查单列，不代表语义正确性。',
             '相对原始v1同时包含两项prompt改动，本轮不能单独归因于其中一项。', '',
             '|轨迹|状态|已收到模型响应轮数|取证次数|提交JSON|验收|',
             '|---|---|---:|---:|---|---|']
    for r in rows:
        lines.append(f"|{r['agent_id']} / {r['task_id']}|{r['status']}|{r['model_rounds']}|{r['tool_calls']}|{r['has_final_json']}|{r['accepted']}|")
    lines += ['', f'新请求{request_count}次，收到响应{response_count}次；服务失败{service_failures}次。',
              '官方评分、标准答案、评估反馈及运行后快照没有提供给模型；没有执行原分析代码。',
              f'旧实验{len(historical)}个文件、冻结Processor {result["processor_files_unchanged"]}个文件保持不变。',
              '原生输出、逐轮请求响应和取证记录保存在results目录。']
    (ROOT / 'report.txt').write_text('\n'.join(lines) + '\n')
    manifest = {str(p.relative_to(ROOT)): b.sha(p) for p in ROOT.rglob('*')
                if p.is_file() and p.name not in ['manifest.json', 'bundle.zip', 'worker.lock']}
    b.save(ROOT / 'manifest.json', {'files': manifest})
    with zipfile.ZipFile(ROOT / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in manifest:
            z.write(ROOT / rel, rel)
        z.write(ROOT / 'manifest.json', 'manifest.json')
    print(b.encode({'accepted': result['accepted'], 'total': 2,
                    'new_requests': request_count, 'new_responses': response_count,
                    'service_failures': service_failures}), flush=True)


def run():
    verify()
    lock = (ROOT / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    b.verify_plan = verify
    b.progress = progress
    b.validate_diagnosis = lambda data, safe: validate_diagnosis(data, safe.index.steps[safe.rid])
    plan = verify()
    index, client = b.EvidenceIndex(b.BUNDLE), b.NativeClient()
    states = {j['record_id']: {'task_id': j['task_id'], 'status': 'pending'} for j in plan['jobs']}
    assert not (ROOT / 'results').exists(), 'Fresh rerun already started; do not resubmit'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(b.run_one, index, client, j, states) for j in plan['jobs']]
        for f in as_completed(futures):
            f.result()
    progress(states, 'finished')
    summarize()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--run', action='store_true')
    p.add_argument('--summarize', action='store_true')
    a = p.parse_args()
    if a.prepare:
        prepare()
    elif a.run:
        run()
    elif a.summarize:
        summarize()
