"""Coverage accounting only; never changes retrieval or output."""
import importlib.util
from pathlib import Path
s=importlib.util.spec_from_file_location('round24',Path(__file__).with_name('run.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def pages(value):
 if isinstance(value,dict):
  if value.get('status')=='source_excerpt' and 'text' in value:yield value
  for v in value.values():yield from pages(v)
 elif isinstance(value,list):
  for v in value:yield from pages(v)
def merge(intervals):
 result=[]
 for a,b in sorted(intervals):
  if result and a<=result[-1][1]:result[-1][1]=max(result[-1][1],b)
  else:result.append([a,b])
 return result
def size(intervals):return sum(b-a for a,b in merge(intervals))
def run():
 plan=m.verify();rows=[]
 for job in plan['jobs']:
  if job['treatment']!='original_v1_prefix12_then_continue_to24':continue
  d=m.ROOT/'results'/job['record_id'];coverage=m.read(d/'prefix_read_ranges.json')
  for path in sorted((d/'tool_reads').glob('*.json')):
   x=m.read(path);total=novel=0
   for page in pages(x['result']):
    eid=page['evidence_id'];a,b=page['block_char_start'],page['block_char_end']
    old=coverage.get(eid,[]);new=merge(old+[[a,b]])
    total+=b-a;novel+=size(new)-size(old);coverage[eid]=new
   rows.append({'round':x['round'],'ordinal':x['ordinal'],'name':x['name'],'arguments':x['arguments'],
    'source_characters_returned':total,'previously_unseen_characters':novel,
    'fully_redundant_source_read':total>0 and novel==0})
 result={'new_retrieval_calls':len(rows),'fully_redundant_source_reads':sum(x['fully_redundant_source_read'] for x in rows),
  'previously_unseen_source_characters':sum(x['previously_unseen_characters'] for x in rows),
  'retrieval_policy_changed':False,'calls':rows}
 m.save(m.ROOT/'read_efficiency.json',result)
 print(m.b.encode({k:v for k,v in result.items() if k!='calls'}))
if __name__=='__main__':run()
