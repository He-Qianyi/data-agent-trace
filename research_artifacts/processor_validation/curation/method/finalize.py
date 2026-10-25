from pathlib import Path
import json,hashlib,shutil,datetime,collections
BASE=Path(__file__).resolve().parents[2];ROOT=BASE/'artifacts/02_recording_quality/trace_curation_environment_short_v1_20261020';WORK=BASE/'work/trace_curation_environment_short_v1_20261020'
assert Path('/Volumes/Public HD').is_mount()
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 tmp=p.with_name(p.name+'.writing');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
summary=read(ROOT/'summary.json');checks=read(ROOT/'subset_checks.json');rows=read(ROOT/'decisions.json');plan=read(ROOT/'plan.json')
assert checks['status']=='passed' and summary['retained']==145 and summary['excluded']==19
config_path=BASE/'configs/03_trace_processor/CURRENT_VALIDATION.json';assert sha(config_path)==plan['source_config_sha256'],'active_config_changed_during_audit'
shutil.copyfile(config_path,ROOT/'CURRENT_VALIDATION.before.json')
now=datetime.datetime.now(datetime.timezone.utc).isoformat();eligible=ROOT/'eligible'
curation={'schema':'trace-corpus-curation-v1','policy_id':'environment-and-short-success-v1-20261020','updated_at_utc':now,
 'source_full_normalized_results':plan['source_bundle'],'source_full_trace_count':164,'normalized_results':str(eligible.relative_to(BASE)),
 'active_trace_count':145,'excluded_count':19,'retained_manifest':str((ROOT/'retained_manifest.jsonl').relative_to(BASE)),
 'excluded_manifest':str((ROOT/'excluded_manifest.jsonl').relative_to(BASE)),'decisions':str((ROOT/'decisions.json').relative_to(BASE)),
 'summary':str((ROOT/'summary.json').relative_to(BASE)),'verification':str((ROOT/'eligible_verification.json').relative_to(BASE)),
 'scope':'Strict downstream diagnosis corpus: exclude confirmed external testbed binding/service/load defects and official-score-1 traces with fewer than10 tool steps. Historical full164 data remain preserved.',
 'limitations':['Present-state input-file structural checks do not prove historical input equivalence or exhaustive environment correctness.','IMDB alternate-database access attempts are preserved; the task-provided binding failed and equivalent input recovery was not independently established.','Task-file corruption, generation incompleteness, agent task failure, and record completeness are distinct.'],
 'model_calls':0,'original_data_modified':False}
curation_config=BASE/'configs/02_recording_quality/CURRENT_CURATED_CORPUS.json';assert not curation_config.exists(),'use a new version if another curation config exists'
old=read(config_path);new={**old,'previous_normalized_results':old['normalized_results'],'full_normalized_results':old['normalized_results'],
 'normalized_results':str(eligible.relative_to(BASE)),'full_processed_trace_count':164,'active_trace_count':145,
 'active_curation_config':str(curation_config.relative_to(BASE)),'corpus_eligibility_summary':str((ROOT/'summary.json').relative_to(BASE)),
 'updated_at_utc':now,'scope':old['scope']+' Active downstream corpus now filtered to145:14 external-environment defects and5 short full-score successes excluded; original164 retained at full_normalized_results. No reannotation or model calls.'}
save(curation_config,curation);save(config_path,new)
save(ROOT/'CURRENT_VALIDATION.activated.json',new);save(ROOT/'CURRENT_CURATED_CORPUS.activated.json',curation)
save(ROOT/'activation.json',{'activated_at':now,'before_sha256':plan['source_config_sha256'],'after_sha256':sha(config_path),'curation_config_sha256':sha(curation_config),'active_count':145,'full_count':164,'original_trace_bytes_unchanged':True,'eligible_verified':checks['status']})
(ROOT/'method').mkdir(exist_ok=True)
for p in WORK.glob('*.py'):shutil.copyfile(p,ROOT/'method'/p.name)
excluded=[r for r in rows if r['decision']=='excluded']
source_id='curation_decisions';sources=[{'id':source_id,'label':'逐条筛选决定与原始证据','path':'decisions.json'},{'id':'input_checks','label':'65个保留输入文件的只读结构检查','path':'input_file_checks.json'},{'id':'subset_checks','label':'筛选视图、索引及数据库内容验证','path':'subset_checks.json'}]
blocks=[{'id':'title','type':'markdown','body':'# Trace Corpus Curation'},
 {'id':'summary','type':'markdown','sourceId':source_id,'body':'## 筛选后保留145条，19条退出当前诊断语料\n\n本轮从当前v5的164条完整录制轨迹中，排除14条存在明确外部环境配置问题的轨迹，以及5条官方满分且少于10个工具步骤的轨迹，两组不重叠。原始记录、标准化内容、评分及历史实验全部保留；本次没有调用模型。'},
 {'id':'definitions','type':'markdown','body':'## 两项规则作用于整条运行\n\n计数单位是一条任务运行/attempt；步骤数取标准化事实层的工具调用数，不把SQL子操作或历史消息重复计数。短成功轨迹指官方score为1且工具步骤少于10。环境排除要求绑定任务明确提供的资源/服务、实际返回和后续恢复记录；agent自己的路径拼写、空格转义、可选依赖、数据库探测和代码错误继续保留。'},
 {'id':'env','type':'markdown','sourceId':source_id,'body':'## 14条环境问题集中在DataAgentBench的三个任务组\n\nIMDB的10条轨迹均按任务说明访问movies_db，却收到数据库不存在的返回。后续部分尝试访问其他库，但没有独立证明它们恢复了同一任务输入；按严格环境口径排除，不声称全部后续查询都未执行。CVE的query4、query5及Krama的query1、query10在任务指定MongoDB地址连接被拒绝，且没有后续成功读取相应数据源的证据；CVE还观察到指定KEV存储空载或绑定到不同服务实例的情况。以下清单提供每条的判定；完整步骤、引文及恢复尝试保存在逐条证据中。'},
 {'id':'excluded','type':'table','tableId':'excluded_cases'},
 {'id':'inputs','type':'markdown','sourceId':'input_checks','body':'## 65个可追溯输入文件通过只读结构检查\n\n检查覆盖约14.5GB文件：SQLite quick_check、DuckDB只读打开并读取各表首行、完整CSV/JSON/JSONL流式解析、地理文件读取及文本解码。没有发现据此需要新增排除的文件损坏。它说明当前保留下来的文件结构可读，不能证明过去每一次运行都使用了完全相同的输入，也不认证数据业务内容正确。'},
 {'id':'falsepositives','type':'markdown','body':'## 常见agent错误没有被误删\n\nDBF默认编码不匹配、把DBF当CSV、把JSONL当单个JSON的报错，均能由正确读取方式解释，并有后续成功读取或当前结构核验支持。错误路径、被转义的空格、额外探测的非任务数据库、未安装的非必需CLI或可选库，也没有单独触发整条排除。初筛关键词命中保留为候选信号，不能充当环境故障判定。'},
 {'id':'validation','type':'markdown','sourceId':'subset_checks','body':'## 145条筛选视图可供现有诊断流程直接读取\n\n保留轨迹的JSON内容和原始164条哈希均不变。完整schema、来源和引用验证通过；现有EvidenceIndex加载145条。下游JSONL逐行保留，DuckDB通过保留子集的双向EXCEPT ALL及字段类型比较，未重新推断JSON结构或改写静态事实。'},
 {'id':'limits','type':'markdown','body':'## 结果限定与后续使用\n\n当前入口已切换到145条筛选视图，后续新诊断以该入口为准。全量164条仍作为历史处理结果保留。本轮没有重启数据库、重跑agent或评估器，不能用当前服务是否在线反推历史环境；也没有把未发现故障写成环境百分之百正确。IMDB的替代库是否与原任务输入严格等价仍是开放问题，如以后补齐独立证据，可在新筛选版本中恢复相应条目。'}]
labels={'ENV_REQUIRED_SERVICE_UNAVAILABLE':'任务指定服务不可用','ENV_REQUIRED_STORE_NOT_LOADED':'必需存储未正确加载','ENV_REQUIRED_DATABASE_BINDING_MISSING':'任务指定数据库不存在','SHORT_SUCCESS_LT10':'满分且少于10步'}
detail=[{'dataset':r['benchmark'],'agent':r['agent_id'],'task':r['task_id'],'steps':r['tool_steps'],'reason':'；'.join(labels[x] for x in r['exclusion_rules'])} for r in excluded]
artifact={'surface':'report','manifest':{'version':1,'surface':'report','title':'Trace Corpus Curation','generatedAt':now,'sources':sources,'tables':[{'id':'excluded_cases','title':'19条排除清单','dataset':'excluded_cases','sourceId':source_id,'defaultSort':{'field':'task','direction':'asc'},'columns':[{'field':'dataset','label':'数据集','type':'text'},{'field':'agent','label':'Agent','type':'text'},{'field':'task','label':'任务','type':'text'},{'field':'steps','label':'工具步骤','format':'number'},{'field':'reason','label':'排除原因','type':'text'}]}],'blocks':blocks},'snapshot':{'version':1,'generatedAt':now,'status':'ready','datasets':{'excluded_cases':detail}},'sources':sources}
save(ROOT/'artifact.json',artifact)
notes={'delivery_mode':'html','runtime_reason':'Codex local repository workflow with durable external-disk artifacts; no report MCP surface selected.','audience':'technical','required_structure_mapping':{'technical_summary':'summary','key_findings':'env/excluded/inputs/falsepositives','definitions':'definitions','methodology':'definitions/inputs/validation','limitations':'limits','recommended_next_steps':'limits','further_questions':'limits'},'visual_choice':'Exact exclusion lookup table; charts would hide task-specific evidence.','implementation_scripts':'method/','canonical_project_root':str(BASE)}
save(ROOT/'report_source_notes.json',notes)
notebook={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}},'cells':[
 {'cell_type':'markdown','metadata':{},'source':['# Trace Corpus Curation\n','只读复核已冻结的筛选结果；不会重跑原任务、模型或评估器。实际检查脚本见同目录method/。']},
 {'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['from pathlib import Path\n','import json, hashlib\n',f"root = Path({str(ROOT)!r})\n","summary = json.loads((root/'summary.json').read_text())\n","{k: summary[k] for k in ['input_traces','retained','excluded','environment_excluded','short_success_excluded']}\n"]},
 {'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':["rows = json.loads((root/'decisions.json').read_text())\n","excluded = [r for r in rows if r['decision'] == 'excluded']\n","[(r['benchmark'], r['agent_id'], r['task_id'], r['exclusion_rules']) for r in excluded]\n"]},
 {'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':["plan = json.loads((root/'plan.json').read_text())\n",f"base = Path({str(BASE)!r})\n","parent = base / plan['source_bundle']\n","assert all(hashlib.sha256((parent/r['trace_path']).read_bytes()).hexdigest() == r['sha256'] for r in plan['traces'])\n","assert not any(r['tool_steps'] < 10 and 1 in r['scores'] for r in rows if r['decision'] == 'retained')\n","json.loads((root/'subset_checks.json').read_text())['status']\n"]}]}
save(ROOT/'audit_companion.ipynb',notebook)
(ROOT/'README.md').write_text('# 当前筛选结果\n\n保留145条，排除19条（环境14条，短满分5条）。\n\n- 当前轨迹：`eligible/traces/`\n- 保留清单：`retained_manifest.jsonl`\n- 排除清单：`excluded_manifest.jsonl`\n- 逐条理由和证据：`decisions.json`\n- 报告：`report.html`\n- 可复核脚本：`method/`；只读Notebook：`audit_companion.ipynb`\n\n完整164条仍保存在原v5目录。eligible使用指向同一硬盘原文件的符号链接，是项目内筛选视图；不能脱离父目录当作独立便携副本。\n')
print(json.dumps({'activated':True,'active':145,'full_preserved':164,'environment_excluded':14,'short_success_excluded':5,'current_config':str(config_path.relative_to(BASE))},ensure_ascii=False))
