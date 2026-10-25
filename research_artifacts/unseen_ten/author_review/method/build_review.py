"""Save an evidence-based assistant review; not independent human gold or an accuracy score."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import hashlib,html,json,shutil
BASE=Path(__file__).resolve().parents[2]
WORK=BASE/'work/diagnosis_unseen_ten_semantic_review_20261020'
SRC=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_20261020'
CLEAN=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_format_revalidation_20261020'
ROOT=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_semantic_review_20261020'
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
assert Path('/Volumes/Public HD').is_mount()
assert not ROOT.exists(),'Never overwrite a completed review'
checked=0
for root in [SRC,CLEAN]:
 for rel,digest in read(root/'manifest.json')['files'].items():assert sha(root/rel)==digest,(root,rel);checked+=1
for rel,digest in read(SRC/'plan.json')['guarded_files'].items():assert sha(BASE/rel)==digest,rel
checks=read(WORK/'checks.json')
assert checks['yelp_regex']['recorded_businessid_47_description_matches'] is False
assert checks['yelp_regex']['control_matches_Goleta_CA'] is True
rows=[]
def add(rid,assessment,core,findings,confirmed,limits,refs):
 job=next(j for j in read(CLEAN/'summary.json')['traces'] if j['record_id']==rid)
 trace=read(SRC/'source_view/traces'/f'{rid}.json')
 catalog={e['evidence_id']:e for e in trace['evidence_catalog']}
 rows.append({**{k:job[k] for k in ['record_id','benchmark','agent_id','task_id']},'assessment':assessment,'core_review':core,
 'supported_claims':confirmed,'findings':findings,'limits':limits,'evidence_ids':refs,
 'diagnosis_path':str((CLEAN/'results'/rid/'diagnosis.json').relative_to(BASE)),'diagnosis_sha256':sha(CLEAN/'results'/rid/'diagnosis.json'),
 'normalized_trace_path':str((SRC/'source_view/traces'/f'{rid}.json').relative_to(BASE)),'normalized_trace_sha256':sha(SRC/'source_view/traces'/f'{rid}.json'),
 'evidence_sources':[{k:catalog[eid].get(k) for k in ['evidence_id','step_id','role','fact_pointer','original_source_ref']} for eid in refs if eid!='FINAL0001'],
 'recorded_official_scores_used_posthoc_only':[x['payload'].get('score') for x in trace['facts']['evaluation']['records'] if 'score' in x['payload']]})
def issue(severity,pointer,claim,problem,correction,evidence):return {'severity':severity,'diagnosis_pointer':pointer,'claim':claim,'problem':problem,'recommended_correction':correction,'evidence_ids':evidence}
add('rawio-c05784a0b4925c35b147','需要修正','capture_delay 是有证据支持的定位方向，但具体 ID 数量和修正后的答案数量写错。',[
 issue('high','/critical_decision_points/0/reasoning','7 个 immediate fee ID；排除后42变35','E00050 在42个候选中列出8个 immediate：284,456,502,804,870,915,944,996。诊断漏了996，42−8=34，而非35。','修正为8个及34个；逐项比较集合，避免仅凭概括计数。',['E00050','E00065','E00068']),
 issue('medium','/root_cause_chain/1/description','把1天与immediate混为一谈是已确认根因','原始手册没有明确给出immediate的数值边界；诊断在失败链中用确定语气，在总结又保留条件。事后官方集合差异支持这个方向，但不能当作模型当时已读到的定义。','原始证据层保持条件性解释；另列事后参考答案对照：最终42个比官方34个恰好多出这8个，不改写历史诊断依据。',['E00006','E00010','E00050'])],
 ['最终答案确为42个；商户capture_delay为1；代码把immediate判为days<=1。','事后记录的官方集合与42个答案的差集恰为8个immediate规则，去除后集合一致。'],
 ['对merchant-only与transaction-matched的口径讨论不能单靠措辞确定；官方集合只用于本次事后核验。'],['T0002','E00006','E00010','E00046','E00047','E00050','E00056','E00064','E00065','E00068'])
add('rawio-c63c1bb0b2f1eaa2080b','有条件合理，未定位评分失败','保留数量/比率歧义、留空失败链是合理的；脚本路径违规真实，但未解释答案评分失败。',[
 issue('medium','/correct_strategy','保留A. NL交付','确认了计数计算正确，并不等于确认题目应按计数。任务未固定top的指标；补充数据核算表明计数第一NL、欺诈率第一BE。','写成“若按数量，NL；若按比率，BE；需固定任务口径”，不能无条件建议保留NL。',['T0002','E00018','E00019'])],
 ['138236条记录、10765笔欺诈及NL2955笔与补充数据核算一致。','脚本写入/tmp违背保存工作区的任务约束；write_stdin失败后重新执行脚本恢复。','没有把/tmp问题强行连到答案值错误。'],
 ['完整数据算得BE比率10.85045%、NL9.92944%，为事后补充核算；诊断模型当时只读取到计数。','本条仍不能作为已成功定位评分失败根因的案例。'],['T0002','T0005','E00009','E00015','E00018','E00019','E00022','E00023','FINAL0001'])
add('rawio-ad24e54fdf0c7d46da13','基本合理，关键影响待补证','正确区分了已恢复的MCC取反错误与最终筛选中的通配疑点。',[
 issue('low','/critical_decision_points/1/reasoning','未报TypeError说明GlobalCard无null account_type/mcc','控制流会先过滤非H账户，因此部分规则的mcc成员判断可能根本未执行；未报错不能证明所有GlobalCard规则都无null mcc。','删除这个普遍性推断，直接检查相应规则。',['E00054','E00058']),
 issue('medium','/summary','写为0.064而非0.064000只是格式细节','六位小数确为明确要求，但仅补零不会解决记录中的数值评分失败。','把格式违规和可能漏纳入费率规则分开；后者需补充规则内容才能确认。',['T0002','T0003','E00063','E00066'])],
 ['S0014的not-in逻辑已在S0015/S0016改正，未进入最终代码。','3条规则204/612/921的费用均值确为0.064，费用公式与手册一致。','补充敏感性核算：把空列表作为通配后46条规则，平均0.1232173913，六位小数0.123217，说明模型指出的筛选风险有实际量级。'],
 ['文档明确null通配，但[]通配需额外定义/参考实现，不能仅以数值接近标准答案证明全部语义。'],['T0002','T0003','E00015','E00029','E00039','E00046','E00050','E00054','E00058','E00063','E00066'])
add('rawio-6e9757019dc8767f3cfc','基本合理，范围需限定','月度条件遗漏和最终未给出答案均有证据；不强行断言脚本异常造成了终止是合理的。',[
 issue('low','/critical_decision_points/0/reasoning','遗漏月度条件可能错误纳入或排除费用','在其余条件固定、条件为合取筛选时，少检查两个条件只会扩大候选集合，不能单独造成漏排后的假阴性。','改成“可能错误纳入本应被月度条件排除的规则”。',['E00092']),
 issue('low','/critical_decision_points/1/reasoning','导航中没有answer.txt写入，所以没有写入answer.txt','导航索引不能证明文件实际不存在；本次可确认的是录制步骤未见agent写入，最终消息未提供答案。runner可能保存最终文字。','将“未见agent写入记录”和“最终内容未交付”写清，文件存在性另按快照说明。',['T0008','T0010','FINAL0001'])],
 ['最终solve.py确实不检查monthly_volume和monthly_fraud_level。','验证脚本是在打印12个ID之后因把int当作dict索引而异常，不能说12个ID没算出来。','事后补充核查：列表含602(1m–5m)、895(<100k)，若按第10天对应自然月1月，商户月额113260.42，两者均不满足；删除后剩10个。'],
 ['任务的“10th of the year”需要明确时间解释；本次月额计算沿用第10天的解释，不用隐藏答案反向确定日期。','补充fees/payments核算不是诊断模型当时取得的证据。'],['T0002','T0008','T0010','E00014','E00051','E00092','E00098','E00107','FINAL0001'])
add('rawio-e7c485f571630e650d08','需要修正主要解释','正则漏掉St. Louis及非美国州混入是事实，但当前失败链没有覆盖决定州排名的统计对象差异，平均口径也说得过满。',[
 issue('high','/root_cause_chain','把MO答案问题主要归于St. Louis漏配','正则确实漏掉81条metadata review_count，但这不足以解释基准为何期望PA。记录已给出review行数PA617、MO164、FL320，而business.review_count求和为MO2151、PA1804，是不同总体/计数来源。','把business.review_count与实际review行数的选择列为主要口径问题；分别核验，不把修复正则当成充分修复。',['E00012','E00015','E00016']),
 issue('high','/critical_decision_points/1/reasoning','企业平均必须先按企业均值再等权平均','任务未明确企业等权还是评论加权；benchmark反馈指向PA及3.7，记录中PA评论级均值3.7634。不能凭英文措辞将评论级均值确认为错误。','把等权/加权当作待固定口径，按任务/参考定义验证后再判断。',['T0002','E00002','E00016']),
 issue('medium','/summary','164条评分是抽样评论','2000行review与metadata计数不同只能证明覆盖/口径不同，未证实抽样机制。','改为“使用所提供review表中的164条评分，覆盖与metadata计数不一致”。',['E00012','E00016'])],
 ['正则无法匹配St. Louis中的句点；businessid_47未计入MO列表，review_count=81。','AB进入州集合，未影响当次最大值。','只按已记录review行计数，未分配行共180，即使全给其他单一州，其最大也不超过500<PA617；在该计数口径下PA排名稳健。'],
 ['这是事后作者式复核，使用了已录官方反馈辅助确认基准口径；不能由评分直接推导完整因果金标。'],['T0002','E00002','E00008','E00011','E00012','E00015','E00016','E00019','FINAL0001'])
add('rawio-28b2f965e92b9d64d640','事实基本成立，因果链需降级','没有数值交付、README过滤过宽、写而未执行等判断有证据；循环太慢被升级为停止根因缺少执行证据。',[
 issue('high','/root_cause_chain/1/description','2.77M逐仓库循环使脚本不可行并导致最终未交付','S0014只有heredoc写文件，没有执行；无计时、超时、预算耗尽或运行失败证据。最后文字承认太慢只支持模型预判，不能证明运行停止由性能问题造成。','记为“未执行方案的性能风险/候选设计问题”；已确认终点是未交付，停止原因仍未知。',['E00038','E00039','E00042','E00043','FINAL0001']),
 issue('medium','/critical_decision_points/0/reasoning','languages多于repos就是口径问题','不同表的覆盖规模不同并不自动证明选错总体；可以要求核对关联，但不能直接以400000为正确全集。','保留为覆盖范围与连接键核验项，勿把表行数差当作确定错误。',['E00023','E00030','E00036'])],
 ['LIKE %README%确包含README.rst/.txt等，超出指定README.md。','脚本按仓库“任一README含版权”计数，与按README.md文件计数存在潜在粒度差异，需固定定义。','S0013尾部NameError发生在统计输出之后，不能说此前有用计数未产生。'],
 ['可确认未见agent写答案，不据此断言runner最终文件不存在。','没有测量执行性能，不给出运行时间或“必然不能完成”结论。'],['T0002','T0003','E00023','E00030','E00033','E00036','E00038','E00039','E00042','E00043','FINAL0001'])
add('rawio-ecb3a8772d271f6bc689','主要异常识别合理，因果需保留','ID分块假设缺乏支持且受到样本反驳，最终没有数值答案，这些判断成立。',[
 issue('medium','/root_cause_chain/1/description','错误类别假设直接导致答案始终没算出','记录只表明该假设出现在最后消息、后续计算未出现；无法排除独立的提前结束/工具调用未继续原因。','保留为已确认的无依据分类判断；其与停止未交付之间为候选因果，不是已证实链条。',['E00063','E00065','E00066','FINAL0001']),
 issue('low','/summary','没有类别字段就无法业务过滤','内容中仍可能支持有明确定义的分类；模型在correct_strategy中保留此可能是恰当的。','继续区分“无类别字段”和“无法进行任何类别判定”，不要把探索性全年计数本身当成已交付错误答案。',['E00014','E00020','E00053'])],
 ['ID100为天文科技标题，ID30000为政治标题，与最终Business/Sports分块断言冲突。','2010–2020欧洲年度计数未含business条件，是中间量。','无最终数值；11年分母正确。'],
 ['answer.txt的runner快照实际保存了最终分析片段；应表述为未交付有效答案而非文件必然不存在。'],['T0002','T0003','E00014','E00020','E00022','E00052','E00053','E00062','E00063','E00065','E00066','E00068','FINAL0001'])
add('rawio-747fdd4b9b059ac3c74c','主要诊断合理，局部归因需收紧','货币单位、全局阈值被改成子集阈值、年内标准差替代滚动窗口，都能从任务与最终代码直接核实。',[
 issue('medium','/critical_decision_points/0/reasoning','S0038证明只改波动率口径使第五行变为Honduras','对照同时把价格由年均改为年末月价，且重算了价格分位数。不能把最终换行唯一归因于波动率窗口。','保留口径不符的判断；具体行变化需固定其他因素再比较。',['E00116','E00117']),
 issue('low','/correct_strategy','rolling(window=12)就是12个月滚动','有月份缺口时12个观测点可能跨越超过12个日历月；已有对照还设置min_periods=1。','修正建议需明确日历月索引、缺月与最小窗口样本策略；不把记录中的对照实现当作已验证标准答案。',['T0005','E00116'])],
 ['最终代码不区分货币且把mp_price直接命名USD，Colombia/DR不在录制的USD国家集合中。','p_median/v_median/p_q75均在逐步收缩后的filtered上计算，与global字面要求不符。','验证只覆盖列名和两个条件，未核查货币/滚动定义。'],
 ['没有重跑WFP全量分析，未独立求正确国家年份集合。','源码hg/ha_yield是单产字段，不能因重命名就视为总产量；诊断已提示替代，但未解决数据适配问题。'],['T0005','E00075','E00076','E00116','E00117','E00123','E00124','E00126','E00127','E00130','FINAL0001'])
add('rawio-5bc9d9c1b623a95007e1','主要错误成立，附加数学判断有误','recommended_crop错误绑定Item、562行等于produced_crop而非Teff，具有直接代码与运行输出支持。',[
 issue('high','/summary','先逐样本求差再平均与两列均值相减是不同口径','同一组完整观测有mean(X−Y)=mean(X)−mean(Y)。当前输入温度成对无缺失；Potato筛选47行，两者均为17.3702127677，仅浮点舍入差异。','删除这条伪差异。真正的范围疑点是使用filtered_cr而不是任务所说的broader crop profile。',['T0005','E00215']),
 issue('medium','/correct_strategy','temp_range仅是两种聚合方式待确认','最终代码对过滤后的土壤样本算温度。补充数据中Potato全48行温差17.3684027796，筛选47行17.3702127677；区别来自样本范围。','把修复项写为“核验broader profile范围”，不要改等价的运算顺序。',['T0005','E00215']),
 issue('low','/critical_decision_points/1/reasoning','运行后完全没有核验','脚本确有head/tail、类型转换和行数打印，但没有检验recommended_crop与Teff的语义不变量。','改为“未核验关键语义一致性”，而非任何核验都没有。',['E00215','E00221'])],
 ['同一输出同时打印全局Teff及produced Potatoes562行；输出列Item重命名导致recommended_crop错误，可在不读完整文件时从代码推出。','捕获到ARIMA成功返回和非零预测，不能把此前索引异常当成最终全零失败。'],
 ['官方事后反馈还显示output1行数562对1087，单改一列不会解决全部得分差异。','两种农药阈值样本范围、yield.csv与yield_df.csv覆盖、硬编码预测年份仍需额外定义/数据核对，当前诊断非穷尽。'],['T0005','E00154','E00168','E00215','E00220','E00221','FINAL0001'])
add('rawio-4b9fd6b15e4b554779db','基本合理，未定位停止原因','仅有探索、无核心计算输出，最终消息仍是继续检查shapefile；留空失败链合理。',[
 issue('low','/summary','没有任何分析代码','记录中存在pandas统计代码，只是没有完成任务要求的匹配、强度、相关性及导出计算。','改为“未完成核心分析计算与导出”，保留无法判断停止原因。',['E00028','E00029','E00031','E00034'])],
 ['11步均为列目录、读头部和描述性统计；没有output.csv写入命令。','当前标准化快照中output.csv未找到，可在本次事后核查支持交付缺失，但不是模型当时看到的文件证据。','没有把未交付倒推为具体算法根因。'],
 ['不能由最终33字符的中途文字判断是模型提前结束、录制问题或预算等原因。'],['T0005','E00028','E00029','E00031','E00032','E00034','E00035','FINAL0001'])
# Store cited source text with locations; diagnostic model read status stays distinct.
ROOT.mkdir(parents=True);(ROOT/'method').mkdir()
for p in [Path(__file__),WORK/'checks.py']:shutil.copyfile(p,ROOT/'method'/p.name)
shutil.copyfile(WORK/'checks.json',ROOT/'checks.json')
ledger=[]
for row in rows:
 t=read(BASE/row['normalized_trace_path']);c={e['evidence_id']:e for e in t['evidence_catalog']}
 for eid in row['evidence_ids']:
  if eid=='FINAL0001':ledger.append({'record_id':row['record_id'],'evidence_id':eid,'text':t['facts']['final_response']['text'],'source_ref':t['facts']['final_response']['source_ref']})
  else:ledger.append({'record_id':row['record_id'],**c[eid]})
save(ROOT/'evidence_ledger.json',ledger)
result={'reviewed_at':datetime.now(timezone.utc).isoformat(),'reviewer':'assistant evidence-based author-style review with local deterministic checks',
 'not_independent_human_gold':True,'not_blinded':True,'semantic_accuracy_percentage_reported':False,'new_external_model_calls':0,
 'captured_commands_or_scripts_executed':False,'new_offline_spot_calculations':True,'original_outputs_modified':False,
 'scope':'All ten diagnoses; task/code/returned-evidence checks plus explicitly separated recorded post-run evaluations and current local-input spot checks. Not exhaustive recomputation of all tasks.',
 'original_experiment_files_verified_unchanged':checked,'source_and_guarded_files_verified_unchanged':len(read(SRC/'plan.json')['guarded_files']),
 'cases':rows}
save(ROOT/'review.json',result)
lines=['新十条错误定位诊断：内容合理性复核','',
 '总体：多条主要判断有直接证据支持，但不能将10/10结构验收解释成10条内容全部正确。发现计数错误、数学等价性误判、统计对象遗漏和因果过度归因。',
 '本次是助手基于证据进行的作者式复核，不是独立人工金标、不是盲审，不报告准确率。原诊断保持不变。',
 '模型生成时未取得的官方评估和本地数据仅用于本次事后核验，在每条中明确标注。未调用外部模型、未执行轨迹中录制的命令/脚本；少量复算使用本次单独编写的checks.py。','']
for i,r in enumerate(rows,1):
 lines += [f"{i}. {r['benchmark']} / {r['agent_id']} / {r['task_id']} — {r['assessment']}",r['core_review'],'有支持的内容：']
 lines += ['  - '+x for x in r['supported_claims']]
 lines+=['需要修改或限定：']
 for f in r['findings']:lines += [f"  [{f['severity']}] {f['problem']}",f"  建议：{f['recommended_correction']}",f"  位置：{f['diagnosis_pointer']}；证据：{', '.join(f['evidence_ids'])}"]
 lines+=['边界：']+['  - '+x for x in r['limits']]+['']
lines+=['复现与保存：checks.json 保存数值/逻辑核查；evidence_ledger.json 保存引用的录制原文及来源；review.json 保存结构化逐条结论。',
 f'原实验与清理结果{checked}个已登记文件、{result["source_and_guarded_files_verified_unchanged"]}个冻结/输入文件哈希保持不变。']
(ROOT/'report.txt').write_text('\n'.join(lines)+'\n')
# Validate all report evidence IDs and every saved checksum.
assert len(rows)==10
for r in rows:
 c={e['evidence_id'] for e in read(BASE/r['normalized_trace_path'])['evidence_catalog']}|{'FINAL0001'}
 assert set(r['evidence_ids'])<=c
 for f in r['findings']:assert set(f['evidence_ids'])<=c
files={str(p.relative_to(ROOT)):sha(p) for p in ROOT.rglob('*') if p.is_file() and not p.name.startswith('._') and p.name!='manifest.json'}
save(ROOT/'manifest.json',{'files':files})
for root in [SRC,CLEAN]:
 for rel,digest in read(root/'manifest.json')['files'].items():assert sha(root/rel)==digest,(root,rel)
print(json.dumps({'reviewed':len(rows),'saved':str(ROOT),'findings_by_severity':dict(Counter(f['severity'] for r in rows for f in r['findings'])),'source_files_unchanged':checked},ensure_ascii=False))
