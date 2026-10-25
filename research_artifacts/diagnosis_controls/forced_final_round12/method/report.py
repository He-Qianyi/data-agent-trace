"""Report the paired final-round intervention; preserve every native output."""
import html
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

spec = importlib.util.spec_from_file_location('control', Path(__file__).with_name('run.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    plan = m.read(m.ROOT / 'plan.json')
    assert m.read(m.ROOT / 'progress.json')['experiment_status'] == 'finished'
    source_review = m.read(m.OLD / 'semantic_review.json')
    comparisons, regressions = [], []
    for job in plan['jobs']:
        rid = job['record_id']
        folder = m.ROOT / 'results' / rid
        old = m.OLD / 'results' / rid
        result = m.read(folder / 'result.json')
        before_json = (old / 'diagnosis.json').exists()
        after_json = (folder / 'diagnosis.json').exists()
        before_valid = m.read(old / 'mechanical_validation.json')['passed'] if (old / 'mechanical_validation.json').exists() else False
        after_valid = m.read(folder / 'mechanical_validation.json')['passed'] if (folder / 'mechanical_validation.json').exists() else False
        comparisons.append({'record_id': rid, 'task_id': job['task_id'], 'treatment': job['treatment'],
                            'baseline_status': job['baseline_status'], 'new_status': result['status'],
                            'before_final_json': before_json, 'after_final_json': after_json,
                            'before_strict_pass': before_valid, 'after_strict_pass': after_valid})
        if job['treatment'] == 'unchanged_early_terminal_reuse':
            assert m.sha(folder / 'diagnosis.json') == m.sha(old / 'diagnosis.json')
            original_review = next(r for r in source_review['cases'] if r['record_id'] == rid)
            regressions.append({'record_id': rid, 'task_id': job['task_id'], 'diagnosis_byte_identical': True,
                                'sha256': m.sha(folder / 'diagnosis.json'),
                                'prior_author_review_status_unchanged': original_review['status'],
                                'new_independent_model_output_generated': False})
    m.save(m.ROOT / 'comparison.json', comparisons)
    m.save(m.ROOT / 'early_terminal_regression_checks.json', regressions)
    affected = next(j for j in plan['jobs'] if j['treatment'] == 'paired_final_round_tool_choice_none')
    directory = m.ROOT / 'results' / affected['record_id']
    receipt = m.read(directory / 'call_round12/receipt.json')
    result = m.read(directory / 'result.json')
    # Source-dependent local checks, not a new model review or hidden-reference comparison.
    review = {
        'reviewer': 'local_source_checked_author_review', 'independent_gold': False,
        'official_scores_or_answers_consulted': False, 'new_review_model_calls': 0,
        'record_id': affected['record_id'], 'status': 'needs_revision',
        'within_budget_final_json': (directory / 'diagnosis.json').exists(),
        'new_tools_executed_at_deadline': result['tools_executed_in_round12'],
        'new_output_conclusion': '已按预算交卷，但存在额外字段、统计学错误和未经支持的归因，不能视为安全可靠的最终诊断。',
        'supported_observations': [
            {'claim': 'S0034完成Ridge并输出R²=0.406782及11个系数，12行metric/value结果；S0033的NameError已恢复。',
             'evidence_ids': ['E00101', 'E00102', 'E00104', 'E00105']},
            {'claim': '最终脚本只用精确名称交集；S0033映射在精确交集筛选后才应用，S0034不再含crop_map。',
             'evidence_ids': ['E00101', 'E00102', 'E00104', 'E00105']}
        ],
        'issues': [
            {'kind': 'output_contract', 'pointer': '/root_cause_chain_note',
             'problem': '原生输出额外包含root_cause_chain_note:null，违反五字段合同；未删除或修复。'},
            {'kind': 'unsupported_failure_attribution', 'pointer': '/root_cause_chain',
             'problem': '模型自己承认作物名标准化未被任务明确规定、不能证实任务违规，仍将该偏差组织成失败链。可保留实际筛选/映射观察，但不能据此认证最终失败根因。'},
            {'kind': 'statistical_error', 'pointer': '/critical_decision_points/1/reasoning',
             'problem': '从全部收入组哑变量与截距线性相关直接推出Ridge(alpha=1.0)系数解不唯一，是把未正则化OLS的结论套入Ridge。正alpha的L2项使系数目标严格凸；不存在其所声称的解不唯一。',
             'basis': '原代码是Ridge(alpha=1.0)。依官方目标函数，任意非零v都有v^T(X^TX+alpha I)v=||Xv||²+alpha||v||²>0。中心化后的系数解唯一；非空样本下截距也可确定。此处是由目标函数作出的数学推导，不是重跑原数据。',
             'primary_source': 'https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html'},
            {'kind': 'unsupported_counterfactual', 'pointer': '/critical_decision_points/1/reasoning',
             'problem': 'R²不受影响没有反事实执行或理论保证；删除哑变量会改变正则化参数化，不能保证预测或R²保持不变。'},
            {'kind': 'unsupported_driver', 'pointer': '/critical_decision_points/2/reasoning',
             'problem': '0.8411、0.9734是总体解释方差比例，不能单独证明主成分主要由季节温度/降水驱动。实际已读记录没有给出支持该归因的载荷或按列方差分析。'},
            {'kind': 'invented_fixed_output_size', 'pointer': '/correct_strategy',
             'problem': '建议删掉一个收入组或改变PCA列口径后，仍要求核对12行。原任务没有固定12行；行数依最终系数数量决定，不能把本次实际行数变成通用要求。'},
            {'kind': 'unsupported_impact_size', 'pointer': '/summary',
             'problem': '丢弃21行的数量有据，但影响很小未经敏感性或偏差分析支持。较小比例不直接证明估计影响小。'}
        ],
        'causal_inference_limit': '对照组第12轮没有最终诊断，因此不能证明这些语义问题由强制提交本身引起；只能说该输出存在这些问题，终止控制未保证语义可靠。',
        'old_completed_quality_limit': '五条早期完成结果逐字节复用，说明本策略未改变这些已记录输出；不是五次独立重跑得到的质量稳定性证据。'
    }
    m.save(m.ROOT / 'semantic_review.json', review)
    old_non_service = [j for j in plan['jobs'] if j['treatment'] != 'transport_censored_before_intervention']
    after_json_count = sum(c['after_final_json'] for c in comparisons)
    strict_count = sum(c['after_strict_pass'] for c in comparisons)
    summary = {
        'comparison_design': plan['comparison_design'], 'single_changed_factor': plan['single_changed_factor'],
        'cohort': 8, 'early_completed_outputs_reused': len(regressions),
        'paired_intervention_cases': 1, 'new_provider_requests': 1, 'new_provider_responses': int(receipt.get('returned_model') is not None),
        'service_censored_before_round12': 2, 'new_connection_interruptions': 0 if receipt.get('returned_model') else 1,
        'before_final_json_count': sum(c['before_final_json'] for c in comparisons), 'after_final_json_count': after_json_count,
        'before_strict_pass_count': sum(c['before_strict_pass'] for c in comparisons), 'after_strict_pass_count': strict_count,
        'non_service_comparable_cases': len(old_non_service),
        'affected_case': {'task_id': affected['task_id'], 'before': affected['baseline_status'], 'after': result['status'],
                          'completed_in_round': 12, 'tools_executed_in_round12': result['tools_executed_in_round12'],
                          'original_round12_tool_calls': 2, 'prefix_tool_calls_reused': result['tool_calls_before_round12'],
                          'semantic_author_review': review['status']},
        'previously_completed_outputs_byte_identical': all(r['diagnosis_byte_identical'] for r in regressions),
        'independent_repeated_run_quality_estimate': False, 'safe_diagnosis_proven': False,
        'usage_new_request_only': receipt.get('usage'),
        'prompt_unchanged': True, 'new_prompt_rules_or_examples': 0, 'new_tool_reads': 0,
        'automatic_retries': 0, 'output_repairs': 0, 'review_model_calls': 0,
        'frozen_processor_files_unchanged': m.verify()
    }
    m.save(m.ROOT / 'summary.json', summary)
    lines = [
        '第一轮：只改诊断运行终止控制，prompt v1不变', '',
        '结论：本例能在第12轮交卷；严格格式验收及安全诊断仍未通过。', '',
        '比较设计：',
        '沿用相同八条非示例轨迹，复用完全相同的历史分支；不是八条重新独立采样。',
        '五条原来在4–5轮结束，逐字节复用；两条此前连接中断，单列为无法评估终止控制。',
        '只有agriculture_16原来走到第12轮，因此仅新增一次请求。',
        '该请求复用前11轮历史与24次取证结果，只将tool_choice从auto改为none。',
        'system/user消息、prompt、示例、工具定义、模型、温度、输出上限、thinking预算全部不变。',
        '没有添加最后一轮提醒语，也没有把原第12轮的两次取证结果泄露给新分支。', '',
        '三项检查：',
        '1. 预算内提交：agriculture_16从12轮后仍取证，变为第12轮返回完整JSON；无第13轮、无第12轮新取证。',
        '2. 是否强行构造根因：新输出确有未经支持的失败归因及统计学错误，因此未通过安全诊断核查。',
        '3. 已完成结果是否下降：五条结果哈希全部相同，既有质量和问题未改变；并非独立重跑的稳定性估计。', '',
        '|指标|原分支|终止控制分支|', '|---|---:|---:|',
        f'|最终JSON（八条含服务中断）|{summary["before_final_json_count"]}/8|{summary["after_final_json_count"]}/8|',
        f'|最终JSON（六条非服务中断样本）|{summary["before_final_json_count"]}/6|{summary["after_final_json_count"]}/6|',
        f'|严格合同与引用验收|{summary["before_strict_pass_count"]}/8|{summary["after_strict_pass_count"]}/8|',
        '|此前连接中断且未到第12轮|2|2（原样单列）|', '',
        '新增JSON的格式与引用：',
        '原生输出多出root_cause_chain_note:null，未做删除或格式修复。',
        '严格validator在额外字段处失败；另行只读核对18处引用，均为存在且第12轮前已读的证据。',
        '引用位置合法并不能证明其支持诊断结论。', '',
        '新增JSON的主要语义问题：',
        '- 承认作物映射问题不是已证实任务违规，却仍将它组织成失败链。',
        '- 从全部收入组哑变量推断Ridge(alpha=1.0)系数解不唯一，统计判断错误。',
        '- 没有加载/载荷证据却声称PCA主要由季节天气驱动；没有验证却说删行影响很小。',
        '- 建议更改特征集合后仍固定要求12行输出，把当前结果大小变成了任务条件。', '',
        'Ridge判断的核查依据：官方定义为平方误差加alpha乘L2系数惩罚；当alpha>0，',
        'X^TX+alpha I为正定矩阵，因此这里不能套用OLS的系数解不唯一结论。',
        '[官方Ridge目标函数](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html)',
        '这是根据目标函数的数学推导；未重新执行原分析或读取官方答案。', '',
        '限制：',
        '只有一个实际到达终止分支的样本，不能据此声称通用完成率100%。',
        '旧分支没有最终诊断，不能断言语义问题由强制交卷造成；能确定的是当前输出仍不可靠。',
        '两条服务中断没有因本轮修改而恢复，也未计作终止控制成功或失败。',
        '终止控制可作为运行保护保留，不能据此认定诊断阶段已通过；本轮不修改下一项语义规则。', '',
        f'新增请求耗时：{receipt["elapsed_seconds"]}秒；usage：{json.dumps(receipt.get("usage"), ensure_ascii=False)}', '',
        '关键文件：',
        'results/rawio-bcb49010f78ccd4af446/diagnosis.json：原生最终JSON，未修复。',
        'results/.../control_request.json 与 treatment_request.json：单因素对照请求。',
        'results/.../prefix_check.json：历史相同、仅tool_choice改变、排除第12轮证据的检查。',
        'results/.../independent_reference_audit.json：18处引用单独核对。',
        'early_terminal_regression_checks.json：五条已完成结果的字节级回归检查。',
        'semantic_review.json：本地作者逐项核查，不是独立金标准。',
        'comparison.json、summary.json、tests.json：分项比较与离线测试。',
        '完整原始来源对象仍需之前的阶段D包；本包保留实际对照请求与响应及原文来源位置。', '']
    report = '\n'.join(lines)
    (m.ROOT / 'report.txt').write_text(report)
    source_link = '<a href="https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html">官方Ridge目标函数</a>'
    body = html.escape(report).replace(html.escape('[官方Ridge目标函数](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html)'), source_link)
    (m.ROOT / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>终止控制单因素验证</title><style>body{font:16px/1.7 system-ui;margin:40px;max-width:1100px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>' + body + '</pre>')
    for p in [Path(__file__), Path(__file__).with_name('test_control.py')]:
        shutil.copy2(p, m.ROOT / 'method' / p.name)
    excluded = {m.ROOT / 'manifest.json', m.ROOT / 'bundle.zip'}
    m.save(m.ROOT / 'manifest.json', {'files': {str(p.relative_to(m.ROOT)): m.sha(p) for p in sorted(m.ROOT.rglob('*')) if p.is_file() and p not in excluded}})
    with zipfile.ZipFile(m.ROOT / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(m.ROOT.rglob('*')):
            if p.is_file() and p != m.ROOT / 'bundle.zip':
                archive.write(p, str(p.relative_to(m.ROOT)))
    with zipfile.ZipFile(m.ROOT / 'bundle.zip') as archive:
        assert archive.testzip() is None
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
