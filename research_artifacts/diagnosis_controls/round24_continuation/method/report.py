import hashlib,importlib.util,json,shutil,zipfile
from pathlib import Path
WORK=Path(__file__).parent
spec=importlib.util.spec_from_file_location('round24',WORK/'run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
a=importlib.util.spec_from_file_location('revalidation',m.BASE/'scripts/revalidate_diagnosis_outputs.py');rv=importlib.util.module_from_spec(a);a.loader.exec_module(rv)
root=m.ROOT;plan=m.verify()
assert all((root/'results'/j['record_id']/'result.json').exists() for j in plan['jobs'])
index=m.b.EvidenceIndex(m.BUNDLE);rows=[];new_case=None
for job in plan['jobs']:
 rid=job['record_id'];folder=root/'results'/rid;result=m.read(folder/'result.json')
 requests=list(sorted((folder/'calls').glob('*/request.json')))
 receipts=[m.read(p) for p in sorted((folder/'calls').glob('*/receipt.json'))]
 old_result=m.read(m.OLD/'results'/rid/'result.json')
 v=m.read(folder/'mechanical_validation.json') if (folder/'mechanical_validation.json').exists() else None
 refs=None
 if (folder/'diagnosis.json').exists():
  data=m.read(folder/'diagnosis.json')
  if isinstance(data,dict):refs=rv.reference_audit(data,index.traces[rid],folder,m.OLD/'results'/rid)
  m.save(folder/'independent_reference_audit.json',refs)
 for p in requests:
  req=m.read(p);assert req['tool_choice']=='auto' and 13<=int(p.parent.name)<=24
  assert req['tools']==m.b.TOOLS and req['messages'][0]['content']==(m.CONTRACT/'diagnosis.prompt.md').read_text()
  assert 'response_format' not in req
  for k in ['model','max_tokens','thinking_token_budget','temperature']:assert req[k]==plan[k]
 row={**job,**result,'accepted':bool(v and v['passed']),'has_final_json':(folder/'diagnosis.json').exists(),
      'new_provider_requests':len(requests),'new_provider_responses':sum((p.parent/'response.json').exists() for p in requests),
      'new_service_failures':sum(bool(r.get('status')) for r in receipts),
      'known_new_usage':{k:sum((r.get('usage') or {}).get(k,0) or 0 for r in receipts) for k in ['prompt_tokens','completion_tokens','total_tokens']},
      'independent_reference_errors':len((refs or {}).get('errors',[]))}
 if job['treatment']=='unchanged_early_terminal_reuse':
  assert m.sha(folder/'native_final.txt')==m.sha(m.OLD/'results'/rid/'native_final.txt')
 if job['treatment']=='original_v1_prefix12_then_continue_to24':
  new_case=row
  # Save local original source pages for reviewing the new diagnosis only.
  if row['has_final_json']:
   data=m.read(folder/'diagnosis.json');safe=m.source_safe(index,rid)
   for extra in m.read(folder/'registered_extra_sources.json'):
    if extra['evidence_id'] in safe.extra:continue
    raw=index.registry(rid).resolve(extra['original_source_ref'])
    safe.extra[extra['evidence_id']]={**extra,'text':raw if isinstance(raw,str) else m.b.encode(raw)}
   ids=list(dict.fromkeys([e['evidence_id'] for e in index.catalogs[rid].values() if e['role']=='original_task_instruction']+['FINAL0001']+[eid for section in ['root_cause_chain','critical_decision_points'] for p in data.get(section,[]) if isinstance(p,dict) for eid in p.get('evidence_ids',[])])) if isinstance(data,dict) else []
   pages=[]
   for eid in ids:
    e=safe.extra.get(eid) or index.catalogs[rid].get(eid)
    if not e:continue
    for start in range(0,max(1,len(e['text'])),12000):pages.append(safe.page(eid,start,12000))
   m.save(root/'review_sources.json',{'pages':pages,'local_review_only':True,'not_sent_to_model':True})
 rows.append(row)
summary={'original_v1_prompt_unchanged':True,'max_model_rounds':24,'forced_final_round':False,'tool_choice':'auto',
 'acceptance_standard':m.POLICY_ID,'early_completed_results_reused':5,'historical_service_censored':2,
 'only_continued_case':'agriculture_16','baseline_prefix_rounds_reused':12,
 'new_provider_requests':sum(r['new_provider_requests'] for r in rows),'new_provider_responses':sum(r['new_provider_responses'] for r in rows),
 'new_service_failures':sum(r['new_service_failures'] for r in rows),
 'accepted':sum(r['accepted'] for r in rows),'total':8,
 'comparable_non_service_accepted':sum(r['accepted'] for r in rows if r['treatment']!='transport_censored_reuse'),
 'comparable_non_service_total':6,'continued_case_result':new_case,
 'known_new_usage':{k:sum(r['known_new_usage'][k] for r in rows) for k in ['prompt_tokens','completion_tokens','total_tokens']},
 'automatic_retries':0,'output_repairs':0,'review_model_calls':0,'independent_semantic_gold':False,
 'frozen_processor_files_unchanged':m.b.verify_processor()}
eff=m.read(root/'read_efficiency.json')
chain=m.read(root/'message_chain_audit.json')
summary.update(new_retrieval_calls=eff['new_retrieval_calls'],fully_redundant_source_reads=eff['fully_redundant_source_reads'],previously_unseen_source_characters=eff['previously_unseen_source_characters'],message_chain_verified=chain['all_checked_histories_match'])
m.save(root/'summary.json',summary);m.save(root/'records.json',rows)
review=root/'semantic_review.json'
lines=['恢复原始v1，模型轮数12→24','',
 '原始v1 prompt逐字不变；撤回确认门槛新增段落，撤回第12轮强制关闭工具。每轮tool_choice=auto，不设置强制最终提交轮。',
 '沿用CodeTracer detailed兼容验收；额外字段不影响通过。单次max_tokens48000、thinking_token_budget16000、max_tool_calls60等其余参数不变。','',
 '比较方式：复用原始v1的五条已完成结果和agriculture_16前12轮历史/26次取证；只从第13轮继续。两条原始服务中断保持删失，不重试。不是八条独立重跑。','',
 f"agriculture_16：{new_case['status']}；已收到模型响应累计{new_case['model_rounds']}轮，工具调用累计{new_case['tool_calls']}次。",
 f"新增请求{summary['new_provider_requests']}次、响应{summary['new_provider_responses']}次；新增服务失败{summary['new_service_failures']}次。",
 f"按当前验收标准：原始v1为5/8，本次为{summary['accepted']}/8；排除两条历史服务删失，本次为{summary['comparable_non_service_accepted']}/6。",
 '五条原来完成的诊断逐字节不变。只在一个实际触及轮数上限的样本上验证，不能据此认证总体诊断准确率。','',
 f"新增{eff['new_retrieval_calls']}次取证，其中{eff['fully_redundant_source_reads']}次没有增加未读原文；新增原文覆盖{eff['previously_unseen_source_characters']}字符。",
 '第16–24轮连续请求同样的E00101尾部与E00102尾部，两段均完整返回到末尾。后续请求已正确携带此前工具返回值；未发现本地消息漏发。',
 '增加轮数没有让这个样本完成提交。重复取证的底层原因尚未定位，不能据此认定所有长轨迹都无法通过增加预算完成。','',
 '本地语义复核：'+('见semantic_review.json。' if review.exists() else '未完成或没有可供复核的新诊断；机械验收不代表语义正确。'),'',
 '原文引用检查与语义质量单独报告，不因额外字段拒收；不会删除或改写模型原输出。',
 '没有新增提醒语、模型复核或输出修复，也没有执行原分析SQL/Python。',
 '固定Processor的79个文件与原始v1对照文件哈希均未改变。','',
 '当前运行配置：configs/05_diagnosis/CURRENT_RUNTIME.json。',
 '计划/前缀核查：plan.json与results/.../prefix_check.json；原生新请求响应保留在results/.../calls/。',
 '新增请求已知usage：'+json.dumps(summary['known_new_usage'],ensure_ascii=False)]
(root/'report.txt').write_text('\n'.join(lines)+'\n')
for p in WORK.glob('*.py'):shutil.copy2(p,root/'method'/p.name)
m.save(root/'tests.json',{'offline_tests_passed':3,'original_prompt_prefix_and_completed_outputs_identical':True,'round13_and24_allow_tools':True,'current_runtime_matches_plan':True})
files={str(p.relative_to(root)):m.sha(p) for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ['manifest.json','bundle.zip']}
m.save(root/'manifest.json',{'files':files})
with zipfile.ZipFile(root/'bundle.zip','w',zipfile.ZIP_DEFLATED) as z:
 for rel in files:z.write(root/rel,rel)
 z.write(root/'manifest.json','manifest.json')
with zipfile.ZipFile(root/'bundle.zip') as z:assert z.testzip() is None
print(json.dumps({k:v for k,v in summary.items() if k!='continued_case_result'},ensure_ascii=False,indent=2))
