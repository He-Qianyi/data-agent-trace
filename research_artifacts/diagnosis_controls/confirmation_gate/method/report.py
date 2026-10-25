import hashlib,html,importlib.util,json,shutil,zipfile
from pathlib import Path
WORK=Path(__file__).parent
s=importlib.util.spec_from_file_location('gate',WORK/'run.py');b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
ROOT=b.ROOT
progress=b.read(ROOT/'progress.json')
assert progress['status'] in ['finished','stopped'] and progress['finished_traces']==8,'not_all_eight_finished'
review=b.read(WORK/'review.json')
assert not any(c['status']=='pending_review' for c in review['cases'])
plan=b.verify_plan();audit=b.read(ROOT/'protocol_audit.json')
assert len(audit['rows'])==8
assert all(b.sha(b.BASE/p)==h for p,h in plan['preserved_control_hashes'].items())
old=b.BASE/'outputs/diagnosis_termination_control_v1_20261018'
comparison=[]
for j in plan['jobs']:
 rid=j['record_id'];before=b.read(old/'results'/rid/'result.json');after=b.read(ROOT/'results'/rid/'result.json')
 before_final=(old/'results'/rid/'diagnosis.json').exists()
 comparison.append({'task_id':j['task_id'],'record_id':rid,'before_status':before['status'],'after_status':after['status'],
   'before_final_json':before_final,'after_final_json':(ROOT/'results'/rid/'diagnosis.json').exists(),
   'before_strict_pass':before['status']=='mechanically_passed','after_strict_pass':after['status']=='mechanically_passed',
   'paired_semantic_comparison_available':before_final and (ROOT/'results'/rid/'diagnosis.json').exists()})
b.save(ROOT/'comparison.json',comparison)
b.save(ROOT/'semantic_review.json',review)
paired=[v for v in comparison if v['paired_semantic_comparison_available']]
controls=[v for v in comparison if v['before_final_json']]
summary={
 'single_changed_factor':plan['single_changed_factor'],'termination_control_unchanged':True,
 'same_eight_initial_inputs':True,'fresh_treatment_runs':8,'historical_completed_controls':len(controls),'paired_final_outputs_available':len(paired),
 'baseline_transport_censored':sum(not v['before_final_json'] for v in comparison),
 'new_provider_requests':sum(v['requests'] for v in audit['rows']),
 'new_provider_responses':sum(v['responses'] for v in audit['rows']),
 'new_service_failures':sum(len(v['transport_failures']) for v in audit['rows']),
 'new_connection_interruptions':sum(len([e for e in v['transport_failures'] if e['receipt'].get('http_status') is None]) for v in audit['rows']),
 'new_http_failures':sum(len([e for e in v['transport_failures'] if e['receipt'].get('http_status') is not None]) for v in audit['rows']),
 'before_final_json':sum(v['before_final_json'] for v in comparison),'after_final_json':sum(v['after_final_json'] for v in comparison),
 'before_strict_pass':sum(v['before_strict_pass'] for v in comparison),'after_strict_pass':sum(v['after_strict_pass'] for v in comparison),
 'paired_before_strict_pass':sum(v['before_strict_pass'] for v in paired),'paired_after_strict_pass':sum(v['after_strict_pass'] for v in paired),
 'fixed_control_cohort_before_strict_pass':sum(v['before_strict_pass'] for v in controls),
 'fixed_control_cohort_after_strict_pass':sum(v['after_strict_pass'] for v in controls),
 'fixed_control_cohort_before_json':sum(v['before_final_json'] for v in controls),
 'fixed_control_cohort_after_json':sum(v['after_final_json'] for v in controls),
 'terminal_protocol_failures':1,
 'max_model_rounds_observed':max(v['model_rounds'] for v in audit['rows']),
 'citation_instances':sum(v['citation_instances'] for v in audit['rows']),
 'citation_errors':sum(v['reference_errors'] for v in audit['rows']),
 'source_pages_verified':sum(v['verified_source_pages'] for v in audit['rows']),
 'usage_known_responses':{k:sum(v['usage'][k] for v in audit['rows']) for k in ['prompt_tokens','completion_tokens','total_tokens']},
 'automatic_retries':0,'output_repairs':0,'review_model_calls':0,
 'independent_gold':False,'false_positive_rate_measured':False,
 'controlled_causal_improvement_proven':False,'frozen_processor_files_unchanged':b.verify_processor(),
 'conclusion':'部分旧断言有所收敛，但尚不能证明总体误报减少或整体质量提升；agriculture_16终局返回工具标记文本而非JSON，不能进行语义对照。'}
b.save(ROOT/'experiment_summary.json',summary)
lines=['第二轮：仅增加“已确认问题”判定门槛','',summary['conclusion'],'',
 '改动：在v1的Instruction与Step之间仅插入一个通用段落。原文要求/定义与实际行为必须有原始证据支持的差异，才能认定问题；未固定口径、证据不足或仅缺少验证保留为总结中的限制。',
 '没有增加具体数据集/错误示例；原五字段JSON合同、原示例、原因果规则不变。第12轮仍tool_choice=none，最多12轮/60次取证。',
 '仍使用SoCLaaS x-test-2，实际版本deepseek-v4.1-flash-1017；max_tokens=48000、thinking_token_budget=16000、temperature=0、并发2，无JSON mode。','',
 '比较设计：八条处理组从头运行；六条历史上有最终诊断，但本轮agriculture_16没有JSON，因此只有五条具备双侧最终输出。两条历史连接中断没有旧最终JSON，其本轮完成不计入误报改善。取证路径可随prompt改变，不是相同证据的终局重判；这是一次非盲作者复核，不能据此估计根因准确率或证明稳定因果效果。','',
 '|检查|第一轮对照|第二轮|','|---|---:|---:|',
 f"|最终JSON（八条）|{summary['before_final_json']}/8|{summary['after_final_json']}/8|",
 f"|严格合同与引用验收（八条）|{summary['before_strict_pass']}/8|{summary['after_strict_pass']}/8|",
 f"|最终JSON（固定六条历史完成样本）|{summary['fixed_control_cohort_before_json']}/6|{summary['fixed_control_cohort_after_json']}/6|",
 f"|严格验收（固定六条历史完成样本）|{summary['fixed_control_cohort_before_strict_pass']}/6|{summary['fixed_control_cohort_after_strict_pass']}/6|",'',
 '总体严格通过从4增至5包含两条历史连接中断样本本轮完成，不能据此判断prompt质量提升。对固定六条旧完成样本，严格通过反而从4降至3：agriculture_22格式通过，但civic与stock新增额外字段失败；agriculture_16仍未严格通过，本轮连最终JSON也未提交。','',
 '逐项语义复核（不把旧结果已写明的疑点计为误报）：','']
for c in review['cases']:
 lines += [c['task_id']+'：'+c['status']]
 for finding in c.get('threshold_comparison',[]):
  lines.append('- '+finding['topic']+'：'+finding['assessment'])
 for key in ['findings','remaining_limits','remaining_causal_overreach']:
  for note in c.get(key,[]):lines.append('- '+note)
 if c.get('new_technical_error'):lines.append('- 新的技术定位错误：'+c['new_technical_error']['source_check'])
 if c.get('threshold_limit'):lines.append('- '+c['threshold_limit'])
 if c.get('mechanical_issue'):lines.append('- 格式：'+c['mechanical_issue'])
 lines.append('')
lines += ['已明确有依据的问题仍有保留：1273与civic的最终消息未提供要求答案、civic的描述续行被误当项目名、stock的日期类型错误与后续恢复。空失败链没有被当作正确性证明。','',
 '连接/服务单列：',f"- 历史两条连接中断原样保留为历史服务删失，不算旧prompt语义错误。第二轮新服务失败{summary['new_service_failures']}次，其中连接中断{summary['new_connection_interruptions']}次、HTTP错误{summary['new_http_failures']}次。",
 f"- 本轮{summary['new_provider_requests']}次新请求、{summary['new_provider_responses']}次响应；自动重试0，输出修复0，复核LLM调用0。",
 f"- 实际最多{summary['max_model_rounds_observed']}轮；{summary['citation_instances']}处引用均存在且实际读过（脚本不证明语义蕴含），核对{summary['source_pages_verified']}个原文页面。",'',
 '作者复核纠正：stock原始T0003只要求最终消息复述答案，未要求只能输出答案；先前作者评价中的这一要求推断撤回，未将其作为本轮漏报或改善。agriculture_16也没有另行要求在最终消息重复CSV数值，不能以此作确认违规。两处探针均在对应新诊断出现前收窄；见review_target_correction*.json。','',
 '终局协议失败单列：agriculture_16第12轮已关闭取证，HTTP200/stop返回DSML read_steps文本，没有native tool_calls且未执行工具。completion_tokens=12411（reasoning=12351），低于48000且未标length，不能解释为token截断或连接中断。未追加第13轮、未重试、未修复；责任在模型还是接口层尚未定位。详见terminal_protocol_failure.json。','',
 '本轮仍未修改失败链因果约束，也未合并格式/技术知识补丁。对于减少总体误报，本轮证据不足；后续因果约束应继续单独检验，不能把终局协议失败记作无错误发现。','',
 '文件：contract/diagnosis.prompt.md为本轮实际prompt；prompt.diff展示唯一文本变化；semantic_review.json为逐项作者复核；comparison.json/experiment_summary.json为结果分项；results/保留原生请求响应和未修复诊断；review_sources/是仅供本地作者复核的原文，不是新增发给模型的证据。',
 '完整源文件核查仍依赖先前阶段D源包；本包保存实际请求中给模型的片段与取证返回。','',
 '本轮已知响应usage：'+json.dumps(summary['usage_known_responses'],ensure_ascii=False)]
(ROOT/'report.txt').write_text('\n'.join(lines)+'\n')
(ROOT/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>第二轮确认门槛单因素验证</title><style>body{font:16px/1.65 system-ui;max-width:1050px;margin:40px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>'+html.escape('\n'.join(lines))+'</pre>')
# Retain the old package metadata with explicit labels; issue a fresh manifest.
c=ROOT/'contract'
for name in ['manifest.json','validation.json','README.txt','bundle.zip']:
 p=c/name
 if p.exists() and not (c/('baseline_v1_'+name)).exists():p.rename(c/('baseline_v1_'+name))
(c/'README.txt').write_text('本轮实际模型输入为diagnosis.prompt.md。它仅在原v1中增加confirmation threshold段落；schema和examples不变。diagnosis.rules.md及baseline_v1_*为原v1构建来源/元数据，不能替代本轮实际prompt。根目录prompt.diff记录唯一模型提示词变化。\n')
b.save(c/'manifest.json',{'files':{str(p.relative_to(c)):b.sha(p) for p in sorted(c.rglob('*')) if p.is_file() and p.name!='manifest.json'}})
for p in WORK.glob('*.py'):shutil.copy2(p,ROOT/'method'/p.name)
shutil.copy2(WORK/'review.json',ROOT/'method/review.json')
checks={'offline_tests_passed':2,'source_projection_checks_passed':8,'single_prompt_insert':True,'schema_examples_unchanged':True,
        'round12_no_tools_tested':True,'no_real_tool_invocation_during_tests':True}
b.save(ROOT/'tests.json',checks)
# Existing schema validation receipts are baseline artifacts, not treatment results.
manifest={'files':{str(p.relative_to(ROOT)):b.sha(p) for p in sorted(ROOT.rglob('*')) if p.is_file() and str(p.relative_to(ROOT)) not in ['manifest.json','bundle.zip','worker.lock']}}
b.save(ROOT/'manifest.json',manifest)
with zipfile.ZipFile(ROOT/'bundle.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for path in manifest['files']:z.write(ROOT/path,path)
 z.write(ROOT/'manifest.json','manifest.json')
with zipfile.ZipFile(ROOT/'bundle.zip') as z:assert z.testzip() is None
print(json.dumps(summary,ensure_ascii=False,indent=2))
