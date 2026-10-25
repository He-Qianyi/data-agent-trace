"""Switch current pointer only after the full merge, source, DB and graph checks."""
from pathlib import Path
import os,sys,datetime,shutil,json
R=Path(__file__).resolve().parents[2];sys.path.insert(0,str(R));assert os.path.ismount('/Volumes/Public HD')
from pipeline.stages.trace_processor.merge_layer.build import read,save,checked
from pipeline.stages.trace_processor.fact_layer.sources import digest
P=R/'artifacts/03_trace_processor/integrated_runs/trace_processor_connection_positional_v5_20261020';rel=str(P.relative_to(R));N=P/'final';cfg=R/'configs/03_trace_processor/CURRENT_VALIDATION.json';parent=read(P/'parent_current_validation.json');s=read(P/'summary.json');v=read(P/'postmerge_validation.json')
assert read(cfg)==parent and s['status']=='validated_not_yet_activated' and v['status']=='passed'
assert s['counts']['traces']==164 and s['known_null_fields_recovered']==26 and s['changed_final_traces']==2 and s['unchanged_final_traces']==162
assert read(N/'portable_verification.json')['status']=='passed' and read(N/'downstream/static_fact_validation.json')['status']=='passed' and read(P/'postmerge_checks/scan_summary.json')['relationship_issues']==0
assert all(x['success'] for x in read(P/'test_results.json')) and sum(x['tests'] for x in read(P/'test_results.json'))==104
assert not v['constant_positional_connection_fields_still_missing'] and v['prior122_preserved']==122 and v['prior709_connections_preserved']==709 and v['prior32_preserved']==32
for name,h in read(P/'facts/run_config.json')['implementation_sha256'].items():checked(R/'pipeline/stages/trace_processor/fact_layer_v5'/name,h)
for path,h in read(P/'protected_implementations.json').items():checked(R/path,h)
for row in read(R/parent['normalized_results']/'validation.json'):checked(R/parent['normalized_results']/row['trace_path'],row['trace_sha256'])
for row in read(N/'validation.json'):checked(N/row['trace_path'],row['trace_sha256'])
for dirname,source in [('implementation',R/'pipeline/stages/trace_processor/fact_layer_v5'),('reproduction_scripts',R/'work/connection_positional_v5_20261020')]:
 out=P/dirname;out.mkdir(exist_ok=False)
 for p in source.iterdir():
  if p.is_file() and not p.name.startswith('._') and p.suffix in {'.py','.json','.md'}:shutil.copyfile(p,out/p.name)
oldrun=R/parent['fact_layer_upgrade'];shutil.copyfile(oldrun/'known709_connections.json',P/'known709_connections.json');shutil.copyfile(oldrun/'known122_recovery.json',P/'prior122_recovery.json')
current={**parent,'normalized_results':rel+'/final','summary':rel+'/summary.json','previous_normalized_results':parent['normalized_results'],'fact_layer_upgrade':rel,'fact_layer_implementation':'pipeline.stages.trace_processor.fact_layer_v5','fact_layer_implementation_version':s['implementation_version'],'scope':'General bounded resolution of the first positional database connection argument recovered26 known NULL fields (13 connection paths and13 resource copies). All164 rebuilt and independently validated;2 final traces updated,162 unchanged. No new calls; all4259 semantic units and citations unchanged; reasonable nulls and previous repairs preserved; no model calls.','updated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'null_recovery_validation':rel+'/known26_recovery.json','connection_recovery_validation':rel+'/known709_connections.json','conservative_null_validation':rel+'/reasonable_null_preservation.json','postmerge_validation':rel+'/postmerge_validation.json','downstream_fact_validation':rel+'/final/downstream/static_fact_validation.json'}
save(P/'CURRENT_VALIDATION.activated.json',current);tmp=cfg.with_name('CURRENT_VALIDATION.json.next');assert not tmp.exists();tmp.write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,cfg)
s.update(status='completed',activated_at_utc=current['updated_at_utc'],current_pointer=str(cfg.relative_to(R)),prior32_still_recovered=32,prior122_still_recovered=122,prior709_connections_still_recorded=709,semantic_changes=0,nulls_before=v['null_total_before'],nulls_after=v['null_total_after'],all_other_null_patterns_identical=True,constant_positional_connection_fields_still_missing=0);save(P/'summary.json',s)
save(P/'activation.json',{'status':'active','previous_config_sha256':digest(P/'parent_current_validation.json'),'current_config_sha256':digest(cfg),'previous_results':parent['normalized_results'],'current_results':current['normalized_results'],'old_versions_preserved':True,'activation_after_complete_validation':True})
def nav(name,text):
 p=R/name
 if p.exists():
  b=P/'navigation_before'/name;b.parent.mkdir(parents=True,exist_ok=True);b.write_bytes(p.read_bytes())
 p.write_text(text)
for name in ['README.md','docs/overview/current-layout.md','artifacts/03_trace_processor/README.md']:
 p=R/name;nav(name,p.read_text().replace(parent['normalized_results'],rel+'/final').replace('integrated_runs/trace_processor_constant_connection_v4_20261020/final/','integrated_runs/trace_processor_connection_positional_v5_20261020/final/'))
idx=read(R/'PROJECT_INDEX.json');idx.update(latest_structured_traces=rel+'/final',previous_structured_traces=parent['normalized_results'],current_processed_trace_configuration='configs/03_trace_processor/CURRENT_VALIDATION.json',latest_fact_layer_implementation=current['fact_layer_implementation'],latest_fact_layer_validation=rel+'/README.md');nav('PROJECT_INDEX.json',json.dumps(idx,ensure_ascii=False,indent=2)+'\n')
p=R/'artifacts/03_trace_processor/integrated_runs/README.md';nav(str(p.relative_to(R)),p.read_text()+'\n## 当前连接首参数修复版本\n\n- [trace_processor_connection_positional_v5_20261020/](trace_processor_connection_positional_v5_20261020/README.md)：26处常量连接URI与资源副本通用修复，164条重新合并和独立验证通过。\n')
readme='''# 数据库连接首位置参数通用修复（2026-10-20）

状态：已完成并切换当前入口。实现：`fact-layer-connection-positional-v5-20261020`。

## 结果

- 已确认26处遗漏全部恢复：13个数据库连接`path_literal`及13个资源目录`path_as_recorded`副本，涉及2条轨迹。
- stockindex__query3：3个连接字段、3个资源副本；stockmarket__query2：10个连接字段、10个资源副本。重复定义/候选修订上下文不是新增工具执行。
- 从同一份原始源清单重建全部164条；2条最终轨迹更新，162条JSON与父版逐字节相同。
- 全部事实差异严格限于上述26个值和13条常量推导说明；其他原字段、顺序、原始输入输出、脚本关系、官方评估及产物完全一致。
- 164条任务和操作LLM证据包完全一致，4259个语义单元、证据目录和引文均原样保留。没有调用LLM或执行捕获的SQL/Python/Shell。
- 104项测试通过；全部164条独立来源校验通过，主要层级引用无新增错误；实际下游数据库读回的4208步完整静态字段保持值、类型及顺序。
- 原32处、122处修复和709处连接均重新检查仍有效；53处保守脚本版本关联null保留。语义问题暂未修改。

## 通用规则

对已识别的数据库连接，首位置参数优先。复用v4已有的受限源顺序字符串解析，支持已知常量、字符串拼接、受限f-string及已导入的路径拼接；不依赖任务名、数据库文件名或示例值。

完整URI/DSN原样保留，包括`file:`前缀、百分号编码、Unicode与查询参数；不解析或执行连接字符串，不认证实际连接成功。`engine_verified`和`statement_execution_verified`仍为false，原表达式与参数字段保留。

首参数存在却无法确定时继续null，不从关键字借一个值代替；没有首参数时，沿用原来的database/dsn关键字规则。只有dbname的PostgreSQL连接不被伪造成文件路径。资源目录通过既有通用映射同步该值。

## 验证与合理null

新增9项合成边界测试覆盖：常量URI、导入别名和路径拼接、重复赋值、PostgreSQL DSN、首参数优先及未知时不回退、分支/循环/局部与运行时值、格式说明限制、关键字连接行为、URI编码和查询参数保真。原95项测试重跑通过，旧测试文件没有修改。

全部新增且可受限解析的13个连接首参数均有值，没有同类遗漏。全量null仅发生两个模式变化：操作路径减少13，资源路径减少13；64,779→64,753，其余null模式计数全部相同。非适用、动态或保留字段没有猜填。

完整结果包校验通过；DuckDB允许补缺省可选空键，但所有原字段的值、类型、数组顺序必须保持。没有新增工具步骤或连接调用。机械验证不代表全部语义标注或后续诊断100%正确。

## 入口与证据

当前结果：[final/traces/](final/traces/)。唯一入口为项目`configs/03_trace_processor/CURRENT_VALIDATION.json`，父版v4及更早版本完整保留。

- [26处恢复明细](known26_recovery.json)
- [全部事实差异与输入兼容](comparison_summary.json) · [输入逐条对照](annotation_compatibility.json)
- [104项测试](test_results.json)
- [合并后精确核验](postmerge_validation.json) · [首参数全量检查](positional_connection_checks.json)
- [全量层级复查](postmerge_checks/scan_summary.json)
- [独立来源校验](final/portable_verification.json)
- [下游静态字段读回](final/downstream/static_fact_validation.json)
- [切换记录](activation.json)

实现与运行脚本快照分别保存于`implementation/`与`reproduction_scripts/`；原始源清单为`source_manifest.jsonl`，全部运行日志保留。冻结原始记录、旧实现、历史提示词及其哈希没有覆盖。
'''
nav(rel+'/README.md',readme)
assert read(cfg)['normalized_results']==rel+'/final' and len([p for p in (N/'traces').glob('*.json') if not p.name.startswith('._')])==164
print(json.dumps({'status':'completed','recovered':26,'updated':2,'unchanged':162,'traces':164,'tests':104,'current_results':rel+'/final'},ensure_ascii=False,indent=2))
