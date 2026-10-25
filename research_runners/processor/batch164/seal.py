"""Seal exact contracts, code, reuse plan and previously approved review records."""
from pathlib import Path
import json
import shutil

from pipeline.stages.trace_processor.fact_layer.sources import digest
from pipeline.stages.trace_processor.merge_layer.build import save,read

BASE=Path.cwd();ROOT=BASE/'outputs/trace_processor_batch164_20261018';FREEZE=BASE/'outputs/trace_processor_frozen_v1_20261018'
sources=[*BASE.glob('pipeline/stages/trace_processor/merge_layer/*.py'),
         *BASE.glob('pipeline/stages/trace_processor/semantic_layer/*.py'),
         *BASE.glob('pipeline/stages/trace_processor/fact_layer/*.py'),
         *BASE.glob('work/trace_processor_batch164_20261018/*.py'),BASE/'tests/test_stage_d_merge.py',
         *[BASE/'scripts'/name for name in ('trace_processor_v2.py','probe_trace_processor_provider.py',
             'build_trace_level_evidence.py','run_trace_diagnostician_v3_baseline.py')]]
for p in sources:
    target=FREEZE/'source_code'/p.relative_to(BASE);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
policy=read(FREEZE/'protocol.json');policy.update(existing_authorized_review_replayed_without_new_edit=True,
    historical_C_records_reused=19,historical_C_units_reused=480,
    no_new_calls_for_complete_compatible_units=True,
    no_new_calls_for_original_D_ten=True,
    incomplete_output_policy='Missing/failed annotations remain explicit; all recorded facts and raw unsuccessful responses retained. No synthetic annotation to claim completion.')
save(FREEZE/'protocol.json',policy)
method='''# Trace Processor 冻结v1（2026-10-18）

本版本固定A事实提取、B任务要求整理、C数据操作语义标注、D校验/合并与下游输入适配。只处理已确认有完整模型输入输出的164条轨迹，不重新采集或评分。

- 新B调用：当前v4任务要求prompt与schema。
- 新C调用：撤去格式补充示例后的metric-scope版本，与10条验证的prompt逐字节一致。
- 接口：SoCLaaS `x-test-2`；预期返回 `deepseek-v4.1-flash-1017`。
- 参数：temperature 0；总输出48,000；thinking预算16,000；并发2；每包最多4个连续单元、32,000 UTF-8字节；不裁剪原证据。
- 不自动重试、修复、复核，不调整prompt或根据本批结果改规则。

## 复用边界

全部164条复用A事实。59条复用已验证B结果；同任务文本的待补B共86份新输入。19条轨迹共480个C单元按原生JSON内容复用。原10条D结果最终逐字节复制。

历史结果不是统一由新prompt生成：每份继承原prompt、模型和请求/响应哈希。只要求结构可兼容、事实证据一致、要求ID语义一致，不按分数或语义表现挑选。优先冻结版本，否则按已冻结的历史请求顺序选择。已有明确授权的任务要求修正连同原补丁保留，不退回原错误描述。

复用单元可从完整历史输出中逐项选择；不改写单元内容。原完整响应、选取位置和输入均保留；这不属于重跑或引文修复。相同任务文本只做一次新增B调用，分别重新绑定每条事实的来源位置。历史全部447份不同请求指纹保留，禁止发送字节等价请求。

## 质量与失败

D保留原事实与语义解释的区别。引文精确定位、多个候选和未绑定引用分开记录；不把文字存在当作语义正确。错误引用不导致同包全部语义丢失。结构错误/无JSON不伪造成功，未覆盖单元和失败响应保留。若B不可用，输出明确的不完整记录及全部可追溯事实。

运行完成后自动生成D、调用现有Evidence Builder与诊断读取器、生成查询存储并验证交付包。以上验证不认证错误诊断准确率。遇到服务/身份错误停止新增请求，保留已提交记录，不隐式重试。
'''
(FREEZE/'METHOD.md').write_text(method)
checks=read(BASE/'work/trace_processor_batch164_20261018/reuse_validation_final/checks.json')
save(FREEZE/'reuse_preflight_checks.json',checks)
save(FREEZE/'tests.json',{'suite':'tests.test_stage_d_merge','tests':10,'status':'passed',
     'native_request_fingerprint_matches':100,'historical_reuse_traces_checked':19,'native_reuse_units_checked':480})
files={str(p.relative_to(BASE)):digest(p) for p in sources}
for p in FREEZE.rglob('*'):
    if p.is_file() and p.name!='frozen_manifest.json':files[str(p.relative_to(BASE))]=digest(p)
for name in ('selected_traces.json','requirements_missing_groups.json','historical_request_signatures.json','reuse_ledger.json','plan.json','preserved_inputs.json'):
    p=ROOT/name;files[str(p.relative_to(BASE))]=digest(p)
save(FREEZE/'frozen_manifest.json',{'schema':'trace-processor-frozen-files-v1','files':files,
    'credential_files_copied':False,'immutable':True})
save(BASE/'configs/03_trace_processor/CURRENT_FROZEN.json',{'version':'trace-processor-frozen-v1-20261018',
    'path':str(FREEZE.relative_to(BASE)),'manifest_sha256':digest(FREEZE/'frozen_manifest.json'),
    'batch_path':str(ROOT.relative_to(BASE)),'old_164_batch_resumed':False})
print('sealed',len(files),'files')
