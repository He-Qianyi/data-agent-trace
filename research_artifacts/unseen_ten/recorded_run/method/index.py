"""Source-preserving navigation over Stage D traces; no diagnosis or execution."""
import copy
import hashlib
import math
import re
from collections import Counter, defaultdict, deque
from pathlib import Path

from ..trace_processor.merge_layer.build import read, bundled_sources
from ..trace_processor.fact_layer.sources import digest, pointer
from ..trace_processor.semantic_layer.evidence import encode


def tokens(text):
    result=[]
    for word in re.findall(r"#?[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]+)?", text):
        result.append(word.lower())
        parts=re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+",word.replace('_',' '))
        result.extend(p.lower() for p in parts if len(p)>1 and p.lower()!=word.lower())
    for word in re.findall(r"[\u3400-\u9fff]+",text):
        result.extend(word[i:i+2] for i in range(len(word)-1))
    return result


class EvidenceIndex:
    def __init__(self, bundle_root, weights=None):
        self.root=Path(bundle_root).resolve()
        self.weights=weights or {'annotation_navigation':4.0,'raw_evidence':1.0,'context_navigation':0.5}
        self.traces={};self.registries={};self.catalogs={};self.units={};self.steps={};self.documents=[]
        self.unit_documents={};self.requirement_units=defaultdict(list)
        for row in read(self.root/'validation.json'):
            rid=row['record_id'];path=self.root/row['trace_path']
            if digest(path)!=row['trace_sha256']:raise ValueError('Frozen normalized trace hash mismatch')
            trace=read(path);self.traces[rid]=trace
            self.catalogs[rid]={e['evidence_id']:e for e in trace['evidence_catalog']}
            self.units[rid]={u['unit_id']:u for u in trace['annotations']['units']}
            self.steps[rid]={s['step_id']:(i,s) for i,s in enumerate(trace['facts']['steps'])}
            for i,unit in enumerate(trace['annotations']['units']):
                sid=unit['step_id'];step=self.steps[rid][sid][1]
                evidence=[e for e in trace['evidence_catalog'] if e.get('step_id')==sid and e['role']!='assistant_claimed_intent']
                # Unit-local fragments remain the retrieval unit. The whole
                # enclosing tool call and model exchange are explicit drilldowns.
                original=read(self.root/trace['provenance']['annotation_inputs']['operation_evidence.json']['bundle_path'])
                target=next(u for u in original['units'] if u['unit_id']==unit['unit_id'])
                own=[self.catalogs[rid][eid] for eid in target['evidence_ids']]
                context=[self.catalogs[rid][eid] for eid in target['context_evidence_ids']]
                nav='\n'.join([step['action']['tool_name'],*[o['type']+' '+o['phase']+' '+o['description']+' '+
                    ' '.join(d['description'] for d in o['details']) for o in unit['operations']]])
                raw='\n'.join(e['text'] for e in own)
                fields={'annotation_navigation':Counter(tokens(nav)), 'raw_evidence':Counter(tokens(raw)),
                        'context_navigation':Counter(tokens('\n'.join(e['text'] for e in context)))}
                doc={'record_id':rid,'unit_id':unit['unit_id'],'step_id':sid,'model_exchange_ref':step['model_exchange_ref'],
                     'annotation_pointer':f'/annotations/units/{i}','evidence_ids':target['evidence_ids'],
                     'context_evidence_ids':target['context_evidence_ids'],'terms':fields}
                self.documents.append(doc);self.unit_documents[rid,unit['unit_id']]=doc
                for n,op in enumerate(unit['operations']):
                    for link in op['requirement_links']:
                        self.requirement_units[rid,link['requirement_id']].append({'unit_id':unit['unit_id'],'step_id':sid,
                            'operation_pointer':f'/annotations/units/{i}/operations/{n}', 'relation':link['relation'],
                            'relation_is_llm_annotation':True,'satisfaction_certified':False})
        self.df={};self.avg={}
        for field in self.weights:
            counts=Counter()
            for doc in self.documents:counts.update(doc['terms'][field].keys())
            self.df[field]=counts
            self.avg[field]=sum(sum(d['terms'][field].values()) for d in self.documents)/max(len(self.documents),1)

    def registry(self,rid):
        if rid not in self.registries:self.registries[rid]=bundled_sources(self.traces[rid],self.root)
        return self.registries[rid]

    def descriptor(self,rid,uid,score=None):
        doc=self.unit_documents[rid,uid];unit=self.units[rid][uid]
        unresolved=sum(c['status']=='unresolved' for c in self.traces[rid]['citation_index'] if c.get('unit_id')==uid)
        result={k:doc[k] for k in ('record_id','unit_id','step_id','model_exchange_ref','annotation_pointer','evidence_ids','context_evidence_ids')}
        result.update(navigation_is_llm_generated=True,semantic_correctness_certified=False,
            validation_status=unit['validation']['status'],unresolved_citations=unresolved,
            operations=[{'type':o['type'],'phase':o['phase'],'description':o['description']} for o in unit['operations']])
        if score is not None:result['navigation_score']=round(score,6)
        return result

    def search(self,rid,question,top_k=3,after_step_id=None):
        if rid not in self.traces:return {'status':'trace_not_found','hits':[]}
        if top_k<1:raise ValueError('top_k must be positive')
        query=set(tokens(question));scored=[];N=len(self.documents)
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

    def evidence_page(self,rid,eid,start=0,characters=2000):
        block=self.catalogs.get(rid,{}).get(eid)
        if block is None:return {'status':'evidence_not_found','evidence_id':eid}
        if start<0 or characters<1 or start>len(block['text']):raise ValueError('Invalid evidence range')
        end=min(start+characters,len(block['text']));text=block['text'][start:end]
        return {'status':'source_excerpt','record_id':rid,'evidence_id':eid,'role':block['role'],
            'text':text,'block_char_start':start,'block_char_end':end,'block_total_characters':len(block['text']),
            'field_char_start':block['char_start']+start,'field_char_end':block['char_start']+end,
            'fact_pointer':('/facts'+block['fact_pointer']) if block.get('fact_pointer') is not None else None,
            'original_source_ref':block.get('original_source_ref'),'representation':block.get('representation','text'),
            'next_char_start':end if end<len(block['text']) else None,
            'literal_text_unchanged':True,'statement_truth_or_execution_not_certified':True}

    def package(self,rid,question,top_k=3,budget_bytes=32000,page_characters=2000):
        found=self.search(rid,question,top_k)
        result={**found,'budget_bytes':budget_bytes,'raw_pages':[],'deferred_pages':[],
                'retrieval_complete_for_candidates':False,'payload_bytes':0}
        queues=[];seen=set()
        for hit in found['hits']:
            pages=deque()
            for eid in hit['evidence_ids']:
                if eid in seen:continue
                seen.add(eid);text=self.catalogs[rid][eid]['text']
                for start in range(0,len(text),page_characters):pages.append((eid,start))
            queues.append(pages)
        deferred=[];stop=False
        while any(queues) and not stop:
            for queue in queues:
                if not queue:continue
                eid,start=queue[0];page=self.evidence_page(rid,eid,start,page_characters)
                result['raw_pages'].append(page)
                if len(encode(result).encode())+1024>budget_bytes:
                    result['raw_pages'].pop();stop=True;break
                queue.popleft()
        # Deferred cursors may be numerous. Keep them in a compact form, never
        # silently drop missing text. The consumer can increase the total budget.
        for queue in queues:
            if queue:deferred.append({'evidence_id':queue[0][0],'next_char_start':queue[0][1],
                                      'remaining_pages_in_unit_queue':len(queue)})
        result['deferred_pages']=deferred;result['retrieval_complete_for_candidates']=not any(queues)
        result['payload_bytes']=len(encode(result).encode())
        # Account for the payload_bytes field's own changed digit count.
        result['payload_bytes']=len(encode(result).encode())
        if result['payload_bytes']>budget_bytes:raise ValueError('Navigation itself exceeds explicit payload budget')
        return result

    def get_step(self,rid,sid):
        if sid not in self.steps.get(rid,{}):return {'status':'step_not_found'}
        i,step=self.steps[rid][sid]
        return {'status':'recorded_step','fact_pointer':f'/facts/steps/{i}','value':copy.deepcopy(step),
                'model_exchange_ref':step['model_exchange_ref'],'scope':'one observed tool submission and its recorded returns'}

    def neighbors(self,rid,sid,before=1,after=2):
        if sid not in self.steps.get(rid,{}):return {'status':'step_not_found'}
        i=self.steps[rid][sid][0];steps=self.traces[rid]['facts']['steps']
        return {'status':'recorded_order_window','steps':[self.get_step(rid,s['step_id']) for s in steps[max(0,i-before):i+after+1]],
                'causal_dependency_certified':False,'parallel_call_serial_order_certified':False}

    def get_exchange(self,rid,xid):
        exchange=next((x for x in self.traces[rid]['facts']['model_exchanges'] if x['exchange_id']==xid),None)
        if exchange is None:return {'status':'exchange_not_found'}
        registry=self.registry(rid)
        return {'status':'full_recorded_model_exchange','exchange_id':xid,
                'request':registry.resolve(exchange['request_ref']),'response':registry.resolve(exchange['response_ref']),
                'request_ref':exchange['request_ref'],'response_ref':exchange['response_ref'],
                'hidden_reasoning_available':False,'request_history_is_not_additional_tool_execution':True}

    def requirement(self,rid,requirement_id):
        req=next((r for r in self.traces[rid]['annotations']['task']['requirements'] if r['requirement_id']==requirement_id),None)
        if req is None:return {'status':'requirement_not_found','links':[]}
        return {'status':'annotated_requirement','value':copy.deepcopy(req),
            'links':copy.deepcopy(self.requirement_units.get((rid,requirement_id),[])),
            'requirement_is_llm_interpretation':True,'linked_operation_does_not_certify_satisfaction':True}

    def outcome(self,rid):
        facts=self.traces[rid]['facts']
        return {'status':'recorded_outcome','task':copy.deepcopy(facts['task']),
            'requirements':copy.deepcopy(self.traces[rid]['annotations']['task']),
            'final_response':{'fact_pointer':'/facts/final_response','value':copy.deepcopy(facts['final_response'])},
            'deliverables':[{'fact_pointer':f'/facts/deliverables/{i}','value':copy.deepcopy(a)} for i,a in enumerate(facts['deliverables'])],
            'evaluation':{'fact_pointer':'/facts/evaluation','value':copy.deepcopy(facts['evaluation'])},
            'post_run_snapshots_and_evaluation_not_agent_time_knowledge':True,
            'snapshot_presence_does_not_certify_agent_creation_or_answer_correctness':True}

    def citation(self,rid,annotation_pointer):
        c=next((c for c in self.traces[rid]['citation_index'] if c['annotation_pointer']==annotation_pointer),None)
        return copy.deepcopy(c) if c else {'status':'citation_not_found'}

    def sql_result_binding(self,rid,sid,ordinal=0):
        step=self.get_step(rid,sid)
        if step['status']!='recorded_step':return step
        operations=[d for d in step['value']['action']['static_extraction']['data_operations'] if d.get('operation')=='sql_api']
        if ordinal<0 or ordinal>=len(operations):return {'status':'sql_operation_not_found'}
        op=operations[ordinal]
        return {'status':op.get('result_scope','not_recorded_separately'),'sql_operation':copy.deepcopy(op),
            'enclosing_observation_ref':step['fact_pointer']+'/observation',
            'individual_result_binding':op.get('individual_result_binding','not_recorded_separately'),
            'statement_execution_verified':op.get('statement_execution_verified',False)}

    def runtime_variable(self,rid,sid,name):
        if sid not in self.steps.get(rid,{}):return {'status':'step_not_found'}
        return {'status':'no_explicit_runtime_state_snapshot','variable':name,
            'scope':'No structured variable-state snapshot field in this normalized fact contract; printed previews can be retrieved separately',
            'value':None,'value_inferred_from_code':False}

    def export_index(self):
        return {'schema':'data-agent-evidence-index-v0','source_bundle':str(self.root),
            'source_manifest_sha256':digest(self.root/'manifest.json'),'weights':self.weights,
            'documents':self.documents,'links':[{'record_id':rid,'requirement_id':req,'candidates':links}
                for (rid,req),links in self.requirement_units.items()],
            'outcomes_indexed_for_ranking':False,'causal_graph_constructed':False,
            'step_order_is_not_causal_order':True,'new_llm_calls':0}
