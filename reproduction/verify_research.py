"""Verify the published research results without model calls or private datasets.

This is a publication audit adapter. It checks the actual released records,
source-ledger bindings, message signatures, saved formatting extraction, counts
and cohort partition. It does not rerun withheld trace contexts or certify the
semantic correctness of a diagnosis. Frozen historical runners are not imported.
"""
import argparse
from collections import Counter,defaultdict
from contextlib import ExitStack
import json
import re
from pathlib import Path
from unittest.mock import patch
from .common import ROOT,read,save,digest
from pipeline.stages.diagnosis.validation import validate_diagnosis_text


def encode(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def signature(value):
    import hashlib
    return hashlib.sha256(encode(value).encode()).hexdigest()


def require(value,label):
    if not value:raise ValueError('Research artifact verification failed: '+label)


def inventory(root):
    manifest=read(root/'manifest.json');seen=set()
    for item in manifest['files']:
        p=(root/item['path']).resolve()
        require(p.is_relative_to(root) and not Path(item['path']).is_absolute(),'relative inventory path')
        require(item['path'] not in seen,'duplicate inventory path')
        require(p.is_file() and digest(p)==item['sha256'],'file hash '+item['path'])
        seen.add(item['path'])
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and not p.name.startswith('._') and p!=root/'manifest.json'}
    require(actual==seen,'closed public inventory')
    provenance=read(root/'publication_provenance.json')
    source_paths=set()
    for row in provenance['sources']:
        require(row['path'] not in source_paths and row['path'] in seen,'source ledger path')
        source_paths.add(row['path'])
        require(digest(root/row['path'])==row['published_sha256'],'published source ledger hash')
        if not row['changes']:require(row['source_sha256']==row['published_sha256'],'unchanged source bytes')
    return {'status':'passed','published_files':len(seen),'bound_existing_source_files':len(source_paths),
            'source_unchanged_during_publication':provenance['source_originals_verified_unchanged'],
            'local_archive_needed_for_original_source_recheck':True}


def experiment(root,relative):
    folder=root/relative;cases=read(folder/'case_index.json');calls=read(folder/'call_index.json');reads=read(folder/'read_event_index.json')
    by_id={c['record_id']:c for c in cases};require(len(by_id)==len(cases),'unique cases')
    require({j['record_id'] for j in read(folder/'plan.json')['jobs']}==set(by_id),'case/plan coverage')
    by_case=defaultdict(list)
    verified_responses=0
    for call in calls:
        require(call['record_id'] in by_id,'call case binding')
        require(len(call['message_sha256'])==len(call['message_roles'])==len(call['message_tool_call_ids'])==len(call['message_content_sha256']),'message index lengths')
        by_case[call['record_id']].append(call)
        response=folder/'results'/call['record_id']/call['call']/'response.json'
        require(response.exists()==call['response_present'],'response presence, including transport failures')
        if not response.exists():continue
        body=read(response);choice=body['choices'][0];message=choice['message']
        assistant={k:message[k] for k in ['role','content','tool_calls','reasoning_content','reasoning'] if k in message}
        assistant.setdefault('role','assistant')
        require(signature(assistant)==call['published_assistant_message_sha256'],'native response message signature')
        require(choice['finish_reason']==call['finish_reason'],'finish reason')
        native=[{'id':c['id'],'name':c['function']['name'],'arguments':c['function']['arguments']} for c in message.get('tool_calls',[])]
        require(native==call['response_tool_calls'],'recorded tool calls')
        receipt=response.with_name('receipt.json')
        if receipt.exists() and read(receipt).get('returned_model'):
            require(body['model']==read(receipt)['returned_model'],'provider model receipt')
        verified_responses+=1
    chains=0;read_links=0;unreturned=0
    read_hashes=defaultdict(set)
    for r in reads:read_hashes[r['record_id']].add(r['result_message_content_sha256'])
    for rid,sequence in by_case.items():
        for previous,current in zip(sequence,sequence[1:]):
            if not previous['response_present']:
                require(previous['message_sha256']==current['message_sha256'],'explicit retry preserved request')
                unreturned+=1;continue
            prefix=previous['message_sha256']+[previous['published_assistant_message_sha256']]
            require(current['message_sha256'][:len(prefix)]==prefix,'entire preceding message signature prefix')
            tool_ids=previous['response_tool_call_ids']
            require(current['message_tool_call_ids'][len(prefix):]==tool_ids,'native tool-result identities appended')
            require(current['message_roles'][len(prefix):]==['tool']*len(tool_ids),'native tool-result roles appended')
            for h in current['message_content_sha256'][len(prefix):]:
                require(h in read_hashes[rid],'tool-result content matches a recorded read')
                read_links+=1
            chains+=1
    statuses=Counter();native_json=0;current_accepted=0
    for case in cases:
        f=folder/'results'/case['record_id'];result=read(f/'result.json');statuses[result['status']]+=1
        native=f/'native_final.txt';diagnosis=f/'diagnosis.json'
        if native.exists():
            text=native.read_text()
            try:value=json.loads(text)
            except ValueError:value=None
            native_json+=isinstance(value,dict)
            current_accepted+=bool(validate_diagnosis_text(text,case['step_ids'])['passed'])
            if diagnosis.exists():require(value==read(diagnosis),'saved diagnosis equals native final JSON')
            # Match the final message to an actual native response where included.
            terminal=[c for c in by_case[case['record_id']] if c['response_present'] and not c['response_tool_call_ids']]
            if terminal:
                c=terminal[-1];body=read(root/c['response_file'])
                require(body['choices'][0]['message'].get('content')==text,'native final equals actual provider output')
        else:require(not diagnosis.exists(),'no diagnosis invented for nonterminal run')
    return {'cases':len(cases),'recorded_status_counts':dict(statuses),'saved_native_JSON_objects':native_json,
        'current_policy_acceptance_recomputed':current_accepted,'provider_responses_checked':verified_responses,
        'message_prefix_transitions_checked':chains,'tool_result_content_links_checked':read_links,
        'unreturned_request_replays_checked':unreturned,'native_read_metadata_records':len(reads),
        'full_request_or_source_text_replayed':False,'semantic_accuracy_measured':False}


def unseen(root):
    folder=root/'unseen_ten';recorded=folder/'recorded_run';cases={c['record_id']:c for c in read(recorded/'case_index.json')}
    summary=read(folder/'format_revalidation/summary.json');native_pass=0;clean_pass=0;removed=0
    for row in summary['traces']:
        rid=row['record_id'];native=(recorded/'results'/rid/'native_final.txt').read_text()
        target=folder/'format_revalidation/results'/rid;extraction=read(target/'extraction.json')
        start,end=extraction['retained_char_range'];cleaned=(target/'diagnosis.json').read_text()
        require(native[start:end]==cleaned,'format extraction retains exact original substring')
        native_ok=bool(validate_diagnosis_text(native,cases[rid]['step_ids'])['passed'])
        clean_ok=bool(validate_diagnosis_text(cleaned,cases[rid]['step_ids'])['passed'])
        require(native_ok==row['native_accepted'] and clean_ok==row['accepted'],'native and postprocessed acceptance')
        native_pass+=native_ok;clean_pass+=clean_ok;removed+=row['wrapper_removed']
    require(len(cases)==summary['total']==10,'unseen-ten denominator')
    require(native_pass==summary['native_accepted']==6 and clean_pass==summary['accepted_after_wrapper_removal']==10 and removed==4,'unseen-ten wrapper counts')
    review=read(folder/'author_review/review.json');require(review['not_independent_human_gold'] and not review['semantic_accuracy_percentage_reported'],'author-review scope')
    require({r['record_id'] for r in review['cases']}==set(cases),'author review covers all ten')
    return {'cases':10,'native_acceptance':native_pass,'after_recorded_wrapper_extraction':clean_pass,
        'wrappers_removed':removed,'diagnostic_payload_edited':False,'author_review_cases':len(review['cases']),
        'independent_gold':False,'root_cause_accuracy':None}


def author_spot_checks(root):
    folder=root/'unseen_ten/author_review'
    ledger=read(folder/'evidence_ledger.json');catalog={(r['record_id'],r['evidence_id']):r['text'] for r in ledger}
    checks=read(folder/'checks.json')
    text=lambda rid,eid:catalog[(rid,eid)]
    rid='rawio-c05784a0b4925c35b147'
    immediate=list(map(int,re.findall(r'Fee (\d+):.*?capture=immediate,',text(rid,'E00050'))))
    require(immediate==checks['1753_immediate_count']['actual_ids'],'author immediate-rule IDs')
    answer=set(map(int,re.findall(r'\d+',text(rid,'E00068'))))
    require(len(answer)==checks['1753_immediate_count']['answer_count'],'author recorded answer count')
    rid='rawio-e7c485f571630e650d08'
    counts={m[0]:int(m[1]) for m in re.findall(r'^\s+([A-Z]{2}): avg_rating=[\d.]+, total_reviews=(\d+)$',text(rid,'E00016'),re.M)}
    expected=checks['yelp_review_population'];require(counts==expected['recorded_counts'],'author recorded review-row population')
    unassigned=expected['total_review_rows']-sum(counts.values())
    bound=max(n for state,n in counts.items() if state!='PA')+unassigned
    require(bound==expected['largest_non_PA_plus_all_unassigned'] and counts['PA']>bound,'author ranking bound')
    rid='rawio-28b2f965e92b9d64d640'
    require(text(rid,'E00042').rstrip().endswith('PYEOF'),'recorded command writes heredoc')
    require(text(rid,'E00043')==checks['github_unexecuted_loop']['S0014_observation'],'recorded loop observation')
    code=text('rawio-747fdd4b9b059ac3c74c','E00116')
    require(all(fragment in code for fragment in ["'AvgPrice': ['mean', 'std']", "'AvgPrice': year_data['AvgPrice']", '.rolling(window=12, min_periods=1).std()']),'two analysis choices changed in comparison')
    return {'public_evidence_spot_checks_recomputed':4,'immediate_rule_count':len(immediate),
        'native_diagnosis_reported_count':checks['1753_immediate_count']['diagnosis_count'],
        'known_count_error_retained':len(immediate)!=checks['1753_immediate_count']['diagnosis_count'],
        'author_review_not_independent_gold':True,'supplemental_dataset_checks_rerun':False,
        'posthoc_gold_comparison_rerun':False}


def processor(root):
    folder=root/'processor_validation';v5=folder/'v5';curation=folder/'curation'
    traces=read(v5/'trace_inventory.json');ids={t['record_id'] for t in traces}
    require(len(ids)==len(traces)==164,'164 trace inventory')
    validation=read(v5/'final/validation.json');require({r['record_id'] for r in validation}==ids,'164 original validation records')
    by_id={r['record_id']:r for r in validation}
    for trace in traces:
        row=by_id[trace['record_id']]
        require(row['trace_sha256']==trace['trace_sha256'],'trace hash anchor')
        require(row['validation']['native_annotations_unchanged']==trace['semantic_units'],'native unit counts')
        for key in ['normalized_schema','fact_roundtrip','bundled_source_hashes','citation_ranges']:
            require(row['validation'][key]=='passed','recorded all-trace validation '+key)
    post=read(v5/'postmerge_validation.json')
    require(post['status']=='passed' and post['native_annotations_citations_and_catalog_unchanged']==164,'v5 historical postmerge status')
    require(sum(t['semantic_units'] for t in traces)==post['counts']['native_units']==4259,'4259 semantic units')
    recovered=read(v5/'known26_recovery.json');require(len(recovered)==26,'26 recovered fields')
    require(len({r['record_id'] for r in recovered})==2,'two recovered traces')
    def rows(name):return [json.loads(s) for s in (curation/name).read_text().splitlines() if s.strip()]
    kept,excluded=rows('retained_manifest.jsonl'),rows('excluded_manifest.jsonl')
    k={r['record_id'] for r in kept};e={r['record_id'] for r in excluded}
    require(not k&e and k|e==ids and len(kept)==len(k)==145 and len(excluded)==len(e)==19,'145/19 complete disjoint partition')
    decisions=read(curation/'decisions.json');require({r['record_id'] for r in decisions}==ids,'164 selection decisions')
    require({d['record_id'] for d in decisions if d['decision']=='retained'}==k,'decisions retained IDs')
    summary=read(curation/'summary.json')
    require(summary['input_traces']==164 and summary['retained']==145 and summary['excluded']==19,'selection summary counts')
    require(summary['environment_excluded']==14 and summary['short_success_excluded']==5,'selection reason counts')
    require(read(curation/'final_verification.json')['all_164_original_trace_hashes_unchanged'],'historical trace preservation')
    return {'historical_traces':164,'tool_steps':sum(t['tool_steps'] for t in traces),
        'semantic_units':sum(t['semantic_units'] for t in traces),'historical_source_checks_passed':len(validation),
        'recovered_fields':26,'retained':len(k),'excluded':len(e),'environment_exclusions':14,'short_success_exclusions':5,
        'partition_recomputed_from_published_lists':True,'full_corpus_source_audit_rerun':False,
        'scope':'Historical all-corpus validation records and hash anchors; source trace objects/datasets are not bundled.'}


def controls(root,experiments):
    result={}
    for exp in experiments:result[Path(exp['directory']).name]=experiment(root,exp['directory'])
    continuation=read(root/'diagnosis_controls/round24_continuation/summary.json')
    require(continuation['continued_case_result']['status']=='round_budget_exhausted','round24 nonconvergence retained')
    candidate=read(root/'diagnosis_controls/no_repeat_and_unlocalized/summary.json')
    require(candidate['accepted']==0 and candidate['total']==2,'two candidate nonterminal results retained')
    require(result['no_repeat_and_unlocalized']['saved_native_JSON_objects']==0,'no candidate final JSON invented')
    return {'experiments':result,'candidate_accepted':0,'candidate_cases':2,
        'candidate_is_two_simultaneous_prompt_changes':True,'single_factor_semantic_improvement_claimed':False,
        'historical_acceptance_policies_are_not_relabelled':True,'independent_gold':False,'root_cause_accuracy':None}


def blocked(*args,**kwargs):raise RuntimeError('Network disabled for research artifact verification')


def verify(artifacts,output=None):
    root=Path(artifacts).resolve()
    if output is not None:
        output=Path(output).resolve();require(not output.is_relative_to(root),'output outside immutable artifact package')
        require(not output.exists(),'new verification output directory')
    with ExitStack() as stack:
        for target in ['socket.socket.connect','socket.socket.connect_ex','socket.create_connection','socket.getaddrinfo']:stack.enter_context(patch(target,blocked))
        integrity=inventory(root);provenance=read(root/'publication_provenance.json')
        run=experiment(root,'unseen_ten/recorded_run')
        result={'status':'passed','scope':'Offline verification of the published research records, not a rerun of the full private corpus',
            'integrity':integrity,'unseen_ten':unseen(root),'unseen_recorded_run':run,'author_spot_checks':author_spot_checks(root),
            'processor_validation':processor(root),'diagnosis_controls':controls(root,provenance['control_experiments']),
            'new_model_calls':0,'network_disabled':True,'captured_analysis_code_executed':False,
            'independent_semantic_gold':False,'root_cause_accuracy':None}
        if output is not None:
            output.mkdir(parents=True,exist_ok=False);save(output/'verification.json',result)
            (output/'report.md').write_text('# Research artifact verification\n\n'
                'All published-file hashes, existing-source bindings, native response records, indexed message/read continuity, recorded formatting extraction and cohort partition checks passed.\n\n'
                'The new ten-case run retains native acceptance 6/10 and separately recorded wrapper-extracted acceptance 10/10. These are structural outcomes.\n\n'
                'The historical processor audit covers 164 traces and 4,259 semantic units; the published selection lists partition into 145 retained and 19 excluded cases. The full source corpus is not rerun.\n\n'
                'Negative controls and semantic-review issues remain unchanged. No model calls, dataset access or captured analysis-code execution were performed. Independent diagnosis accuracy remains unavailable.\n',encoding='utf-8')
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--artifacts',type=Path,default=ROOT/'research_artifacts');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=verify(a.artifacts,a.output)
    print(json.dumps({'status':r['status'],'published_files':r['integrity']['published_files'],
        'unseen_native_acceptance':r['unseen_ten']['native_acceptance'],'unseen_after_wrapper_extraction':r['unseen_ten']['after_recorded_wrapper_extraction'],
        'historical_processed_traces':r['processor_validation']['historical_traces'],'new_model_calls':0},ensure_ascii=False))

if __name__=='__main__':main()
