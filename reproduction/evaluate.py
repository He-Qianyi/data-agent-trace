"""Recompute mechanical checks on a genuine recorded public excerpt.

No ground truth is manufactured. An author review is not independent semantic
gold, and structural/evidence counts are not root-cause accuracy.
"""
import argparse
import collections
import json
from pathlib import Path
import jsonschema
from .common import ROOT,read,save,digest
from pipeline.stages.diagnosis.validation import validate_diagnosis
from pipeline.stages.trace_processor.fact_layer_v5.extract import extract_action
from pipeline.stages.trace_processor.merge_layer.citations import citations,locate


def evaluate(sample, output):
    sample=Path(sample).resolve();output=Path(output).resolve()
    manifest=read(sample/'manifest.json')
    for item in manifest['files']:
        if digest(sample/item['path']) != item['sha256']:
            raise ValueError('Released sample hash mismatch: '+item['path'])
    trace=read(sample/'trace_excerpt.json')
    if trace.get('synthetic') is not False:
        raise ValueError('This evaluator requires the declared real recorded excerpt')
    provenance=read(sample/'provenance.json')
    if digest(sample/'diagnosis.json') != provenance['source_diagnosis_sha256']:
        raise ValueError('Diagnosis differs from original recorded bytes')
    data=read(sample/'diagnosis.json')
    annotations=read(sample/'processor_annotations.json')
    by_id={e['evidence_id']:e for e in trace['evidence']}
    if len(by_id)!=len(trace['evidence']):raise ValueError('Duplicate evidence ID')
    schema=read(ROOT/'configs/diagnosis/diagnosis.schema.json')
    errors=[{'path':'/'.join(map(str,e.absolute_path)),'validator':e.validator,'message':e.message}
        for e in jsonschema.Draft202012Validator(schema).iter_errors(data)]
    mechanical=validate_diagnosis(data,trace['original_trace_step_ids'])
    refs=[]
    for section in ['root_cause_chain','critical_decision_points']:
        for i,item in enumerate(data.get(section,[])):
            for eid in item.get('evidence_ids',[]):
                refs.append({'section':section,'index':i,'evidence_id':eid,'published':eid in by_id})
    quote_checks=[]
    for path,citation in citations(annotations,'/recorded_processor_annotations'):
        if citation['evidence_id'] not in by_id:
            quote_checks.append({'annotation_pointer':path,'evidence_id':citation['evidence_id'],'status':'outside_published_excerpt'})
        else:
            quote_checks.append({'annotation_pointer':path,**locate(citation,by_id,set(by_id))})
    # The code excerpt is parsed, never executed or used to regenerate observations.
    facts=[{'step_id':s['step_id'],'extraction':extract_action(s['action'])} for s in trace['steps']]
    result={'record_id':trace['record_id'],'data_origin':'real_recorded_excerpt','new_model_calls':0,
        'recorded_code_executed':False,'sample_file_hashes':'passed','recorded_diagnosis_bytes':'unchanged',
        'coverage':{'published_step_ids':trace['published_step_ids'],'original_trace_step_ids':trace['original_trace_step_ids'],
                    'full_native_recording_available':False,'evidence_text_transformed':provenance['evidence_text_transformed']},
        'strict_declared_schema':{'passed':not errors,'errors':errors},
        'current_structural_acceptance':mechanical,
        'diagnosis_evidence_references':{'total':len(refs),'published':sum(r['published'] for r in refs),'checks':refs,
            'presence_does_not_prove_entailment':True,'actual_model_read_audit_replayed':False},
        'processor_literal_citations':{'counts':dict(collections.Counter(c['status'] for c in quote_checks)),
            'checks':quote_checks,'semantic_correctness_certified':False},
        'semantic_evaluation':{'independent_gold_available':False,'root_cause_accuracy':None,
            'causal_chain_accuracy':None,'reason':'No independent semantic gold is released; existing author review is descriptive only.'}}
    output.mkdir(parents=True,exist_ok=False)
    save(output/'evaluation.json',result);save(output/'recomputed_static_facts.json',facts)
    lines=['# Real sample mechanical evaluation','',
        'This report is recomputed from a real recorded excerpt. No model calls or recorded code execution occurred.','',
        f"Record: `{trace['record_id']}`",'',
        f"Strict declared schema: {'passed' if not errors else 'failed'}.",
        f"Current CodeTracer-compatible structural acceptance: {'passed' if mechanical['passed'] else 'failed'}.",
        f"Published diagnostic evidence references: {sum(r['published'] for r in refs)}/{len(refs)}.",
        '', '## Schema findings','']
    lines += [f"- {e['validator']}: {e['message']}" for e in errors] or ['None.']
    lines += ['', '## Interpretation limits','',
        'An extra field in the original diagnosis is preserved. Different structural policies can therefore yield different acceptance outcomes. Neither policy evaluates diagnosis truth.',
        '', 'Evidence ID presence and exact quotation checks establish mechanical properties only. The full original model-read audit is not replayed from this public excerpt.',
        '', 'Independent semantic gold and root-cause accuracy are unavailable and are not assigned a numerical value. See the existing author review and sample coverage notes.']
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample',type=Path,default=ROOT/'samples/real/agriculture_22_claude')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=evaluate(a.sample,a.output)
    print(json.dumps({'record_id':r['record_id'],'strict_schema_passed':r['strict_declared_schema']['passed'],
        'current_acceptance_passed':r['current_structural_acceptance']['passed'],
        'root_cause_accuracy':r['semantic_evaluation']['root_cause_accuracy']},indent=2))
if __name__=='__main__':main()
