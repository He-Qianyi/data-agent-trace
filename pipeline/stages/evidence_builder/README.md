# 只读索引与按需取证 v0

输入是完整的阶段D交付目录（含`validation.json`、标准化JSON、来源`objects/`），不是原始录制目录。索引保留trace→step→unit、模型交互及已有任务要求链接；不推导因果边或完整DataFrame血缘。

```python
from pipeline.stages.evidence_builder.index import EvidenceIndex

index = EvidenceIndex('/path/to/stage_D_bundle')
packet = index.package(record_id, question, top_k=3, budget_bytes=32000)
# 若retrieval_complete_for_candidates为False，按deferred_pages继续读取；
# 或以更大的总budget_bytes取得同一候选集合的后续原文页。
```

`search`用C阶段未认证语义作导航，并结合原证据文本进行分字段BM25检索；英文标识符拆分与中文双字切分，不调用嵌入或LLM。默认搜索按单条轨迹限制，官方评估/最终答案/运行后快照不进入排名。没有命中不代表事实不存在。

|接口|内容与边界|
|---|---|
|`package` / `evidence_page`|实际原文页、字符范围、原来源及显式续读位置。按紧凑JSON的UTF-8字节预算，不静默截断。|
|`get_step`|一次工具提交的全部记录参数与返回，不增加执行次数。|
|`get_exchange`|完整已录请求与响应；可能很大，非默认检索包组成，不声称包含隐藏推理。|
|`neighbors`|记录顺序中的相邻步骤，不认证串行执行或因果依赖。|
|`requirement`|已有要求解释和候选操作链接，不认证满足；空链接不证明未工作。|
|`outcome`|任务、最终回复、产物和官方评估原记录；快照/评估为运行后信息，文件存在不认证agent创建或答案正确。|
|`citation`|阶段D引文状态原样返回，不通过检索结果修复原引文。|
|`sql_result_binding`|静态SQL/表达式及原记录的结果范围；无逐语句绑定时仅返回整个工具观察引用。|
|`runtime_variable`|本合同无结构化变量状态快照时返回未知；不会执行代码重建变量。打印预览可单独检索。|

复现本次试验（项目内）：

```sh
PYTHONPATH=. python3 work/evidence_index_pilot_20261018/run.py
PYTHONPATH=. python3 work/evidence_index_pilot_20261018/measure.py
PYTHONPATH=. python3 work/evidence_index_pilot_20261018/report.py
python3 -m unittest tests.test_evidence_index -v
```

固定20个问题及其位置验收先于索引和检索结果冻结；位置验收不参与排名。额外相同任务上下文的体积比较、替代证据人工说明独立记录，不更改原指定来源命中分数。没有独立根因金标准或诊断模型评分，不将检索命中率称作诊断准确率。
