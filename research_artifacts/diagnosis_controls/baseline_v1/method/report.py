"""Post-run local audit and deliverable packaging; no model calls or repairs."""
import hashlib
import html
import json
from pathlib import Path
import shutil
import zipfile

BASE = Path(__file__).resolve().parents[2]
ROOT = BASE / 'outputs/diagnosis_eight_native_tools_20261018'
PRIOR = BASE / 'outputs/diagnosis_eight_validation_20261018'
BUNDLE = BASE / 'outputs/stage_d_ten_20261018'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def visit(value):
    if isinstance(value, dict):
        if value.get('status') == 'source_excerpt' and 'text' in value:
            yield value
        for v in value.values():
            yield from visit(v)
    elif isinstance(value, list):
        for v in value:
            yield from visit(v)


def main():
    assert read(ROOT / 'progress.json')['status'] in ['finished', 'stopped']
    plan = read(ROOT / 'plan.json')
    cases = []
    source_checks = []
    receipts = []
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    for job in plan['jobs']:
        rid = job['record_id']
        directory = ROOT / 'results' / rid
        result = read(directory / 'result.json') if (directory / 'result.json').exists() else {'status': 'not_run'}
        trace = read(BUNDLE / 'traces' / (rid + '.json'))
        assert sha(BUNDLE / 'traces' / (rid + '.json')) == job['trace_sha256']
        catalog = {e['evidence_id']: e for e in trace['evidence_catalog']}
        inputs = read(ROOT / 'inputs' / (rid + '.json'))
        pages = list(visit(inputs))
        tool_errors = []
        for path in sorted((directory / 'tool_reads').glob('*.json')):
            tool = read(path)
            pages.extend(visit(tool['result']))
            if tool['result'].get('status') in ['invalid_tool_arguments', 'unknown_tool', 'invalid_range', 'evidence_not_found']:
                tool_errors.append({'ordinal': tool['ordinal'], 'status': tool['result'].get('status')})
        checked = 0
        exchange_pages = 0
        for page in pages:
            eid = page['evidence_id']
            if eid in catalog:
                original = catalog[eid]['text']
            elif eid == 'FINAL0001':
                original = trace['facts']['final_response']['text']
            else:
                # Exchange pages were resolved from the immutable source registry by the runtime;
                # compare their source pointers against the separately retained raw source there.
                from pipeline.stages.trace_processor.merge_layer.build import bundled_sources
                raw = bundled_sources(trace, BUNDLE).resolve(page['original_source_ref'])
                original = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, separators=(',', ':'))
                exchange_pages += 1
            assert page['text'] == original[page['block_char_start']:page['block_char_end']], (rid, eid)
            checked += 1
        for path in sorted((directory / 'calls').glob('*/receipt.json')):
            receipt = read(path)
            receipts.append({'record_id': rid, 'round': path.parent.name, **receipt})
            if receipt.get('returned_model') is not None:
                assert receipt['returned_model'] == plan['expected_returned_model']
            for key in usage:
                usage[key] += (receipt.get('usage') or {}).get(key, 0) or 0
            request = read(path.parent / 'request.json')
            assert request['messages'][0]['content'] == (ROOT / 'method/diagnosis.prompt.md').read_text()
            assert 'response_format' not in request
        validation = read(directory / 'mechanical_validation.json') if (directory / 'mechanical_validation.json').exists() else None
        diagnosis = read(directory / 'diagnosis.json') if (directory / 'diagnosis.json').exists() else None
        if diagnosis is not None:
            readable_path = ROOT / 'diagnoses' / (job['agent_id'] + '__' + job['task_id'] + '.json')
            readable_path.parent.mkdir(exist_ok=True)
            shutil.copy2(directory / 'diagnosis.json', readable_path)
        cases.append({**job, **result, 'mechanical_validation': validation,
                      'critical_points': len(diagnosis['critical_decision_points']) if diagnosis and 'critical_decision_points' in diagnosis else None,
                      'chain_entries': len(diagnosis['root_cause_chain']) if diagnosis and 'root_cause_chain' in diagnosis else None,
                      'tool_argument_errors': tool_errors})
        source_checks.append({'record_id': rid, 'unchanged_source_trace': True,
                              'original_source_pages_verified': checked, 'exchange_pages_verified': exchange_pages,
                              'all_exact_source_slices': True})
    save(ROOT / 'source_page_audit.json', source_checks)
    review = read(ROOT / 'semantic_review.json')
    assert {r['record_id'] for r in review['cases']} == {j['record_id'] for j in plan['jobs']}
    first = read(PRIOR / 'summary.json')
    probe = read(PRIOR / 'protocol_probe_native_tools/result.json')
    save(ROOT / 'transport_comparison.json', {
        'first_run': {'model_calls': first['model_calls'], 'native_tool_calls': first['tool_calls'],
                      'mechanically_passed': first['mechanically_passed'], 'usage': first['usage']},
        'controlled_probe': probe,
        'prompt_hash_identical': plan['prompt_sha256'] == read(PRIOR / 'plan.json')['prompt_sha256'],
        'input_hashes_identical': [j['input_sha256'] for j in plan['jobs']] == [j['input_sha256'] for j in read(PRIOR / 'plan.json')['jobs']],
        'causal_scope': 'One trace establishes JSON mode/tool-call incompatibility for this exact setup; not a universal statement about all SoCLaaS models or JSON mode.',
        'first_probe_response_reused_not_resubmitted': True})
    passed = sum(c['mechanical_validation'] is not None and c['mechanical_validation']['passed'] for c in cases)
    generated = sum((ROOT / 'results' / c['record_id'] / 'diagnosis.json').exists() for c in cases)
    native_requests = len(receipts)
    native_responses = sum(r.get('returned_model') is not None for r in receipts)
    reused = sum('reused_probe' in r for r in receipts)
    explicit_replays = sum(r.get('explicit_transport_replay', False) for r in receipts)
    summary = {'traces': cases, 'final_diagnosis_json_generated': generated, 'mechanically_passed': passed,
               'provider_requests_represented': native_requests,
               'model_responses_used': native_responses, 'new_requests_after_probe': native_requests - reused,
               'probe_responses_reused': reused,
               'explicit_transport_replays_of_unreturned_requests': explicit_replays,
               'native_tool_calls': sum(c.get('tool_calls', 0) for c in cases),
               'usage_transport_corrected_run_including_reused_probe': usage,
               'usage_first_failed_run': first['usage'],
               'total_distinct_provider_requests_including_failed_first_run': native_requests + first['model_calls'] + (0 if reused else 1),
               'interrupted_provider_requests_without_usage': sum(r.get('returned_model') is None for r in receipts),
               'source_pages_verified': sum(c['original_source_pages_verified'] for c in source_checks),
               'semantic_review_counts': {status: sum(c['status'] == status for c in review['cases'])
                                           for status in sorted({c['status'] for c in review['cases']})},
               'independent_gold': False, 'root_cause_accuracy_measured': False,
               'official_scores_not_sent': True, 'post_run_snapshots_not_sent': True,
               'prompt_unchanged': True, 'automatic_retries': 0, 'repairs': 0,
               'frozen_processor_files_unchanged': len(read(BASE / 'outputs/trace_processor_frozen_v1_20261018/frozen_manifest.json')['files'])}
    for path, digest in read(BASE / 'outputs/trace_processor_frozen_v1_20261018/frozen_manifest.json')['files'].items():
        assert sha(BASE / path) == digest
    save(ROOT / 'summary.json', summary)
    first_archive = ROOT / 'first_run_json_mode'
    for name in ['plan.json', 'summary.json', 'progress.json']:
        first_archive.mkdir(exist_ok=True)
        shutil.copy2(PRIOR / name, first_archive / name)
    shutil.copytree(PRIOR / 'results', first_archive / 'results', dirs_exist_ok=True)
    shutil.copytree(PRIOR / 'protocol_probe_native_tools', first_archive / 'controlled_probe', dirs_exist_ok=True)
    lines = [
        'Data Agent诊断 · 八条非示例轨迹验证', '',
        f'最终诊断JSON：{generated}/8；其中{passed}/{generated}通过严格合同检查。',
        f'机械验收：{passed}/8；这是格式、ID和实际读取检查，不是根因准确率。',
        f'原生取证调用：{summary["native_tool_calls"]}次；实际使用模型响应：{native_responses}次（含{reused}次复用核查）。',
        f'工具模式请求记录：{native_requests}次；其中连接中断等未取得模型响应：{summary["interrupted_provider_requests_without_usage"]}次。',
        f'连接中断后的显式续跑：{explicit_replays}次；未重生成已完成诊断，原中断记录完整保留。',
        '使用原版五字段prompt和两个示例，未更改诊断规则或修复任何模型输出。', '',
        f'{summary["source_pages_verified"]}次原文页核对全部与原来源一致；实际页数以source_page_audit.json为准。',
        '原文保真和引用ID合法，不等于模型对原文的解释或因果归因正确。', '',
        '接口修正与首轮保留：',
        '首轮开启JSON mode，8条均无有效诊断且无原生工具调用。',
        '同一条轨迹仅取消response_format，原生取证调用即恢复；随后用相同prompt和输入运行八条。',
        '本现象只在本次x-test-2设置得到验证，不推广到所有模型/接口。',
        '首轮原始请求/响应和单因素核查保存在first_run_json_mode/，未删除或计为成功。', '',
        '资源记录：',
        f'工具模式累计已返回usage：{usage["total_tokens"]:,} tokens，其中输入{usage["prompt_tokens"]:,}、输出{usage["completion_tokens"]:,}。',
        f'首轮JSON-mode失败试验另计{first["usage"]["total_tokens"]:,} tokens；{summary["interrupted_provider_requests_without_usage"]}次断连请求未取得usage，不能估算为0。',
        '一次轨迹诊断包含多次按需取证后的模型调用，不能把本试验说成只有8次LLM调用。', '',
        '逐条结果：', '', '|任务|机械验收|模型响应/取证调用|本地语义核查|', '|---|---|---|---|']
    for case in cases:
        rev = next(r for r in review['cases'] if r['record_id'] == case['record_id'])
        lines.append(f"|{case['task_id']}|{case['status']}|{case.get('model_rounds',0)}/{case.get('tool_calls',0)}|{rev['status']}：{rev['conclusion']}|")
    lines += ['', '语义核查细节：', '']
    for rev in review['cases']:
        lines.append(rev['task_id'] + '：' + rev['conclusion'])
        for point in rev['notes']:
            lines.append('- ' + point)
        lines.append('')
    lines += ['限制：',
              '两条prompt示例已排除；这八条此前参加过取证试验，因此并非严格从未接触的独立测试集。',
              '语义结论是本地作者逐项核查，不是独立双人标注；没有根因金标准，不报告根因准确率/F1。',
              '官方评估只能反映任务结果，不能单独证实具体根因。本轮未发送评分、标准答案或运行后快照。',
              '按需读取不保证上下文完整；空结果、缺失记录与未读取数据必须区分。', '',
              '关键文件：',
              'results/<record_id>/diagnosis.json：模型原生诊断结果，未修复。',
              'diagnoses/：相同原生JSON的易读文件名副本，不改变内容。',
              'results/<record_id>/tool_reads/：每次只读取证及实际返回页。',
              'results/<record_id>/mechanical_validation.json：合同与引用检查。',
              'semantic_review.json：本地语义核查，保留不确定性。',
              'source_page_audit.json：实际取证页与原文逐字比较。',
              'plan.json、method/、transport_comparison.json：冻结设置及接口修正记录。',
              'explicit_recovery_protocol.json：若存在，记录对未取得响应请求的显式续跑，不把它伪称为零次重发。',
              '本包保留实际发送的请求与原生响应；完整原始模型I/O仍需原阶段D来源包。', '']
    report = '\n'.join(lines)
    (ROOT / 'report.txt').write_text(report)
    (ROOT / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>八条诊断验证</title><style>body{font:16px/1.7 system-ui;margin:40px;max-width:1200px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>' + html.escape(report) + '</pre>')
    shutil.copy2(Path(__file__), ROOT / 'method/report.py')
    recovery_script = Path(__file__).with_name('recover_interrupted.py')
    if recovery_script.exists():
        shutil.copy2(recovery_script, ROOT / 'method/recover_interrupted.py')
    save(ROOT / 'manifest.json', {'files': {str(p.relative_to(ROOT)): sha(p) for p in sorted(ROOT.rglob('*'))
                                          if p.is_file() and p.name not in ['manifest.json', 'bundle.zip', 'worker.lock']}})
    with zipfile.ZipFile(ROOT / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob('*')):
            if p.is_file() and p.name not in ['bundle.zip', 'worker.lock']:
                z.write(p, str(p.relative_to(ROOT)))
    with zipfile.ZipFile(ROOT / 'bundle.zip') as z:
        assert z.testzip() is None
    print(json.dumps({k: v for k, v in summary.items() if k != 'traces'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(BASE))
    main()
