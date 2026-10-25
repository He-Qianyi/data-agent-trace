import hashlib
import json
from collections import Counter
from pathlib import Path

from pipeline.stages.trace_processor.semantic_layer.provider import helpers
from pipeline.stages.trace_processor.semantic_layer.validate import schema_check

ROOT = Path('outputs')
CONTRACT = ROOT/'stage_c_no_format_ten_20261018/contracts'


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def input_data(payload):
    msgs = payload.get('messages', payload.get('input', []))
    text = msgs[-1]['content']
    prefix = 'UNTRUSTED ANNOTATION INPUT DATA:\n'
    suffix = '\nApply the system contract and return JSON only.'
    if not isinstance(text,str) or not text.startswith(prefix) or not text.endswith(suffix): raise ValueError('input_wrapper')
    return json.loads(text[len(prefix):-len(suffix)])


def inventory():
    core,_=helpers()
    result=[]; skipped=Counter()
    roots=[p for p in ROOT.iterdir() if p.is_dir() and (p.name.startswith('stage_') or p.name.startswith('semantic_layer'))]
    schemas={stage:read(CONTRACT/name) for stage,name in [('B','requirements.schema.json'),('C','operations.schema.json')]}
    for root in sorted(roots):
        for path in sorted(root.rglob('request.json')):
            if any(x in path.parts for x in ['execution_history','implementation_snapshot']): continue
            if path.parent.name !='call' and not path.parent.name.endswith('_call') and not path.parent.name.endswith('requirements') and not path.parent.name.startswith('000'): continue
            try:
                request=read(path);data=input_data(request)
                stage='C' if set(data)=={'requirements','target_units','evidence'} else 'B' if 'evidence' in data and 'target_units' not in data else None
                if not stage: continue
                response=read(path.parent/'response.json')
                native=core.parse_annotation_response(core.normalize_native_chat(response) if 'choices' in response else response,request.get('max_tokens',request.get('max_output_tokens',48000)))
                valid_indices=None
                if stage=='C' and isinstance(native,dict) and isinstance(native.get('units'),list):
                    valid_indices=[]
                    for ui,unit in enumerate(native['units']):
                        try:schema_check({'units':[unit]},schemas['C']);valid_indices.append(ui)
                        except ValueError:pass
                    if not valid_indices:raise ValueError('no_schema_valid_native_units')
                else:schema_check(native,schemas[stage])
                record_id=next((s for s in path.parts if s.startswith('rawio-')),None)
                if stage=='C':
                    expected=[u['unit_id'] for u in data['target_units']]
                    if [u['unit_id'] for u in native['units']]!=expected:raise ValueError('unit_order_coverage')
                system=request.get('messages',request.get('input'))[0]['content']
                prompt=system.split('\nOUTPUT JSON SCHEMA (structural contract):\n')[0]
                item={'stage':stage,'root':str(root),'request_path':str(path.resolve()),'call_dir':str(path.parent.resolve()),
                      'request_sha256':sha(path),'response_sha256':sha(path.parent/'response.json'),
                      'record_id':record_id,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                      'max_tokens':request.get('max_tokens',request.get('max_output_tokens')),
                      'thinking_token_budget':request.get('thinking_token_budget'),
                      'valid_unit_indices':valid_indices,
                      'returned_model':response.get('model'),'data':data,'native':native,
                      'receipt':read(path.parent/'receipt.json') if (path.parent/'receipt.json').exists() else None}
                result.append(item)
            except Exception as exc:skipped[type(exc).__name__+':'+str(exc)[:90]]+=1
    return result,dict(skipped)


if __name__=='__main__':
    items,skipped=inventory()
    path=Path('work/trace_processor_batch164_20261018/inventory.json')
    path.write_text(json.dumps(items,ensure_ascii=False)+'\n')
    print('B calls',sum(x['stage']=='B' for x in items),'C calls',sum(x['stage']=='C' for x in items))
    print('C records',len({x['record_id'] for x in items if x['stage']=='C'}))
    print('roots',dict(Counter((x['root'],x['stage']) for x in items)))
    print('skipped',skipped)
