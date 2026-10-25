# 第二阶段：先整理任务要求，再标注操作语义

## 新版阶段 C 的独立预算配置（2026-10-18）

新版命名中，任务要求整理为 B，操作语义为 C；下文 A/B 是历史内部命名。
下一轮新版 C 使用 `configs/03_trace_processor/stage-c-runtime.json`，通过
`StageCRuntime.load().client()` 构造客户端。该配置为 `x-test-2` 的 Chat 请求设置
`max_tokens=48000`、`thinking_token_budget=16000`，保留约 32000 tokens 给最终内容。
`reasoning_effort` 不另设；原有 temperature、JSON mode、并发、分包和重试规则保持原配置。

`provider.Client` 新增可选 `thinking_token_budget` 参数，并在 Chat HTTP JSON 顶层发送，
同时记入 receipt。该控制只对已验证的 `x-test-2` Chat 路径启用；省略参数时保留原请求格式。
历史已冻结的 run_config 不被覆盖，旧 `prepare/execute` 命令不会自动套用此新版 C 配置。
新 C 执行器应读取 profile，并把其中并发/分包值纳入新实验冻结配置，不恢复旧失败请求。

一次不含轨迹数据的极小请求已确认 48000/16000 两个参数进入实际请求并返回完整 JSON。
这不证明复杂轨迹绝不截断，也不认证受限推理后的语义质量；本次没有重跑三条轨迹。

输入为第一阶段冻结的完整模型 I/O 事实层。新的两阶段流程独立于历史 v2–v9 方法；不改原始轨迹或第一阶段输出，不执行轨迹中的代码，不重新评估，不运行错误诊断。

## A. LLM 整理任务要求

脚本从 `task.instruction` 获取完整原始 prompt，按物理文本行及长度建立可追溯文本块。只有原题进入此阶段：不提供工具调用、答案、文件快照、评估、task 元数据中的 gold/evaluator 字段。

LLM 输出任务概述、原子要求、原文证据、歧义和逐块归属。要求类型覆盖目标、数据来源、筛选、指标、分组、转换、统计/模型方法、可视化、输出文件/列/格式、约束和验证。每个题目文本块必须标为要求、上下文或歧义；标为要求的块必须被某个要求引用。

脚本验证 Schema、块覆盖和精确引文，再分配 `R0001` 等 requirement ID。同一完整 prompt 哈希只整理一次，不跨不同 prompt 合并。逐块归属检查不等于证明每个句内约束都正确、完整地被理解。

## B. LLM 标注操作语义

前一阶段的任务要求全部获得有效输出后，脚本构造工具步骤的证据视图：提交参数、工具原始文本观察、录制信号和产生本次调用的当前助手文本。当前助手文本只代表其声明的意图。不重新发送不断扩大的模型请求历史。

长字段按 UTF-8 字节限额无损分段；相邻证据片段在同一 Step 内组成单元。每个单元须有标注，每个 Step 至少对应一个单元。LLM 可以为一段代码识别多个数据操作；脚本按原 Step 汇集各单元，不将语义片段伪造为新的执行步骤，不用脚本猜测/合并语义相似操作。

操作类型覆盖数据发现、文档/表结构检查、读取、清洗、筛选、连接、聚合、指标计算、统计、建模、预测、绘图、导出、结果检查/验证、答案提交及脚本/环境操作。可补充数据对象、筛选条件、分组、度量、连接键、模型与输出的语义。全部是带证据的 LLM 解释，与事实层保留的原值分开。

每个操作记录：

- `type`、`description`、`basis=explicit|inferred|uncertain`。
- `phase=defined_in_code|submitted_for_execution|observed_in_result|inspection|unknown`。脚本定义、执行尝试和观察结果分别表达；代码出现不证明语句真正执行。
- `details`：可见证据支持的数据语义信息。
- `requirement_links`：只能引用已接受的 requirement ID，关系为 `targets/checks/produces_candidate_for/provides_context_for/uncertain`；不表达要求已经满足。
- `evidence`：模型复制的精确原文引文；脚本绑定来源字段、原始引用和字符位置。不明确的重复引文保留全部候选位置/计数。
- `limitations`：上下文、动态代码、截断、多模态内容未传入等限制。

观察类操作必须引用工具观察。每个操作至少引用本单元的一条证据，不能只靠上下文或别的 Step。不为 SQL 臆造独立结果，不将某个错误现象标成根因。

## 输入隔离

LLM 的请求白名单不包含 `evaluation`、`deliverables` 的运行后快照、`final_response`、task 元数据的评估函数/标准答案信息。三种格式的完整模型 I/O 仍由事实层的原始引用保留；语义标注器不需要重复读取请求历史。若原 agent 自身的可见工具输出含某种评估文字，它仍属于 agent 可见记录，不会因此获得外部官方评估数据。

非文本媒体不自动发送给本轮文本模型；没有看到图片的内容不得据此解释图像细节。`recording_signals` 是事实层状态的确定性显示，不表示任务成功。

## 运行与产物

在研究项目根目录：

```sh
python3 -m pipeline.stages.trace_processor.semantic_layer prepare \
  --facts outputs/fact_layer_v1_20261017 \
  --output outputs/semantic_layer_v1_20261017

python3 -m pipeline.stages.trace_processor.semantic_layer execute \
  --output outputs/semantic_layer_v1_20261017
```

可在 `prepare` 时显式选择 `--wire-protocol chat`。该模式使用官方主要入口 `/v1/chat/completions`，将原有 JSON Schema 原样附到 system contract，并使用 `response_format={"type":"json_object"}`；本地 Schema、引文和覆盖校验不变。完整原生 Chat 响应保留在调用目录，解析后统一进入同一标注校验器。协议固定在新批次配置中，不在失败后自动切换。

2026-10-17 的不含轨迹数据的最小对照发现：`x-test-2` 的 Responses 请求返回 `continuation_limit_exceeded`，错误正文明确指出 API-key 已保留会话额度；Chat Completions 请求返回 HTTP 200、模型 `deepseek-v4.1-flash` 和 `OK`。这项结果支持为独立标注调用选用不依赖该 Responses 状态缓存的 Chat 接口，不代表批量任务已经完成。

`prepare` 不调用模型，先冻结输入视图、原事实哈希、选择集、两个 prompt/Schema 和实现哈希。`execute` 沿用现有 SoCLaaS credential loader；凭据只由运行时在内存中读取，不进入请求审计文件或错误日志。默认 `qwen3.6:35b`、串行、单次输出预算 18,000 token、单请求超时 600 秒、整个新批次请求上限 1,200。任务整理完成前不启动操作标注。

`calls/` 留存真实请求、响应和凭据安全的 receipt；`requirements/` 保存已校验任务要求；`operations/` 保存已校验的操作单元；`traces/` 只写全部单元已完成的轨迹，将任务要求及 Step.annotations 加入第一阶段事实副本，并保留事实来源路径/哈希。没有成功响应时不生成替代标注。

任何 HTTP 错误、超时/解码错误、输出不完整/无效、Schema/引用/覆盖校验失败都会停止新请求，保留失败证据，不自动重试、切换协议/模型或修复重发。新批次的 `pause.requested` 可停止后续请求。失败 attempt 不接受隐式重新执行；原有历史批次不被恢复。

## 质量边界

脚本检查证明结构、覆盖计数和精确原文来源；不能证明 LLM 的解释正确、原子要求完整或标签准确。没有以另一个 LLM 的“通过”为充分保证。后续仍需独立人工抽样/金标准设计；本阶段输出明确 `semantic_correctness_certified=false`，不声称诊断能力或标注准确率。
