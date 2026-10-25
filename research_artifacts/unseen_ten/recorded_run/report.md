新十条标准化轨迹：最后一版错误定位诊断流程

完成10条；最终JSON 6/10；CodeTracer兼容验收 6/10；服务失败 0 次。

输入来自当前 v5。排除历史诊断、取证试验及示例涉及的10条，从其余154条中按预先冻结规则抽取官方非满分轨迹。
采用最后实际运行的 v1-no-repeat-and-unlocalized-candidate；24轮、60次取证、max_tokens=48000、thinking_token_budget=16000、并发2、无自动重试或强制最终轮。全局配置不改动。
取证仅访问录制证据；官方评分、参考答案、评估反馈、运行后文件快照不发送。没有重执行轨迹中的代码。
机械验收不认证根因正确性；引用存在性与实际读过的检查单独保存，未做独立金标准准确率测量。

|数据集|Agent|任务|工具步骤|状态|模型轮数|取证次数|JSON验收|引用问题数|
|---|---|---|---:|---|---:|---:|---|---:|
|dabstep|claude_code|1753|22|invalid_json|5|8|False|0|
|dabstep|codex|49|6|mechanically_passed|2|2|True|0|
|dabstep|codex|1305|19|invalid_json|5|7|False|0|
|dabstep|kimi_code|1681|24|invalid_json|7|11|False|0|
|dataagentbench|claude_code|yelp__query2|7|mechanically_passed|2|2|True|0|
|dataagentbench|claude_code|GITHUB_REPOS__query1|14|mechanically_passed|5|6|True|0|
|dataagentbench|claude_code|agnews__query3|21|mechanically_passed|4|6|True|0|
|agenticdatabench|claude_code|agriculture_28|43|mechanically_passed|3|3|True|0|
|agenticdatabench|codex|agriculture_29|64|mechanically_passed|7|7|True|0|
|agenticdatabench|codex|agriculture_03|11|invalid_json|4|5|False|0|

模型响应 44 轮，只读取证 57 次。
逐条原生诊断位于 results/<record_id>/diagnosis.json；逐轮请求响应、取证、引用核查均保留。

原生输出包装核查（不修复输出、不更改验收）

- single_fenced_json_with_surrounding_text: 4
- native_json: 6

引用存在性/已读取核查：0 条有问题，共 0 处。此检查不认证引文是否支持诊断结论。
围栏中的诊断仅用于区分“没有诊断内容”和“诊断包装不合规”，不另存成通过验收的 diagnosis.json。详情见 output_review.json。
