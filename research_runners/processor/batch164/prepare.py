"""Freeze current contracts and plan only missing annotation work."""
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import shutil
import sys

BASE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(BASE))
from pipeline.stages.trace_processor.semantic_layer.evidence import encode, operation_input, requirement_input
from pipeline.stages.trace_processor.semantic_layer.validate import requirements, schema_check
from pipeline.stages.trace_processor.fact_layer.sources import Sources,digest
from pipeline.stages.trace_processor.merge_layer.build import read,validated_task

ROOT=BASE/'outputs/trace_processor_batch164_20261018'
FREEZE=BASE/'outputs/trace_processor_frozen_v1_20261018'
TEN=BASE/'outputs/stage_c_no_format_ten_20261018'


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.writing');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def signature(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def compact(accepted):return [{k:r[k] for k in ('requirement_id','kind','description','basis')} for r in accepted['requirements']]
def visible(view):return {'evidence':[{k:e[k] for k in ('evidence_id','role','text')} for e in view['evidence']]}
def copy_call(item,dest):
    dest.mkdir(parents=True,exist_ok=False)
    for p in Path(item['call_dir']).glob('*.json'):
        if p.name in ('request.json','response.json','receipt.json'):shutil.copyfile(p,dest/p.name)


def main():
    ROOT.mkdir(exist_ok=False);FREEZE.mkdir(exist_ok=False)
    for p in (TEN/'contracts').iterdir():
        if p.is_file():shutil.copyfile(p,FREEZE/p.name)
    runtime=read(FREEZE/'runtime_profile.json')
    runtime.update(prompt_path=str((FREEZE/'compiled_system_prompt.md').relative_to(BASE)),
                   schema_path=str((FREEZE/'operations.schema.json').relative_to(BASE)),status='frozen_v1_batch164')
    write(FREEZE/'runtime_profile.json',runtime)
    shutil.copyfile(BASE/'outputs/stage_d_ten_20261018/normalized_trace.schema.json',FREEZE/'normalized_trace.schema.json')
    shutil.copyfile(BASE/'work/trace_processor_batch164_20261018/provider_metadata.json',FREEZE/'provider_metadata.json')
    source_selection=BASE/'outputs/semantic_layer_deepseek_chat_v1_20261017/selection.json'
    rows=read(source_selection);assert len(rows)==164 and len({r['record_id'] for r in rows})==164
    inventory=read(BASE/'work/trace_processor_batch164_20261018/inventory.json')
    b_by_view=defaultdict(list)
    c_by_record=defaultdict(list)
    for item in inventory:
        if item['stage']=='B':b_by_view[signature(item['data'])].append(item)
        else:c_by_record[item['record_id']].append(item)
    for patch_path in BASE.glob('outputs/stage_a_reviewed_inputs*/**/review_patch.json'):
        patch=read(patch_path)
        for path,expected in patch['original_files_sha256'].items():assert digest(path)==expected
        for item in inventory:
            if item['stage']=='B' and patch['original_files_sha256'].get(item['request_path'])==item['request_sha256']:
                reviewed=copy.deepcopy(item);reviewed.update(existing_review=patch,existing_review_path=str(patch_path))
                b_by_view[signature(item['data'])].append(reviewed)
    c_prompt=digest(FREEZE/'compiled_system_prompt.md');b_prompt=digest(FREEZE/'requirements.compiled.md')
    def rank(item,stage):
        return (item['prompt_sha256']==(c_prompt if stage=='C' else b_prompt),Path(item['request_path']).stat().st_mtime_ns,item['request_path'])
    reuse=[];jobs=[];b_missing={};preserved={};bad_candidates=Counter()
    for row in rows:
        fact_path=Path(row['fact_path']);assert digest(fact_path)==row['fact_sha256']
        fact=read(fact_path)
        Sources.from_entries(fact['sources'])
        view=operation_input(fact);tv=requirement_input(fact);bv=visible(tv)
        shared=ROOT/'shared'/row['record_id'];write(shared/'operation_evidence.json',view);write(shared/'task_evidence.json',tv);write(shared/'requirements_input.json',bv)
        row.update(operation_view_path=str(shared/'operation_evidence.json'),operation_view_sha256=digest(shared/'operation_evidence.json'),units=len(view['units']))
        unit_map={u['unit_id']:u for u in view['units']};evidence_map={e['evidence_id']:e for e in view['evidence']}
        valid_b=[]
        for item in b_by_view.get(signature(bv),[]):
            try:
                accepted=validated_task(item['native'],tv,item.get('existing_review'))
                valid_b.append((item,accepted,signature(compact(accepted))))
            except ValueError:bad_candidates['B_literal_or_accounting']+=1
        c_groups=defaultdict(list)
        for item in c_by_record.get(row['record_id'],[]):
            try:
                data=item['data']
                assert all(unit_map[u['unit_id']]==u for u in data['target_units'])
                assert all(e=={k:evidence_map[e['evidence_id']][k] for k in e} for e in data['evidence'])
                req_sig=signature(data['requirements'])
                if req_sig not in {x[2] for x in valid_b}:continue
                c_groups[req_sig].append(item)
            except (AssertionError,KeyError):bad_candidates['C_fact_view_mismatch']+=1
        chosen_b=None;chosen_c={}
        if c_groups:
            def group_rank(pair):
                sig,items=pair
                coverage={x['native']['units'][n]['unit_id'] for x in items for n in x['valid_unit_indices']}
                return (len(coverage),max(rank(x,'C') for x in items),sig)
            req_sig,items=max(c_groups.items(),key=group_rank)
            chosen_b=max((x for x in valid_b if x[2]==req_sig),key=lambda x:rank(x[0],'B'))
            for item in sorted(items,key=lambda x:rank(x,'C'),reverse=True):
                for index,u in enumerate(item['native']['units']):
                    if index not in item['valid_unit_indices']:continue
                    chosen_c.setdefault(u['unit_id'],(item,index))
        elif valid_b:chosen_b=max(valid_b,key=lambda x:rank(x[0],'B'))
        if chosen_b:
            item,accepted,_=chosen_b
            write(shared/'requirements_native.json',item['native']);write(shared/'requirements_accepted.json',accepted)
            copy_call(item,shared/'requirements_call')
            if item.get('existing_review'):write(shared/'requirements_review.json',item['existing_review'])
            write(shared/'requirements_reuse.json',{'source_request':item['request_path'],'source_request_sha256':item['request_sha256'],
                'source_response_sha256':item['response_sha256'],'source_prompt_sha256':item['prompt_sha256'],
                'current_prompt_match':item['prompt_sha256']==b_prompt,'model':item['returned_model'],
                'native_output_unchanged':True,'existing_authorized_review_replayed':bool(item.get('existing_review')),
                'existing_review_path':item.get('existing_review_path'),'citations_rebound_to_this_fact_sources':True,'llm_calls':0})
            reuse.append({'record_id':row['record_id'],'stage':'B','source':item['request_path'],'source_prompt_sha256':item['prompt_sha256'],'current_prompt_match':item['prompt_sha256']==b_prompt})
            reqs=compact(accepted)
            for uid in sorted(chosen_c):
                item,index=chosen_c[uid];unit=item['native']['units'][index]
                folder=ROOT/'cases'/row['record_id']/'packets'/f'{len([j for j in jobs if j["record_id"]==row["record_id"]])+1:04d}'
                packet={**copy.deepcopy(item['data']),'target_units':[unit_map[uid]]}
                write(folder/'input.json',packet);write(folder/'llm_output.json',{'units':[unit]})
                write(folder/'original_native_output.json',item['native']);copy_call(item,folder/'call')
                write(folder/'reuse.json',{'source_unit_index':index,'source_request':item['request_path'],
                    'source_request_sha256':item['request_sha256'],'source_response_sha256':item['response_sha256'],
                    'source_prompt_sha256':item['prompt_sha256'],'current_prompt_match':item['prompt_sha256']==c_prompt,
                    'selection_only_not_annotation_repair':True,'native_unit_unchanged':True,'llm_calls':0})
                write(folder/'result.json',{'status':'reused_native_schema_valid','quote_gate_deferred_to_D':True,'target_units':[uid]})
                job={'record_id':row['record_id'],'task_id':row['task_id'],'packet':int(folder.name),'directory':str(folder.relative_to(ROOT)),
                     'input_sha256':digest(folder/'input.json'),'target_units':[uid],'reused':True}
                jobs.append(job)
                reuse.append({'record_id':row['record_id'],'stage':'C','unit_id':uid,'source':item['request_path'],
                    'source_unit_index':index,'source_prompt_sha256':item['prompt_sha256'],'current_prompt_match':item['prompt_sha256']==c_prompt})
        else:b_missing.setdefault(signature(bv),[]).append(row['record_id'])
        row['reused_C_units']=len(chosen_c);row['B_reused']=bool(chosen_b)
        print(row['task_id'],row['agent_id'],'B',bool(chosen_b),'C',len(chosen_c),'/',len(view['units']),flush=True)
    write(ROOT/'selected_traces.json',rows);write(ROOT/'jobs.json',jobs)
    write(ROOT/'reuse_ledger.json',reuse);write(ROOT/'requirements_missing_groups.json',b_missing)
    write(ROOT/'source_selection.json',{'path':str(source_selection),'sha256':digest(source_selection)})
    # Every existing request, including unusable or incompatible attempts, is
    # reserved against byte-identical resubmission by the new runner.
    old_requests=[]
    for directory in BASE.joinpath('outputs').iterdir():
        if directory.is_dir() and (directory.name.startswith('stage_') or directory.name.startswith('semantic_layer')):
            for p in directory.rglob('request.json'):
                try:old_requests.append(signature(read(p)))
                except (OSError,ValueError):pass
    write(ROOT/'historical_request_signatures.json',sorted(set(old_requests)))
    for p in [source_selection,*[Path(r['fact_path']) for r in rows],*[Path(i['request_path']) for i in inventory],
              *[Path(i['call_dir'])/'response.json' for i in inventory]]:
        preserved[str(p)]=digest(p)
    write(ROOT/'preserved_inputs.json',preserved)
    policy={'version':'trace-processor-frozen-v1-20261018','new_generation':{'B_prompt_sha256':b_prompt,'C_prompt_sha256':c_prompt,
            'runtime':runtime},'reuse_policy':'Prefer structurally complete native units matching the frozen prompt; otherwise reuse compatible historical native units with original prompt/model provenance. Bind to identical fact evidence and identical task-requirement ID semantics. Never rank by benchmark score or semantic correctness.',
            'reused_packet_projection':'Select unchanged native units from original complete response; archive both. Not a model rerun, quotation repair, or new annotation.',
            'failure_policy':'No automatic retries/repair/review. Preserve failures and missing coverage explicitly. Continue other traces unless provider/service identity failure.',
            'scope':{'traces':164,'fact_steps':sum(r['steps'] for r in rows),'annotation_units':sum(r['units'] for r in rows)},
            'old_batch_remains_paused':True,'official_evaluation_not_sent':True,'existing_D_ten_reused_byte_for_byte':True}
    write(FREEZE/'protocol.json',policy)
    stats={'traces':164,'B_reused_traces':sum(r['B_reused'] for r in rows),'B_unique_missing_inputs':len(b_missing),
           'C_reused_traces':sum(r['reused_C_units']>0 for r in rows),'C_reused_units':sum(r['reused_C_units'] for r in rows),
           'C_missing_units':sum(r['units']-r['reused_C_units'] for r in rows),'total_units':sum(r['units'] for r in rows),
           'historical_requests_reserved':len(set(old_requests)),'excluded_historical_candidates':dict(bad_candidates),
           'reused_prompt_versions':dict(Counter((x['stage']+':'+x['source_prompt_sha256']) for x in reuse))}
    write(ROOT/'plan.json',stats);write(ROOT/'progress.json',{'status':'prepared','stage':'B','plan':stats})
    print(json.dumps(stats,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
