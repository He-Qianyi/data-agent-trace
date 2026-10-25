# 阶段D：事实、语义与来源合并

适用于完整模型输入输出的事实层，以及冻结后的B任务要求、C操作标注。不调用LLM，不运行轨迹中的代码，不重新评分，不修改原始引文或补充错误根因。

```sh
python3 -m pipeline.stages.trace_processor.merge_layer \
  --source outputs/stage_c_no_format_ten_20261018 \
  --output outputs/new_stage_d_run
```

输出目录必须不存在。来源哈希变化、结构错误、重复/缺失标注单元、证据包与事实不一致会终止运行。引用字符串或引用归属错误记录到具体引用与单元，不丢弃同包其他标注。该处理不修复引用，也不把非精确引文判为精确。

|字段|含义|
|---|---|
|`facts`|A阶段原JSON对象，逐字段保持一致；原始字节文件也归档。模型交互、工具参数/代码/结果、资源、版本、产物、最终回答和官方评估都在这里。内部`annotations.status=not_run`是A阶段的历史状态，后续标注放在外层`annotations`。|
|`annotations.task`|B阶段已经验证的任务整理结果，原样保留。目标、筛选、指标、单位、排序、产物要求等仍属于LLM解释。|
|`annotations.units`|全部C阶段原生标注，包括历史被整包拒绝的结果。仅添加步骤/交互关联、原生输出位置与机械检查状态；不改动操作、明细、要求关联或引文。|
|`evidence_catalog`|脚本提供给B/C的完整证据块，含事实位置、原始引用、表示类型与字符范围。通过确定性重建逐块核对。|
|`citation_index`|每个引文的标注JSON位置、精确匹配状态、所有匹配范围及问题。偏移是声明表示中的Unicode字符位置，不是原文件字节偏移。|
|`analysis_index`|步骤→模型交互、调用、标注操作和产物引用的索引。一个步骤仍是一次工具提交；同一交互内的展示顺序不等于串行执行。|
|`provenance`|原事实、原来源文件、B/C输入/输出/API记录及冻结合同的SHA256与随包相对路径。事实中的原路径保留，解析使用随包映射。|
|`quality`|覆盖、引文状态和原录制限制；不认证语义正确性或诊断准确率。|

`exact_unique`表示在声明证据块与允许范围内找到一次原文；`exact_multiple`保留所有候选，不任选一个当唯一位置；`unresolved`表示不能绑定。只在原证据块查找，不通过空白归一化、删行号、拼接片段、修改实体ID或换证据块来使引用通过。即使精确匹配，也不证明引文支持模型解释。

SQL文本、参数表达式和数据操作的静态位置保留在`facts.steps[*].action.static_extraction`。执行结果保留在该步骤`observation`。原记录没有逐条SQL结果绑定时，保留`result_scope=enclosing_tool_result_only`、`statement_execution_verified=false`，不臆造语句到结果行的数据血缘。文件写入候选、执行记录和运行后快照也保持原有区别。

## 下游验证

`downstream.py`适配旧版事件合同，实际调用`build_trace_level_evidence.build_evidence_sample`和`materialize`，输出完整事件、关系边、Evidence Builder结果及DuckDB/Parquet。所有模型交互都有完整来源引用，所有工具步骤有原参数、工具返回、静态提取、语义覆盖及引用状态。已有诊断事件读取器与评估读取器用于离线兼容检查；不调用其历史根因分类规则。

下游的`literal_citations`仅含精确且在允许范围内的引文；`unresolved_citations`单独保留。`semantic_units`中的文本仍是模型解释，不能因进入Evidence Builder而升级成事实。适配器只把录制层明确的失败信号标为事件失败；进程退出0不认证任务正确。评估仍是运行后信息；当前三个benchmark沿用旧读取器满分1的显式兼容政策，不改原分数。

```sh
python3 -m pipeline.stages.trace_processor.merge_layer.verify /path/to/copied_stage_d_bundle
python3 -m unittest tests.test_stage_d_merge -v
```

`verify`读取交付清单、JSON合同、归档事实与随包来源，重新验证所有来源及引用，无需原始挂载路径。完整记录表示已录下的输入输出完整；它不恢复原工具截断掉的内容、隐藏推理、未捕获的文件版本或未记录的数据依赖。
