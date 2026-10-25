"""Portable fresh B/C/D orchestration over the released research algorithms.

A facts are built by the original fact_layer_v5 CLI. This adapter removes the
historical batch's private reuse ledger and mirror paths; it does not invent
annotations or silently retry failed calls. Live use is opt-in.
"""
import argparse
import copy
from pathlib import Path
import shutil

from .common import ROOT, read, save, digest
from pipeline.stages.trace_processor.fact_layer.sources import Sources
from pipeline.stages.trace_processor.semantic_layer.evidence import requirement_input, operation_input, batches, encode
from pipeline.stages.trace_processor.semantic_layer.provider import Client
from pipeline.stages.trace_processor.semantic_layer.validate import requirements, schema_check
from pipeline.stages.trace_processor.merge_layer.build import Bundle, merge_one, validate_final
from pipeline.stages.trace_processor.merge_layer.schema import schema as normalized_schema


def process(facts_root, output, config, env_file=None, max_requests=1000, client_factory=Client):
    facts_root, output = Path(facts_root).resolve(), Path(output).resolve()
    profile = read(config)
    contract_root = ROOT / 'configs/processor'
    prompt_b = (contract_root / 'requirements.compiled.md').read_text()
    prompt_c = (contract_root / 'compiled_system_prompt.md').read_text()
    schema_b = read(contract_root / 'requirements.schema.json')
    schema_c = read(contract_root / 'operations.schema.json')
    validation_schema_c = read(contract_root / 'validation-operations.schema.json')
    manifest = [__import__('json').loads(line) for line in (facts_root / 'manifest.jsonl').read_text().splitlines() if line.strip()]
    if not manifest or max_requests < 1:
        raise ValueError('Need validated facts and a positive request budget')
    if any(r['status'] not in ('passed', 'passed_with_warnings') for r in manifest):
        raise ValueError('Fact manifest includes ineligible records')
    # Read-only preflight precedes output creation and any provider request.
    facts = {}
    for row in manifest:
        p = facts_root / row['path']
        if digest(p) != row['sha256']:
            raise ValueError('Fact manifest hash mismatch')
        f = read(p)
        Sources.from_entries(f['sources'])
        if f['quality']['source_reference_validation'] != 'passed':
            raise ValueError('Unvalidated fact source references')
        facts[row['record_id']] = (p, f)
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'release_run_config.json', {'profile':profile, 'profile_sha256':digest(config),
        'facts_manifest_sha256':digest(facts_root/'manifest.jsonl'), 'max_requests':max_requests,
        'public_contract_sha256':{p.name:digest(p) for p in contract_root.iterdir() if p.is_file() and not p.name.startswith('._')},
        'orchestration':'portable fresh sequential run; no historical reuse ledger',
        'new_orchestration_not_a_historical_experiment':True, 'automatic_retries':0})
    state = {'status':'prepared', 'requests_attempted':0, 'traces_completed':0}
    save(output/'progress.json', state)
    client = client_factory(profile['model'], env_file, output_tokens=profile['max_tokens'],
        timeout=profile['timeout_seconds'], wire_protocol='chat', thinking_token_budget=profile['thinking_token_budget'])
    def call(prompt, data, schema, folder):
        if (output/'pause.requested').exists():
            raise InterruptedError('pause_requested')
        if state['requests_attempted'] >= max_requests:
            raise InterruptedError('request_budget_exhausted')
        state['requests_attempted'] += 1
        save(output/'progress.json',state)
        value, receipt = client.ask(prompt, data, schema, folder)
        if receipt.get('returned_model') != profile['expected_returned_model']:
            raise ValueError('Unexpected returned model; no fallback')
        schema_check(value, schema)
        return value, receipt
    rows, jobs, task_cache = [], [], {}
    try:
        for record in manifest:
            rid=record['record_id'];fact_path,fact=facts[rid]
            shared=output/'shared'/rid
            tv,view=requirement_input(fact),operation_input(fact)
            visible={'evidence':[{k:e[k] for k in ('evidence_id','role','text')} for e in tv['evidence']]}
            for name,value in [('task_evidence.json',tv),('operation_evidence.json',view),('requirements_input.json',visible)]:save(shared/name,value)
            key=tv['task_prompt_sha256']
            if key in task_cache:
                native,source=task_cache[key]
                shutil.copytree(source,shared/'requirements_call')
            else:
                native,_=call(prompt_b,visible,schema_b,shared/'requirements_call')
                task_cache[key]=(copy.deepcopy(native),shared/'requirements_call')
            accepted=requirements(native,tv)
            save(shared/'requirements_native.json',native);save(shared/'requirements_accepted.json',accepted)
            compact=[{k:r[k] for k in ('requirement_id','kind','description','basis')} for r in accepted['requirements']]
            row={k:record[k] for k in ('record_id','benchmark','agent_id','task_id')}
            row.update(fact_path=str(fact_path),fact_sha256=digest(fact_path),operation_view_sha256=digest(shared/'operation_evidence.json'))
            rows.append(row)
            for i,packet in enumerate(batches(view,compact,max_bytes=profile['max_packet_bytes'],max_units=profile['max_units_per_packet']),1):
                folder=output/'cases'/rid/f'{i:04d}'
                save(folder/'input.json',packet)
                native,receipt=call(prompt_c,packet,schema_c,folder/'call')
                save(folder/'llm_output.json',native)
                save(folder/'result.json',{'status':'model_response_received','receipt':receipt})
                jobs.append({'record_id':rid,'packet':i,'directory':str(folder.relative_to(output)),
                    'input_sha256':digest(folder/'input.json'),'target_units':[u['unit_id'] for u in packet['target_units']]})
            state['traces_completed']+=1;save(output/'progress.json',state)
        save(output/'selected_traces.json',rows);save(output/'jobs.json',jobs)
        final=output/'final';final.mkdir();bundle=Bundle(final)
        contracts={p.name:bundle.add(p) for p in sorted(contract_root.iterdir()) if p.is_file() and not p.name.startswith('._')}
        schema=normalized_schema(read(ROOT/'pipeline/stages/trace_processor/fact_layer_v5/schema.json'),validation_schema_c)
        save(final/'normalized_trace.schema.json',schema)
        reports=[]
        for row in rows:
            trace=merge_one(row,output,[j for j in jobs if j['record_id']==row['record_id']],bundle,validation_schema_c)
            trace['provenance']['frozen_contracts']=contracts
            relative=Path('traces')/(row['record_id']+'.json')
            save(final/relative,trace);schema_check(trace,schema)
            check=validate_final(read(final/relative),final,read(row['fact_path']))
            reports.append({**{k:row[k] for k in ('record_id','benchmark','agent_id','task_id')},
                'trace_path':str(relative),'trace_sha256':digest(final/relative),'quality':trace['quality'],'validation':check})
        save(final/'validation.json',reports)
        save(final/'input_manifest.json',list(bundle.inputs.values()))
        save(final/'manifest.json',{'files':[{'path':str(p.relative_to(final)),'sha256':digest(p)}
            for p in sorted(final.rglob('*')) if p.is_file() and not p.name.startswith('._')]})
        state['status']='completed';save(output/'progress.json',state)
        return state
    except Exception as exc:
        state.update(status='stopped',error_type=type(exc).__name__)
        save(output/'progress.json',state)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--facts',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path,default=ROOT/'configs/processor/reproduction.json')
    p.add_argument('--env-file',type=Path,required=True);p.add_argument('--max-requests',type=int,default=1000)
    p.add_argument('--allow-network',action='store_true')
    a=p.parse_args()
    if not a.allow_network:p.error('Live model calls require --allow-network; use the offline evaluator for the public excerpt')
    print(encode(process(a.facts,a.output,a.config,a.env_file,a.max_requests)))
if __name__=='__main__':main()
