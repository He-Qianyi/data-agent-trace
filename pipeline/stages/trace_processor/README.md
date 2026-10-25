# Trace Processor

2026-10-19 当前事实层使用[版本化的 v2 适配器](fact_layer_v2/README.md)，修复连续换行后的脚本调用漏提取。
新建事实层使用 `python3 -m pipeline.stages.trace_processor.fact_layer_v2 --manifest … --output …`；
v1 入口保留用于冻结历史版本复现。当前完整结果与实现版本以
`configs/03_trace_processor/CURRENT_VALIDATION.json` 为准，当前结果包含 164 条，复用原有 B/C 标注。

2026-10-18 新增[阶段D合并与下游适配](merge_layer/README.md)：保留冻结事实、全部原生B/C标注，逐条限定引用状态，随包归档完整来源，并实际检查旧Evidence Builder、诊断读取器及查询存储。不改变历史prompt、整包验收或旧批次运行状态。

2026-10-17 新增完整模型输入输出轨迹的[录制格式适配器与事实层](fact_layer/README.md)。独立入口为 `python3 -m pipeline.stages.trace_processor.fact_layer --manifest … --output …`，只做脚本事实提取和来源校验，不运行语义标注。旧入口的行为保持如下。

默认模式只创建确定性的来源索引，不复制语义内容、不填事实或关系。配置在 `configs/03_trace_processor/source-index.json`。

语义阶段通过 `frozen-method.template.json` 显式指定已审查的entrypoint、SHA256、新attempt和请求预算。模板默认不可运行；不自动选择旧方法、不改prompt或字段、不恢复旧批次。参数必须通过该方法实际支持的CLI参数绑定，通用入口不会假装任意旧脚本都能改模型或预算。新输出在 `artifacts/03_trace_processor`。
