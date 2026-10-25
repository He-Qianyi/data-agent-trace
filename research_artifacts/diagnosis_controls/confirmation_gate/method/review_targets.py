import importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('gate',Path(__file__).with_name('run.py'));b=importlib.util.module_from_spec(s);s.loader.exec_module(b)
ROOT=b.ROOT
checks=[
 {'id':'1273-manual-coverage','task':'1273','bucket':'unsupported_factual_scope','baseline':'从grep筛选片段断言完整manual未定义null；不作为已确认数值错误计数。'},
 {'id':'1273-weighting','task':'1273','bucket':'interpretation_unfixed','baseline':'规则均值vs交易加权未固定；旧输出已有不确定措辞，不能算已确认错误来虚增改善。'},
 {'id':'1273-file-absence','task':'1273','bucket':'unsupported_confirmed_discrepancy','baseline':'把未见写入升级为数值未写入文件；最终回复不含答案则确有原文。'},
 {'id':'1464-inline-script','task':'1464','bucket':'unsupported_confirmed_discrepancy','baseline':'把保存已创建脚本的条件要求扩展为所有内联代码必须另存脚本。'},
 {'id':'1464-empty-list','task':'1464','bucket':'interpretation_unfixed','baseline':'空列表语义属于未决项，旧输出明确并非已确认数值错误。'},
 {'id':'agri22-f1','task':'agriculture_22','bucket':'interpretation_unfixed','baseline':'折内F1均值vs汇总OOF未固定；旧输出已经不判错，不能计误报下降。'},
 {'id':'agri16-crop-map','task':'agriculture_16','bucket':'unsupported_confirmed_discrepancy','baseline':'真实映射失效，但任务没有明确名称归一化规则；仍把该候选解释列为已支持问题/失败链。'},
 {'id':'agri16-pca-scope','task':'agriculture_16','bucket':'unsupported_confirmed_discrepancy','baseline':'PCA soil列口径未固定却在总结中列为已支持问题。'},
 {'id':'agri16-ridge','task':'agriculture_16','bucket':'unsupported_technical_claim','baseline':'错误推断Ridge系数不唯一及删dummy不影响R²；单列技术断言，不与口径误报混计。'},
 {'id':'agri16-pca-driver','task':'agriculture_16','bucket':'unsupported_technical_claim','baseline':'只凭解释方差比例断言天气主导PCA。'},
 {'id':'agri16-fixed12','task':'agriculture_16','bucket':'unsupported_confirmed_discrepancy','baseline':'建议改特征后仍固定核对12行，把当次行数变成必需条件。'},
 {'id':'agri16-small-impact','task':'agriculture_16','bucket':'unsupported_technical_claim','baseline':'没有敏感性证据却说删21行影响很小。'},
 {'id':'civic-file-absence','task':'civic_unstructured__query6','bucket':'unsupported_confirmed_discrepancy','baseline':'从已见最终文本/未见写入断言answer文件缺失。'},
 {'id':'civic-stopping-cause','task':'civic_unstructured__query6','bucket':'unsupported_causal_claim','baseline':'从解析问题跳到反复返工导致停止；因果规则本轮不改，单列。'},
 {'id':'stock-final-format','task':'stockmarket__query1','bucket':'no_unstated_final_format_requirement','baseline':'实际T0003要求复述答案，未要求只输出答案；不能将解释文字视为已确认格式错误。历史作者复核中的该说法撤回。'}]
positives=[
 {'task':'1273','probe':'最终回复没有给出答案；仅可确定消息交付不足，不证明文件缺失。'},
 {'task':'civic_unstructured__query6','probe':'最终消息没有年份/总额；实际正则漏掉混合章节；实际续行误识别为项目名。'},
 {'task':'stockmarket__query1','probe':'日期字面量类型错误已恢复；最终文件与查询一致；任务没有only-answer要求，不得强加。'},
 {'task':'1464','probe':'null通配有手册依据、写入和读回有据；不可把未知集合正确性当已证实正确或错误。'},
 {'task':'agriculture_22','probe':'执行和读回有据；F1聚合口径未固定，不应变成确定错误。'},
 {'task':'agriculture_16','probe':'S0033异常已恢复；S0034执行写入和数据框回显有据。最终消息只有核验意图，但任务未单独要求最终消息再重复CSV数值，缺少独立读回本身不认证错误。'}]
b.save(ROOT/'preregistered_review_targets.json',{'recorded_at':b.now(),'source':'existing round1 and native-baseline author reviews; prepared before treatment outputs','independent_gold':False,'sent_to_model':False,'targets':checks,'supported_observation_probes':positives,'counting_rule':'deduplicate mechanisms within a trace; do not count uncertainty-only notes as false positives; new unsupported claims must also be reported'})
index=b.EvidenceIndex(b.BUNDLE)
ids={
'1273':['E00017','E00018','E00024','E00025','E00034','E00035','E00047','E00048'],
'1464':['E00005','E00012','E00014','E00015','E00018'],
'agriculture_22':['E00012','E00014','E00015','E00019'],
'agriculture_16':['E00082','E00089','E00091','E00101','E00102','E00104','E00105'],
'civic_unstructured__query6':['E00035','E00041','E00045','E00054','E00056','E00057'],
'stockmarket__query1':['E00023','E00026','E00030','E00033','E00039','E00040','E00053','E00054']}
for job in b.read(ROOT/'plan.json')['jobs']:
 if job['task_id'] not in ids:continue
 safe=b.SafeEvidence(index,job['record_id']); initial=safe.initial()
 selected=ids[job['task_id']]+['FINAL0001']+initial['task_evidence_ids']
 pages=[]
 for eid in selected:
  e=safe.extra.get(eid) or index.catalogs[job['record_id']][eid]
  for start in range(0,max(1,len(e['text'])),12000):pages.append(safe.page(eid,start,12000))
 b.save(ROOT/'review_sources'/(job['record_id']+'.json'),{'task_id':job['task_id'],'pages':pages,'local_author_review_only':True,'not_sent_to_model':True})
print('Review targets and original source pages saved locally; not included in any model input.')
