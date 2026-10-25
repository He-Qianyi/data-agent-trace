# 录制格式适配器与脚本事实层 v1

这是重新设计 Trace Processor 的第一步，仅处理已审计为完整模型输入输出、正常结束的原始轨迹。没有调用标注 LLM，没有执行轨迹中的 shell/Python/SQL，没有重新评估任务。此模块有独立入口，不修改旧 source-index、frozen semantic method、原轨迹或语料指针。

## 运行

在研究项目根目录运行：

```sh
python3 -m pipeline.stages.trace_processor.fact_layer \
  --manifest outputs/raw_trace_model_io_inventory_20261017/strict_complete_ended_manifest.jsonl \
  --output outputs/fact_layer_v1_20261017
```

输出目录必须不存在。`--limit N` 仅用于预检。未支持的录制格式、录制检查失败、来源哈希变化、缺少工具结果、重复模型调用 ID 或引用不一致，均会使该条失败；不会用默认结果补齐。输出 manifest 记录逐条状态，出现失败时命令返回非零值。

## 三个适配器

|适配器|依据的录制形状|模型交互|调用与结果|
|---|---|---|---|
|`anthropic-four-surface-relay-v1`|同一条记录包含 Anthropic 请求/响应及 Responses 转换前后四个表面|保留整个记录引用；规范入口指向 Anthropic 原请求、原响应|从响应抽取生成调用，以框架 assistant/user 事件及后续 request replay 识别提交/返回|
|`codex-native-serialized-v1`|`native_capture_type=codex_rollout_trace`|原生序列化 `model_input`、`model_output`|优先使用原生工具执行 ledger；没有该 sidecar 的旧录制保留 replay 证据并发出提示|
|`direct-responses-serialized-v1`|`native_capture_type=direct_serialized_fetch`|实际序列化请求、完整 Responses 响应|Responses 调用与框架 tool 消息配对，同时保留 replay 返回值|

选择依据是字段结构/录制版本，不是任务、数据集或 agent 名。`agent_id` 仅原样保留在元数据中。完整录制表示完整原请求/序列化请求与对应响应；不声称包含隐藏推理或所有 HTTP 头。

## 输出结构

每条 `traces/<record_id>.json` 包含：

|字段|内容与边界|
|---|---|
|`trace_metadata`|原始身份、适配器、录制类型、明确记录的模型与 cwd、审计中的完成证据|
|`task`|完整 prompt 及原始 task 元数据；不由脚本改写约束或分解目标|
|`model_exchanges`|每次请求/响应的完整来源引用、响应 ID、该响应生成的调用 ID|
|`steps`|一次提交的工具调用及其观察到的结果；包含失败的参数校验/工具/进程调用|
|`generated_calls_without_observed_submission`|有模型生成记录但没有观察到提交的调用；不能计为执行步骤|
|`native_internal_tool_records`|无模型可见调用 ID 的内部执行记录；不伪造 agent 步骤|
|`data_resources`|任务明确声明的资源、已有 input manifest、代码 API 中可静态定位的路径；不声称读取成功|
|`script_revisions`|原文中的代码定义/编辑及有限的静态重建；不声称是执行时的文件快照|
|`deliverables`|声明/写入尝试/静态写入 API 指向的文件候选、实际保留的运行后文件快照与哈希；包括中间脚本，不能全部当成最终答案|
|`final_response`|runner 明确保存的最终文本；缺少时保留最后助手消息候选及其选择依据|
|`evaluation`|已有官方评估原文和明确引用的明细；不重跑、不解释分数；标明不是 agent 当时可见信息|
|`sources`|原文件路径、角色、字节数、SHA256、是否仍存在|
|`quality`|录制检查、引用/原文一致性检查、已保留差异和限制；不认证分析语义正确|

`annotations.status=not_run` 为后续语义层预留状态，当前不填目的、数据含义、错误类型或根因。

### Step 与原文的关系

一个模型响应可能生成多个调用，一个工具调用可能包含多条 SQL/Python 语句。Step 仅以调用 ID 去重，不因后续请求重复携带历史记录而增加。展示顺序是模型响应顺序及内容块顺序，不推断同一响应中的工具串行执行。

`action.arguments_raw` 保留记录中的参数原值（JSON 字符串或对象）；`arguments` 是确定性 JSON 解码结果。不同表面的空格等序列化差异放在 `serialization_variants`。若模型生成参数与框架提交参数不同，同时保留 `generated_call`、所有 `submission_variants` 与来源；优先使用框架提交记录提取动作，提示 `call_argument_surfaces_differ`。不静默把生成命令当作执行命令。

`observation.variants` 分开保留原生返回、框架返回、request replay。优先级为原生→框架→replay，只决定便于阅读的首选值，不覆盖其他值。错误包装不同仍可追溯。`tool_status_as_recorded`、`is_error_as_recorded`、`exit_code_as_recorded` 相互独立；没有退出码就保留 null。不会把“原生工具调用 completed”当成“被调用进程 exit 0”。多模态内容块不扁平化丢弃；`text` 只是额外的文本视图。

### Data Agent 信息如何抽取

- 完整 shell 命令无损保留；识别有限的 heredoc、Python `-c`、Python 脚本调用、sqlite3/duckdb CLI。解析失败保留原命令，不执行以求补全。
- Python 用 AST 定位文件读取/写入、连接、SQL API、groupby/merge/quantile 等明确语法；记录 API 拼写、原表达式、行号及可直接取得的字面量。API 拼写不等于完成了语义分类，也不证明该语句运行。
- SQL 保存可确定的原字符串/静态常量；动态拼接无法确定时保留表达式，`sql_text=null`，不展开 f-string 或执行查询。
- SQL 的结果引用只到所在工具的原始返回，`individual_result_binding=not_recorded_separately` 或 `result_scope=enclosing_tool_result_only`。多个查询共享 stdout 时，不将某段输出臆配为某条 SQL 的独立结果。
- 写脚本与执行脚本分开；只有已知之前的定义、且明确编辑片段可匹配时才静态重建。运行关联只表示较早记录的路径候选，不认证执行时内容、持久化成功、并行时序、目录变化或环境展开。
- CSV 快照可直接记录存储列名和行数。它是运行后当前保留文件的测量，不回填为 agent 当时看到的结果，不推断列类型、指标意义或最终交付成功。
- 静态识别不是覆盖所有任意程序的解析器；未识别的调用完整参数/返回仍在事实层中，不能把 `data_operations=[]` 解读为没有数据操作。

## 来源定位与重建

`sources` 注册到文件级，模型完整 I/O 通过引用保留，不把不断重复的整个上下文再次复制进每个 Step。本包依赖原始外置磁盘；保留引用不等于生成一个脱离原文件的自包含数据集。

引用包含 `source_id`、可选的 **1 起始物理行号** `line`、RFC 6901 `json_pointer`。JSONL 按实际 LF 行解析，不能使用 `str.splitlines()`，否则 JSON 字符串内的 U+2028/U+0085 等字符会被错误分行。某些 JSONL 行是数组，pointer 先含数组索引。文本文件使用 `representation=text`。

JSON 编码参数中的子字段另外带 `decode_json_string=true` 与 `decoded_pointer`；源代码片段可带字符区间 `[char_start,char_end)`，区间作用于已解码字段。`shell_quoted_argument_decoding` 表示 shlex 解码后的词法 token，不冒充执行后/环境展开后的程序字节。

可重新打开输出中的来源注册表并检查哈希，然后恢复完整请求：

```python
import json
from pipeline.stages.trace_processor.fact_layer.sources import Sources

trace = json.load(open("outputs/fact_layer_v1_20261017/traces/RECORD_ID.json"))
sources = Sources.from_entries(trace["sources"], verify_hashes=True)
original_request = sources.resolve(trace["model_exchanges"][0]["request_ref"])
```

## 检查能保证什么

每次构建重跑已有的只读录制检查，核对独立检查器给出的模型交互/工具调用计数；校验全部引用可解析、参数与返回值等于原文、直接截取的代码片段等于来源区间；构建前后复核已注册文件哈希。输入 manifest 与实现哈希保存在 `run_config.json`，逐条结果文件哈希保存在 `manifest.jsonl`。

离线 fixture 测试覆盖三种格式、重复历史、生成未提交、参数变体、原生参数错误、输出包装差异、进程失败、截断、代码定义与执行、动态 SQL、官方零分与运行后文件快照。已有手工示例只用于回归核对，不作为独立金标准，当前结果不能据此宣称语义召回率/诊断准确率。

## 扩展方式

新增录制格式应增加独立适配器及 fixture，明确 request/response 与提交/结果的来源，不增加任务 ID 特判。当前 CLI 不提供上下文级旧轨迹的降级入口，也不自动调用 LLM 补字段。
