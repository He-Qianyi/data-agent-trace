"""Durable, resumable missing-work-only B/C batch, followed by automatic D.

No retry, repair, benchmark replay, or evaluation rerun. Start only after seal.py.
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile

BASE=Path(__file__).resolve().parents[2];sys.path.insert(0,str(BASE))
from pipeline.stages.trace_processor.semantic_layer.stage_c_runtime import StageCRuntime
from pipeline.stages.trace_processor.semantic_layer.provider import ProviderFailure
from pipeline.stages.trace_processor.semantic_layer.evidence import batches, encode, requirement_input
from pipeline.stages.trace_processor.semantic_layer.validate import requirements, schema_check
from pipeline.stages.trace_processor.fact_layer.sources import digest
from pipeline.stages.trace_processor.merge_layer.build import Bundle, merge_one, validate_final, checked, bundled_sources
from pipeline.stages.trace_processor.fact_layer.build import validate as validate_facts
from pipeline.stages.trace_processor.merge_layer.downstream import export as export_downstream
from pipeline.stages.trace_processor.merge_layer.verify import verify as verify_delivery

ROOT=BASE/'outputs/trace_processor_batch164_20261018'
FREEZE=BASE/'outputs/trace_processor_frozen_v1_20261018'
TEN_D=BASE/'outputs/stage_d_ten_20261018'
MIRROR=Path('${LOCAL_MIRROR}/outputs/trace_processor_batch164_20261018')


def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.writing');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
def signature(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def compact(value):return [{k:r[k] for k in ('requirement_id','kind','description','basis')} for r in value['requirements']]
def timestamp():return datetime.now(timezone.utc).isoformat()
def verify_freeze():
    for path,sha in read(FREEZE/'frozen_manifest.json')['files'].items():checked(BASE/path,sha)
    assert (BASE/'outputs/semantic_layer_deepseek_chat_v1_20261017/pause.requested').exists()


def progress(**values):
    p=ROOT/'progress.json';current=read(p) if p.exists() else {}
    current.update(values,updated_at_utc=timestamp(),worker_pid=os.getpid())
    write(p,current)
    # User-facing live snapshot; raw task material stays in the existing project.
    MIRROR.mkdir(parents=True,exist_ok=True)
    write(MIRROR/'progress.json',current)
    report=['# Trace Processor · 164条批量处理', '',
        f"更新时间：{current['updated_at_utc']}", '',f"状态：**{current.get('status')}**；阶段：**{current.get('stage')}**。", '',
        '已完成的兼容标注按来源复用，不重新调用模型。历史版本单独登记；新增调用使用冻结v1配置。', '',
        '|项目|数量|','|---|---:|']
    labels={'B_reused_traces':'复用B轨迹','C_reused_units':'复用C单元','B_new_calls_finished':'新增B调用已返回',
        'B_new_calls_planned':'新增B调用计划','B_ready_traces':'B结果可用轨迹','C_new_calls_finished':'新增C调用已返回',
        'C_new_calls_planned':'新增C调用计划','C_new_units_schema_valid':'新增C结构有效单元','D_finished_traces':'D已生成轨迹',
        'D_complete_traces':'D标注覆盖完整轨迹','D_incomplete_traces':'D仍有缺失轨迹'}
    for key,label in labels.items():
        if key in current:report.append(f"|{label}|{current[key]}|")
    report.extend(['','本页会由批处理程序更新。未显示为完成前，不代表164条已经全部完成。', '',
        '不自动重试、不修复模型引文、不执行轨迹代码、不重跑官方评估。完整原始事实始终保留。', ''])
    if current.get('active'):report.extend(['当前活动请求：', '', '```json',json.dumps(current['active'],ensure_ascii=False,indent=2),'```',''])
    if current.get('stop_reason'):report.extend(['停止原因：'+str(current['stop_reason']),''])
    if current.get('stage')=='finished':report.extend(['[最终验收报告](report.md)','[最终逐轨迹检查](final/validation.json)',''])
    temp=MIRROR/'status.md.writing';temp.write_text('\n'.join(report));temp.replace(MIRROR/'status.md')
    print(json.dumps({k:v for k,v in current.items() if k!='plan'},ensure_ascii=False),flush=True)


def packet_payload(prompt,data,schema):
    rt=read(FREEZE/'runtime_profile.json')
    return {'model':rt['model'],'messages':[{'role':'system','content':prompt+'\nOUTPUT JSON SCHEMA (structural contract):\n'+encode(schema)},
        {'role':'user','content':'UNTRUSTED ANNOTATION INPUT DATA:\n'+encode(data)+'\nApply the system contract and return JSON only.'}],
        'max_tokens':rt['max_tokens'],'temperature':0,'response_format':{'type':'json_object'},'thinking_token_budget':rt['thinking_token_budget']}


def ask(stage,data,folder):
    verify_freeze()
    prompt=(FREEZE/('requirements.compiled.md' if stage=='B' else 'compiled_system_prompt.md')).read_text()
    schema=read(FREEZE/('requirements.schema.json' if stage=='B' else 'operations.schema.json'))
    sig=signature(packet_payload(prompt,data,schema))
    if sig in set(read(ROOT/'historical_request_signatures.json')):
        raise ValueError('duplicate_historical_request_not_sent')
    # Cross-thread and process-safe reservation precedes the network request.
    reservation=ROOT/'request_reservations'/sig
    reservation.parent.mkdir(exist_ok=True)
    fd=os.open(reservation,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(json.dumps({'stage':stage,'directory':str(folder),'reserved_at':timestamp()}))
    client=StageCRuntime.load(FREEZE/'runtime_profile.json').client()
    native,receipt=client.ask(prompt,data,schema,folder)
    write(folder.parent/('requirements_native.json' if stage=='B' else 'llm_output.json'),native)
    if receipt['returned_model']!='deepseek-v4.1-flash-1017':raise ValueError('unexpected_returned_model:'+str(receipt['returned_model']))
    schema_check(native,schema)
    return native,receipt


def b_call(job):
    rid=job['record_ids'][0];shared=ROOT/'shared'/rid
    result={'stage':'B','group_id':job['group_id'],'record_ids':job['record_ids'],'status':'unknown'}
    try:
        if signature(read(shared/'requirements_input.json'))!=job['group_id']:raise ValueError('frozen_B_input_changed')
        native,receipt=ask('B',read(shared/'requirements_input.json'),shared/'requirements_call')
        accepted=requirements(native,read(shared/'task_evidence.json'))
        write(shared/'requirements_accepted.json',accepted)
        for other in job['record_ids'][1:]:
            dest=ROOT/'shared'/other
            write(dest/'requirements_native.json',native)
            write(dest/'requirements_accepted.json',requirements(native,read(dest/'task_evidence.json')))
            shutil.copytree(shared/'requirements_call',dest/'requirements_call')
            write(dest/'requirements_reuse.json',{'source_request':str(shared/'requirements_call/request.json'),
                'source_request_sha256':digest(shared/'requirements_call/request.json'),'source_response_sha256':digest(shared/'requirements_call/response.json'),
                'source_prompt_sha256':digest(FREEZE/'requirements.compiled.md'),'current_prompt_match':True,
                'native_semantics_unchanged':True,'citations_rebound_to_this_fact_sources':True,'llm_calls':0})
        result.update(status='passed',returned_model=receipt['returned_model'])
    except ProviderFailure as exc:result.update(status='provider_failure',failure=exc.metadata)
    except Exception as exc:result.update(status='validation_failure',reason=type(exc).__name__+':'+str(exc)[:1000])
    write(ROOT/'B_results'/f"{job['group_id']}.json",result)
    return result


def c_call(job):
    folder=ROOT/job['directory'];result={**job,'stage':'C','status':'unknown'}
    try:
        checked(folder/'input.json',job['input_sha256'])
        native,receipt=ask('C',read(folder/'input.json'),folder/'call')
        if [u['unit_id'] for u in native['units']]!=job['target_units']:raise ValueError('unit_order_or_coverage')
        result.update(status='native_schema_valid',returned_model=receipt['returned_model'],units=len(native['units']))
    except ProviderFailure as exc:result.update(status='provider_failure',failure=exc.metadata)
    except Exception as exc:result.update(status='validation_failure',reason=type(exc).__name__+':'+str(exc)[:1000])
    write(folder/'result.json',result)
    return result


def halt_reason(result):
    if result.get('reason','').startswith('ValueError:unexpected_returned_model'):return result['reason']
    if result['status']=='provider_failure':
        meta=result['failure']
        if meta.get('reason')!='provider_annotation_output_unusable':return meta
    return None


def pool_execute(stage,jobs,worker,result_path):
    results=read(result_path) if result_path.exists() else []
    key=(lambda j:j['group_id']) if stage=='B' else (lambda j:j['directory'])
    existing={key(r) for r in results}
    todo=[j for j in jobs if key(j) not in existing]
    # Never re-enter a submitted call whose result wasn't committed: retain it
    # for offline recovery instead of risking duplicate billed requests.
    for j in todo:
        call=ROOT/'shared'/j['record_ids'][0]/'requirements_call' if stage=='B' else ROOT/j['directory']/'call'
        if call.exists():raise RuntimeError('Existing in-flight/unfinished call requires offline recovery: '+str(call))
    pending={};index=0;stop=None
    with ThreadPoolExecutor(max_workers=2) as pool:
        while pending or (index<len(todo) and not stop):
            if (ROOT/'pause.requested').exists():stop='user_pause_requested'
            while not stop and len(pending)<2 and index<len(todo):
                job=todo[index];index+=1;pending[pool.submit(worker,job)]=job
            active=[{'stage':stage,'task':j.get('task_id'),'record_id':j.get('record_id'),
                     'group_id':j.get('group_id'),'packet':j.get('packet')} for j in pending.values()]
            progress(stage=stage,status='running',active=active,**{stage+'_new_calls_planned':len(jobs),stage+'_new_calls_finished':len(results)})
            done,_=wait(pending,timeout=30,return_when=FIRST_COMPLETED) if pending else (set(),set())
            for future in done:
                pending.pop(future);result=future.result();results.append(result);write(result_path,results)
                reason=halt_reason(result)
                if reason:stop=reason
            if stage=='C' and done:
                progress(C_new_units_schema_valid=sum(r.get('units',0) for r in results if r['status']=='native_schema_valid'))
    if stop:
        progress(stage=stage,status='stopped',active=[],stop_reason=stop)
        return False
    progress(stage=stage,status='phase_finished',active=[],**{stage+'_new_calls_planned':len(jobs),stage+'_new_calls_finished':len(results)})
    return True


def prepare_c():
    if (ROOT/'C_jobs.json').exists():return read(ROOT/'C_jobs.json')
    rows=read(ROOT/'selected_traces.json');all_jobs=read(ROOT/'jobs.json');new_jobs=[];unavailable=[]
    for row in rows:
        shared=ROOT/'shared'/row['record_id']
        if not (shared/'requirements_accepted.json').exists():
            unavailable.append({'record_id':row['record_id'],'stage':'B','reason':'requirements_not_accepted'});continue
        view=read(shared/'operation_evidence.json');taken={uid for j in all_jobs if j['record_id']==row['record_id'] for uid in j['target_units']}
        checked(shared/'operation_evidence.json',row['operation_view_sha256'])
        remaining={**view,'units':[u for u in view['units'] if u['unit_id'] not in taken]}
        try:packets=list(batches(remaining,compact(read(shared/'requirements_accepted.json')),max_bytes=32000,max_units=4))
        except ValueError as exc:
            unavailable.append({'record_id':row['record_id'],'stage':'C','reason':'frozen_packet_budget:'+str(exc)});continue
        offset=len([j for j in all_jobs if j['record_id']==row['record_id']])
        for n,packet in enumerate(packets,offset+1):
            folder=ROOT/'cases'/row['record_id']/'packets'/f'{n:04d}';write(folder/'input.json',packet)
            job={'record_id':row['record_id'],'task_id':row['task_id'],'packet':n,'directory':str(folder.relative_to(ROOT)),
                 'input_sha256':digest(folder/'input.json'),'target_units':[u['unit_id'] for u in packet['target_units']],'reused':False}
            new_jobs.append(job)
    write(ROOT/'jobs.json',all_jobs+new_jobs);write(ROOT/'C_jobs.json',new_jobs);write(ROOT/'unavailable.json',unavailable)
    progress(stage='C_prepared',B_ready_traces=sum((ROOT/'shared'/r['record_id']/'requirements_accepted.json').exists() for r in rows),
             C_new_calls_planned=len(new_jobs),C_new_calls_finished=0)
    return new_jobs


def d_stage():
    verify_freeze();final=ROOT/'final';final.mkdir(exist_ok=True);bundle=Bundle(final)
    schema=read(FREEZE/'normalized_trace.schema.json');write(final/'normalized_trace.schema.json',schema)
    frozen_contracts={p.name:bundle.add(p) for p in FREEZE.iterdir() if p.is_file()}
    reports=[];blocked=[]
    prior={r['record_id']:r for r in read(TEN_D/'validation.json')}
    all_jobs=read(ROOT/'jobs.json')
    for row in read(ROOT/'selected_traces.json'):
        rid=row['record_id'];relative=Path('traces')/(rid+'.json')
        try:
            if rid in prior:
                # These ten final outputs are copied byte for byte, including
                # their original per-packet provenance. No stages rerun.
                for p in (TEN_D/'objects').iterdir():
                    dest=final/'objects'/p.name
                    if not dest.exists():dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest)
                dest=final/relative;dest.parent.mkdir(exist_ok=True);shutil.copyfile(TEN_D/prior[rid]['trace_path'],dest)
                trace=read(dest);origin='reused_final_D_byte_for_byte'
                if digest(dest)!=prior[rid]['trace_sha256']:raise ValueError('Existing D changed during copy')
            else:
                shared=ROOT/'shared'/rid
                if not (shared/'requirements_accepted.json').exists():raise ValueError('B_unavailable_no_normalized_semantic_trace_invented')
                valid=[];failed=[]
                for job in [j for j in all_jobs if j['record_id']==rid]:
                    result=read(ROOT/job['directory']/'result.json')
                    if result['status'] in ('native_schema_valid','reused_native_schema_valid'):valid.append(job)
                    else:failed.append(job)
                trace=merge_one(row,ROOT,valid,bundle,read(FREEZE/'operations.schema.json'),allow_missing=True)
                trace['provenance']['frozen_contracts']=frozen_contracts
                trace['provenance']['unsuccessful_packets']=[{'packet':j['packet'],'files':[bundle.add(p) for p in (ROOT/j['directory']).rglob('*.json')]} for j in failed]
                write(final/relative,trace);origin='merged_frozen_batch'
            schema_check(trace,schema)
            validation=validate_final(trace,final,read(row['fact_path']));validation['normalized_schema']='passed'
            reports.append({'record_id':rid,'benchmark':row['benchmark'],'agent_id':row['agent_id'],'task_id':row['task_id'],
                'trace_path':str(relative),'trace_sha256':digest(final/relative),'origin':origin,'quality':trace['quality'],'validation':validation})
        except Exception as exc:
            # The original facts remain available even if a semantic stage was
            # unusable. This is a visibly incomplete record, not a fabricated pass.
            fact_file=bundle.add(row['fact_path'],row['fact_sha256'])
            facts=read(row['fact_path'])
            source_files=[{'source_id':s['source_id'],**bundle.add(s['path'],s['sha256'])} for s in facts['sources'] if s['exists']]
            failed_folder=ROOT/'shared'/rid
            retained=[bundle.add(p) for p in failed_folder.rglob('*.json')]
            incomplete={'schema_version':'data-agent-stage-d-incomplete-v1','facts':facts,
                'provenance':{'fact_file':fact_file,'source_files':source_files,'annotation_files':retained},
                'quality':{'status':'incomplete_annotations','reason':type(exc).__name__+':'+str(exc)[:1000],
                    'semantic_correctness_certified':False,'fact_mutation_performed':False}}
            validate_facts(facts,bundled_sources(incomplete,final))
            incomplete_path=Path('incomplete')/(rid+'.json');write(final/incomplete_path,incomplete)
            blocked.append({'record_id':rid,'task_id':row['task_id'],'reason':incomplete['quality']['reason'],
                            'trace_path':str(incomplete_path),'fact_file':fact_file,'annotation_files':retained,
                            'complete_facts_source_validation':'passed'})
        write(final/'validation.json',reports);write(final/'blocked_traces.json',blocked)
        complete=sum(r['quality'].get('units_retained')==next(x['units'] for x in read(ROOT/'selected_traces.json') if x['record_id']==r['record_id']) for r in reports)
        progress(stage='D',status='running',D_finished_traces=len(reports),D_complete_traces=complete,
                 D_incomplete_traces=len(reports)-complete+len(blocked),active=[])
    write(final/'input_manifest.json',list(bundle.inputs.values()))
    if reports:export_downstream(final)
    summary={'status':'completed' if len(reports)==164 and all(r['quality'].get('units_retained')==next(x['units'] for x in read(ROOT/'selected_traces.json') if x['record_id']==r['record_id']) for r in reports) else 'completed_with_incomplete_annotations',
        'total_traces':164,'normalized_traces':len(reports),'blocked_traces':len(blocked),
        'complete_annotation_traces':complete,'missing_units':sum(next(x['units'] for x in read(ROOT/'selected_traces.json') if x['record_id']==r['record_id'])-r['quality']['units_retained'] for r in reports),
        'original_D_ten_unchanged':all(r['trace_sha256']==prior[r['record_id']]['trace_sha256'] for r in reports if r['record_id'] in prior),
        'new_B_calls':len(read(ROOT/'B_results.json')) if (ROOT/'B_results.json').exists() else 0,
        'new_C_calls':len(read(ROOT/'C_results.json')) if (ROOT/'C_results.json').exists() else 0,
        'reused_B_traces':read(ROOT/'plan.json')['B_reused_traces'],'reused_C_units':read(ROOT/'plan.json')['C_reused_units'],
        'quote_repairs':0,'review_calls':0,'automatic_retries':0,'semantic_accuracy_certified':False,
        'finished_at_utc':timestamp()}
    write(ROOT/'summary.json',summary)
    return summary


def finish(summary):
    final=ROOT/'final'
    report=['# 冻结v1 · 164条Trace Processor批量结果','',f"状态：**{summary['status']}**。", '',
        '所有新增调用使用冻结配置。兼容历史标注按原prompt/model版本复用；原10条阶段D结果逐字节保持一致。', '',
        '```json',json.dumps(summary,ensure_ascii=False,indent=2),'```','',
        '[逐轨迹验收](final/validation.json) · [缺失/失败记录](final/blocked_traces.json) · [复用来源](reuse_ledger.json)', '',
        '[Evidence Builder与诊断读取检查](final/downstream/validation.json) · [查询存储检查](final/downstream/query_validation.json)', '',
        '引文无法精确定位时保留告警，不通过改写、换证据块或修改实体ID使其通过。结构失败、无JSON和未完成标注显式保留，不当作成功。输入兼容与来源核验不等于语义或诊断准确率认证。','']
    (ROOT/'report.md').write_text('\n'.join(report))
    # Rehash preserved inputs at completion; never alter the historical batches.
    for path,sha in read(ROOT/'preserved_inputs.json').items():checked(path,sha)
    verify_freeze()
    files=[{'path':str(p.relative_to(final)),'sha256':digest(p),'bytes':p.stat().st_size}
           for p in sorted(final.rglob('*')) if p.is_file() and p.name not in ('manifest.json','portable_verification.json','bundle.zip')]
    write(final/'manifest.json',{'schema':'stage-d-delivery-manifest-v1','files':files,
        'excludes':['manifest.json','portable_verification.json','bundle.zip']})
    write(final/'portable_verification.json',verify_delivery(final))
    shutil.copytree(FREEZE,ROOT/'frozen_method',dirs_exist_ok=True)
    # Publish once all validation has finished. Ongoing status snapshots are small.
    shutil.copytree(ROOT,MIRROR,dirs_exist_ok=True)
    progress(stage='finished',status=summary['status'],active=[],summary=summary)


def main():
    lock=open(ROOT/'worker.lock','a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    write(ROOT/'worker.json',{'pid':os.getpid(),'started_at_utc':timestamp(),'freeze':str(FREEZE)})
    verify_freeze();plan=read(ROOT/'plan.json')
    for name in ('plan.json','reuse_ledger.json'):
        MIRROR.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,MIRROR/name)
    shutil.copytree(FREEZE,MIRROR/'frozen_method',dirs_exist_ok=True)
    progress(status='running',stage='B',B_reused_traces=plan['B_reused_traces'],C_reused_units=plan['C_reused_units'])
    groups=read(ROOT/'requirements_missing_groups.json')
    b_jobs=[{'group_id':key,'record_ids':rids} for key,rids in sorted(groups.items())]
    if not pool_execute('B',b_jobs,b_call,ROOT/'B_results.json'):return
    c_jobs=prepare_c()
    # Small end-to-end merge is unnecessary for the old ten; all other native
    # packets finish once, then the frozen D merger runs automatically.
    if not pool_execute('C',c_jobs,c_call,ROOT/'C_results.json'):return
    finish(d_stage())


if __name__=='__main__':
    try:main()
    except BaseException as exc:
        try:progress(status='stopped',stop_reason=type(exc).__name__+':'+str(exc)[:2000],active=[])
        finally:raise
