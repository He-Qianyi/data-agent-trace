"""Offline raw-recording -> A/B/C/D -> native-tool diagnosis -> evaluation replay.

All model responses are recorded research outputs. Request data and every native
read/search result must match those outputs' recorded context. No code in a trace
is executed. Sockets are blocked for the whole replay, including source checking.
"""
import argparse
import copy
from contextlib import ExitStack
import json
import math
from pathlib import Path
import shutil
from unittest.mock import patch

from .common import ROOT, read, save, digest, load_native
from pipeline.stages.trace_processor.fact_layer_v5.build import build
from pipeline.stages.trace_processor.semantic_layer.evidence import requirement_input, operation_input, batches, encode
from pipeline.stages.trace_processor.semantic_layer.validate import schema_check, requirements
from pipeline.stages.trace_processor.merge_layer.build import Bundle, merge_one, validate_final
from pipeline.stages.trace_processor.merge_layer.schema import schema as normalized_schema
from pipeline.stages.trace_processor.merge_layer.verify import verify as verify_bundle
from pipeline.stages.evidence_builder.index import EvidenceIndex, tokens
from pipeline.stages.diagnosis.validation import validate_diagnosis_text

DEFAULT_SAMPLE = ROOT / 'samples/real/dabstep_1464_claude'


def require_equal(actual, expected, label):
    if actual != expected:
        raise ValueError('Recorded replay mismatch: ' + label)


def verify_sample(sample):
    """Verify the closed, relative-path inventory before creating any output."""
    sample = Path(sample).resolve()
    manifest = read(sample/'manifest.json')
    seen = set()
    for row in manifest['files']:
        relative = Path(row['path'])
        p = (sample/relative).resolve()
        if relative.is_absolute() or not p.is_relative_to(sample) or row['path'] in seen:
            raise ValueError('Invalid or duplicate sample manifest path')
        if not p.is_file() or digest(p) != row['sha256']:
            raise ValueError('Sample hash mismatch: ' + row['path'])
        seen.add(row['path'])
    actual = {str(p.relative_to(sample)) for p in sample.rglob('*')
              if p.is_file() and not p.name.startswith('._') and p != sample/'manifest.json'}
    require_equal(actual, seen, 'sample inventory')
    provenance = read(sample/'provenance.json')
    for row in provenance['source_files']:
        require_equal(digest(sample/row['path']), row['published_sha256'], 'published source '+row['path'])
    return {'files_verified':len(seen), 'source_files_verified':len(provenance['source_files'])}


def prompt_identity(text):
    # Only previously disclosed local path spellings differ in the public copies.
    for path, placeholder in [
        ('/Volumes/Public HD/Data Agent Trace', '${PROJECT_ROOT}'),
        ('/Users/research/Documents/ChatGPT/Data Agent Trace Research', '${LOCAL_MIRROR}')]:
        for n in (8,4,2,1):
            text = text.replace(path.replace(' ', chr(92)*n+' '), placeholder)
        text = text.replace(path, placeholder)
    return text.replace('${LOCAL_MIRROR}', '${PROJECT_ROOT}')


def annotation_response(folder, expected_data, prompt, schema, expected_native):
    request, response = read(folder/'request.json'), read(folder/'response.json')
    require_equal(request['messages'][1]['content'],
                  'UNTRUSTED ANNOTATION INPUT DATA:\n'+encode(expected_data)+'\nApply the system contract and return JSON only.',
                  'annotation request evidence')
    require_equal(prompt_identity(request['messages'][0]['content']),
                  prompt_identity(prompt+'\nOUTPUT JSON SCHEMA (structural contract):\n'+encode(schema)),
                  'annotation frozen prompt/schema')
    choice = response['choices'][0]
    require_equal(choice['finish_reason'], 'stop', 'annotation completion')
    parsed = json.loads(choice['message']['content'])
    require_equal(parsed, expected_native, 'annotation native response')
    require_equal(response['model'], read(folder/'receipt.json')['returned_model'], 'annotation model receipt')
    schema_check(parsed, schema)
    return parsed


def bind_manifest(sample):
    row = read(sample/'raw/manifest.json')
    def local(relative):
        p = (sample/relative).resolve()
        if not p.is_relative_to(sample/'raw'):
            raise ValueError('Raw source escapes sample')
        return str(p)
    row['attempt_directory'] = local(row['attempt_directory'])
    row['model_io_path'] = local(row['model_io_path'])
    for source in row['sources']:
        source['path'] = local(source['path'])
    for receipt in row['completion']['receipts']:
        receipt['path'] = local(receipt['path'])
    row['historical_evaluation']['paths'] = [local(p) for p in row['historical_evaluation']['paths']]
    row['declared_source_hashes'] = {local(p):h for p,h in row['declared_source_hashes'].items()}
    return row


def processor_replay(sample, output):
    row = bind_manifest(sample); rid = row['record_id']
    facts = build(row)  # Original v5 implementation; fresh capture/source verification ON.
    historical = read(sample/'reference/historical_fact.json')
    # Full observed bodies/identities must agree; static extraction uses current v5.
    require_equal([s['step_id'] for s in facts['steps']], [s['step_id'] for s in historical['steps']], 'all step IDs')
    for current, old in zip(facts['steps'], historical['steps']):
        for key in ['tool_call_id','model_exchange_ref','observation']:
            require_equal(current[key], old[key], 'original step '+key)
        for key in ['tool_name','arguments','arguments_raw','arguments_ref']:
            require_equal(current['action'][key], old['action'][key], 'original action '+key)
    require_equal(facts['model_exchanges'], historical['model_exchanges'], 'all model exchanges')
    require_equal(facts['final_response'], historical['final_response'], 'recorded final response')
    fact_path = output/'facts/traces'/f'{rid}.json';save(fact_path,facts)
    tv, view = requirement_input(facts), operation_input(facts)
    source = sample/'recorded_processor'
    shared = source/'shared'/rid
    require_equal(tv, read(shared/'task_evidence.json'), 'complete task evidence')
    require_equal(view, read(shared/'operation_evidence.json'), 'complete operation evidence and unit partition')
    contract = ROOT/'configs/processor'
    prompt_b, prompt_c = [(contract/p).read_text() for p in ['requirements.compiled.md','compiled_system_prompt.md']]
    schema_b, schema_c = read(contract/'requirements.schema.json'), read(contract/'operations.schema.json')
    visible = {'evidence':[{k:e[k] for k in ('evidence_id','role','text')} for e in tv['evidence']]}
    require_equal(visible,read(shared/'requirements_input.json'),'requirements input')
    native_b = annotation_response(shared/'requirements_call', visible, prompt_b, schema_b, read(shared/'requirements_native.json'))
    accepted = requirements(native_b, tv)
    require_equal(accepted, read(shared/'requirements_accepted.json'), 'accepted recorded requirements')
    compact = [{k:r[k] for k in ('requirement_id','kind','description','basis')} for r in accepted['requirements']]
    profile = read(contract/'reproduction.json')
    packets = list(batches(view, compact, max_bytes=profile['max_packet_bytes'], max_units=profile['max_units_per_packet']))
    directories = sorted(p for p in (source/'cases'/rid).iterdir() if p.is_dir() and p.name.isdigit())
    require_equal(len(packets),len(directories),'complete packet count')
    jobs=[]
    for number,(packet,folder) in enumerate(zip(packets,directories),1):
        require_equal(packet,read(folder/'input.json'),'packet evidence '+folder.name)
        annotation_response(folder/'call',packet,prompt_c,schema_c,read(folder/'llm_output.json'))
        jobs.append({'record_id':rid,'packet':number,'directory':str(folder.relative_to(source)),
                     'input_sha256':digest(folder/'input.json'),'target_units':[u['unit_id'] for u in packet['target_units']]})
    record = {k:row[k] for k in ['record_id','benchmark','agent_id','task_id']}
    record.update(fact_path=str(fact_path),fact_sha256=digest(fact_path),operation_view_sha256=digest(shared/'operation_evidence.json'))
    final = output/'processor/final';final.mkdir(parents=True,exist_ok=False);bundle=Bundle(final)
    frozen = {p.name:bundle.add(p) for p in sorted(contract.iterdir()) if p.is_file() and not p.name.startswith('._')}
    operations_schema = read(contract/'validation-operations.schema.json')
    schema = normalized_schema(read(ROOT/'pipeline/stages/trace_processor/fact_layer_v5/schema.json'),operations_schema)
    save(final/'normalized_trace.schema.json',schema)
    trace = merge_one(record, source, jobs, bundle, operations_schema)
    trace['provenance']['frozen_contracts']=frozen
    relative = Path('traces')/f'{rid}.json';save(final/relative,trace);schema_check(trace,schema)
    checked = validate_final(trace,final,facts)
    save(final/'validation.json',[{**{k:row[k] for k in ['record_id','benchmark','agent_id','task_id']},
        'trace_path':str(relative),'trace_sha256':digest(final/relative),'quality':trace['quality'],'validation':checked}])
    save(final/'input_manifest.json',list(bundle.inputs.values()))
    save(final/'manifest.json',{'files':[{'path':str(p.relative_to(final)),'sha256':digest(p)}
        for p in sorted(final.rglob('*')) if p.is_file() and not p.name.startswith('._')]})
    report={'status':'passed','model_exchanges':len(facts['model_exchanges']),'tool_steps':len(facts['steps']),
        'semantic_units':len(trace['annotations']['units']),'recorded_annotation_responses_replayed':1+len(jobs),
        'full_historical_step_bodies_match':True,'full_historical_evidence_match':True,
        'fact_quality':facts['quality'],'normalized_quality':trace['quality'],'bundle_verification':verify_bundle(final),
        'new_model_calls':0,'recorded_code_executed':False}
    save(output/'processor/replay_report.json',report)
    return final,report


class HistoricalSearchIndex(EvidenceIndex):
    """Original BM25 calculation with frozen aggregate statistics of its corpus.

    The original index was built over 220 units, rather than this sample's nine.
    Only statistics for terms already visible in this public sample are included.
    All searchable documents below are real local units; no dummy trace is added.
    """
    def __init__(self,bundle,statistics):
        super().__init__(bundle)
        if len(self.documents)!=statistics['sample_document_count']:
            raise ValueError('Search statistics/sample mismatch')
        self.population=statistics['document_count'];self.weights=statistics['weights']
        self.df=statistics['document_frequency'];self.avg=statistics['average_field_lengths']

    def search(self,rid,question,top_k=3,after_step_id=None):
        if rid not in self.traces:return {'status':'trace_not_found','hits':[]}
        if top_k<1:raise ValueError('top_k must be positive')
        query=set(tokens(question));scored=[];N=self.population
        minimum=self.steps[rid][after_step_id][0] if after_step_id else -1
        for doc in self.documents:
            if doc['record_id']!=rid or self.steps[rid][doc['step_id']][0]<=minimum:continue
            score=0
            for field,weight in self.weights.items():
                tf=doc['terms'][field];length=sum(tf.values());average=self.avg[field] or 1
                for term in query:
                    count=tf.get(term,0)
                    if not count:continue
                    idf=math.log(1+(N-self.df[field][term]+0.5)/(self.df[field][term]+0.5))
                    score+=weight*idf*(count*2.2)/(count+1.2*(0.25+0.75*length/average))
            if score>0:scored.append((score,doc))
        scored.sort(key=lambda x:(-x[0],self.steps[rid][x[1]['step_id']][0],x[1]['unit_id']))
        return {'status':'candidates_found' if scored else 'no_index_hits','record_id':rid,'question':question,
            'search_scope':'recorded operation evidence and unverified annotation navigation; outcomes excluded',
            'absence_of_hit_is_not_proof_of_absence':True,
            'hits':[self.descriptor(rid,d['unit_id'],s) for s,d in scored[:top_k]]}


def diagnosis_replay(sample,bundle,output):
    source=sample/'recorded_diagnosis';rid=read(sample/'provenance.json')['record_id']
    index=HistoricalSearchIndex(bundle,read(source/'search_statistics.json'))
    native=load_native();safe=native.SafeEvidence(index,rid)
    initial=safe.initial();out=output/'diagnosis';save(out/'initial_input.json',initial)
    prompt=(ROOT/'configs/diagnosis/diagnosis.prompt.md').read_text()
    messages=[{'role':'system','content':prompt},{'role':'user','content':'CURRENT UNTRUSTED DIAGNOSIS INPUT DATA:\n'+native.encode(initial)}]
    calls=sorted(p for p in (source/'calls').iterdir() if p.is_dir() and p.name.isdigit())
    tool_number=0;final=None
    for number,folder in enumerate(calls,1):
        request,response=read(folder/'request.json'),read(folder/'response.json')
        expected=copy.deepcopy(request['messages']);actual=copy.deepcopy(messages)
        expected[0]['content']=prompt_identity(expected[0]['content']);actual[0]['content']=prompt_identity(actual[0]['content'])
        require_equal(actual,expected,'diagnosis round '+str(number)+' entire message chain')
        require_equal(request['tools'],native.TOOLS,'native tool contract')
        choice=response['choices'][0];message=choice['message'];tool_calls=message.get('tool_calls') or []
        require_equal(response['model'],read(folder/'receipt.json')['returned_model'],'diagnosis model receipt')
        if tool_calls:
            require_equal(choice['finish_reason'],'tool_calls','diagnosis tool completion')
            assistant={k:message[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in message}
            assistant.setdefault('role','assistant');messages.append(assistant)
            for call in tool_calls:
                tool_number+=1;name=call['function']['name'];args=json.loads(call['function']['arguments'])
                spec=next(t['function']['parameters'] for t in native.TOOLS if t['function']['name']==name)
                schema_check(args,spec)
                result=safe.invoke(name,args);safe.note_read(result)
                record={'ordinal':tool_number,'round':number,'name':name,'arguments':args,'result':result}
                require_equal(record,read(source/'tool_reads'/f'{tool_number:03d}.json'),'complete native tool result '+str(tool_number))
                save(out/'tool_reads'/f'{tool_number:03d}.json',record)
                messages.append({'role':'tool','tool_call_id':call['id'],'content':native.encode(result)})
        else:
            require_equal(choice['finish_reason'],'stop','diagnosis final completion')
            require_equal(number,len(calls),'no requests after final')
            final=message.get('content') or ''
            require_equal(final,(source/'native_final.txt').read_text(),'native final text')
    if final is None:raise ValueError('No recorded final diagnosis; replay cannot invent one')
    data=json.loads(final);require_equal(data,read(source/'diagnosis.json'),'saved final diagnosis')
    require_equal(safe.read_ranges,read(source/'read_ranges.json'),'complete read ranges')
    # Preserve historical bytes, including their original formatting.
    shutil.copyfile(source/'diagnosis.json',out/'diagnosis.json')
    shutil.copyfile(source/'native_final.txt',out/'native_final.txt')
    save(out/'read_ranges.json',safe.read_ranges)
    strict=native.validate_diagnosis(data,safe)
    acceptance=validate_diagnosis_text(final,index.steps[rid])
    citations=[]
    for section in ['root_cause_chain','critical_decision_points']:
        for item in data[section]:
            for eid in item.get('evidence_ids',[]):
                citations.append({'evidence_id':eid,'exists':eid in index.catalogs[rid] or eid in safe.extra,
                                  'actually_read':eid in safe.read_ranges,'read_ranges':safe.read_ranges.get(eid,[])})
    report={'status':'passed','recorded_model_rounds_replayed':len(calls),'native_tool_calls_reexecuted':tool_number,
        'all_request_messages_match':True,'all_native_tool_results_match':True,'all_read_ranges_match':True,
        'diagnosis_bytes_match_recording':digest(out/'diagnosis.json')==digest(source/'diagnosis.json'),
        'strict_schema_and_read_audit':strict,'current_structural_acceptance':acceptance,'citation_read_audit':citations,
        'retrieval_context':'Frozen historical corpus aggregate statistics; real sample units only.',
        'new_model_calls':0,'recorded_code_executed':False}
    save(out/'replay_report.json',report)
    return report


def _blocked(*args,**kwargs):
    raise RuntimeError('Network disabled during offline replay')


def replay(sample,output):
    sample,output=Path(sample).resolve(),Path(output).resolve()
    with ExitStack() as stack:
        for target in ['socket.socket.connect','socket.socket.connect_ex','socket.create_connection','socket.getaddrinfo']:
            stack.enter_context(patch(target,_blocked))
        if output.is_relative_to(sample):
            raise ValueError('Replay output must be outside the immutable sample')
        integrity=verify_sample(sample)
        output.mkdir(parents=True,exist_ok=False)
        bundle,processor=processor_replay(sample,output)
        diagnosis=diagnosis_replay(sample,bundle,output)
        review=read(sample/'author_review.json')
        result={'status':'passed','mode':'offline recorded-output replay','sample':sample.name,
            'integrity':integrity,'processor':processor,'diagnosis':diagnosis,
            'semantic_evaluation':{'independent_gold_available':False,'root_cause_accuracy':None,
                'author_review_status':review['case']['status'],'author_review_conclusion':review['case']['conclusion'],
                'native_diagnosis_was_not_repaired':True,'mechanical_pass_is_not_semantic_correctness':True},
            'new_model_calls':0,'network_disabled':True,'recorded_code_executed':False,
            'benchmark_agent_score_recomputed':False}
        outcome={'tool_steps':processor['tool_steps'],'model_exchanges':processor['model_exchanges'],
            'semantic_units':processor['semantic_units'],'annotation_responses':processor['recorded_annotation_responses_replayed'],
            'diagnosis_rounds':diagnosis['recorded_model_rounds_replayed'],'native_tool_calls':diagnosis['native_tool_calls_reexecuted'],
            'strict_schema_and_read_audit_passed':diagnosis['strict_schema_and_read_audit']['passed'],
            'current_structural_acceptance_passed':diagnosis['current_structural_acceptance']['passed'],
            'author_review_status':review['case']['status'],'root_cause_accuracy':None,'new_model_calls':0}
        require_equal(outcome,read(sample/'expected_outcome.json'),'verified sample outcome')
        save(output/'evaluation.json',result)
        (output/'report.md').write_text(
            '# Complete real sample replay\n\n'
            'Raw recording → Trace Processor → native-tool Trace Diagnosis → evaluation: passed.\n\n'
            f"- {processor['tool_steps']} original tool steps; {processor['model_exchanges']} recorded agent exchanges.\n"
            f"- {processor['recorded_annotation_responses_replayed']} recorded annotation responses replayed.\n"
            f"- {diagnosis['recorded_model_rounds_replayed']} diagnostic rounds; {diagnosis['native_tool_calls_reexecuted']} exact native tool returns.\n"
            '- All request histories, evidence blocks, read ranges and final diagnosis bytes match.\n'
            '- Strict schema/read audit and current structural acceptance pass.\n'
            '- Network blocked; zero new model calls; recorded code not executed.\n\n'
            f"Existing author review: **{review['case']['status']}**. {review['case']['conclusion']}\n\n"
            'This review is not independent gold. Root-cause accuracy remains null; the original diagnosis is retained unchanged.\n',
            encoding='utf-8')
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample',type=Path,default=DEFAULT_SAMPLE)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=replay(a.sample,a.output)
    print(json.dumps({'status':r['status'],'tool_steps':r['processor']['tool_steps'],
        'diagnosis_rounds':r['diagnosis']['recorded_model_rounds_replayed'],
        'new_model_calls':0,'author_review_status':r['semantic_evaluation']['author_review_status']},ensure_ascii=False))

if __name__=='__main__':main()
