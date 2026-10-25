from pathlib import Path
import json,hashlib,shutil,sys,collections,datetime
BASE=Path(__file__).resolve().parents[2];ROOT=BASE/'artifacts/02_recording_quality/trace_curation_environment_short_v1_20261020';OUT=ROOT/'eligible'
sys.path.insert(0,str(BASE))
from pipeline.stages.trace_processor.merge_layer.verify import verify
import duckdb
assert Path('/Volumes/Public HD').is_mount()
def read(p):return json.loads(p.read_text())
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
plan=read(ROOT/'plan.json');parent=BASE/plan['source_bundle'];decisions=read(ROOT/'decisions.json');summary=read(ROOT/'summary.json')
keep={r['record_id'] for r in decisions if r['decision']=='retained'};excluded={r['record_id'] for r in decisions if r['decision']=='excluded'}
assert len(keep)==summary['retained'] and keep.isdisjoint(excluded) and len(keep|excluded)==164
assert not OUT.exists();OUT.mkdir();(OUT/'traces').mkdir();(OUT/'objects').symlink_to(parent/'objects',target_is_directory=True)
rows=[r for r in read(parent/'validation.json') if r['record_id'] in keep]
refs=set()
def collect(x):
 if isinstance(x,dict):
  if isinstance(x.get('bundle_path'),str) and x['bundle_path'].startswith('objects/'):refs.add(x['bundle_path'])
  for v in x.values():collect(v)
 elif isinstance(x,list):
  for v in x:collect(v)
for row in rows:
 p=parent/row['trace_path'];(OUT/row['trace_path']).symlink_to(p);collect(read(p));assert sha(p)==row['trace_sha256']
save(OUT/'validation.json',rows)
save(OUT/'input_manifest.json',[r for r in read(parent/'input_manifest.json') if r['bundle_path'] in refs])
save(OUT/'active_fact_manifest.json',[r for r in read(parent/'active_fact_manifest.json') if r['record_id'] in keep])
save(OUT/'blocked_traces.json',[])
shutil.copyfile(parent/'normalized_trace.schema.json',OUT/'normalized_trace.schema.json')
shutil.copyfile(ROOT/'retained_manifest.jsonl',OUT/'curation_manifest.jsonl')
(OUT/'downstream').mkdir();jsonl_stats={}
for name in ['diagnosis_inputs','evidence_samples','timeline','edges']:
 before=after=0;observed=set()
 with (parent/'downstream'/f'{name}.jsonl').open() as src,(OUT/'downstream'/f'{name}.jsonl').open('w') as dst:
  for line in src:
   obj=json.loads(line);rid=obj['trace_id'];before+=1
   if rid in keep:dst.write(line);after+=1;observed.add(rid)
 assert observed==keep,(name,len(observed))
 jsonl_stats[name]={'parent_rows':before,'retained_rows':after,'distinct_traces':len(observed),'lines_preserved_exactly':True}
reports=[r for r in read(parent/'downstream/validation.json') if r['record_id'] in keep];save(OUT/'downstream/validation.json',reports)
conn=duckdb.connect(str(OUT/'downstream/trace_evidence.duckdb'))
conn.execute("ATTACH '"+str(parent/'downstream/trace_evidence.duckdb').replace("'","''")+"' AS source (READ_ONLY)")
conn.execute('CREATE TEMP TABLE retained_ids(record_id VARCHAR)');conn.executemany('INSERT INTO retained_ids VALUES (?)',[(i,) for i in sorted(keep)])
dbchecks=[]
for (table,) in conn.execute('SHOW TABLES FROM source').fetchall():
 q='"'+table.replace('"','""')+'"';cols=conn.execute('DESCRIBE source.'+q).fetchall();names=[c[0] for c in cols]
 if 'trace_id' not in names:
  conn.execute('CREATE TABLE '+q+' AS SELECT * FROM source.'+q);continue
 predicate=' WHERE trace_id IN (SELECT record_id FROM retained_ids)'
 conn.execute('CREATE TABLE '+q+' AS SELECT * FROM source.'+q+predicate)
 schema=conn.execute('DESCRIBE '+q).fetchall();assert [(x[0],x[1]) for x in cols]==[(x[0],x[1]) for x in schema]
 for a,b in [('SELECT * FROM '+q,'SELECT * FROM source.'+q+predicate),('SELECT * FROM source.'+q+predicate,'SELECT * FROM '+q)]:
  assert conn.execute('SELECT count(*) FROM (('+a+') EXCEPT ALL ('+b+'))').fetchone()[0]==0
 count,distinct=conn.execute('SELECT count(*),count(DISTINCT trace_id) FROM '+q).fetchone();assert conn.execute('SELECT count(*) FROM '+q+' WHERE trace_id NOT IN (SELECT record_id FROM retained_ids)').fetchone()[0]==0
 dbchecks.append({'table':table,'rows':count,'distinct_traces':distinct,'all_columns_types_and_values_equal_parent_subset':True})
conn.close();save(OUT/'downstream/query_validation.json',{'status':'passed','trace_count':len(keep),'tables':dbchecks,'no_json_type_reinference':True})
save(OUT/'downstream/static_fact_validation.json',{'status':'passed','method':'Typed SQL subset copy; full bidirectional EXCEPT ALL over every trace-bearing table and identical DuckDB column types; no JSON inference/reannotation.','trace_count':len(keep),'tables':dbchecks})
save(OUT/'curation.json',{'source_bundle':str(parent.relative_to(BASE)),'curation_run':str(ROOT.relative_to(BASE)),'retained':len(keep),'excluded':len(excluded),'shared_objects':'Objects and traces are immutable symlinks to the preserved full v5 bundle; this is a project-local curated view, not an independent portable copy.','model_calls':0})
files=[]
for p in OUT.rglob('*'):
 if p.is_file() and not p.name.startswith('._') and p.name!='manifest.json':files.append({'path':str(p.relative_to(OUT)),'sha256':sha(p),'bytes':p.stat().st_size})
# Include every archived dependency referenced by retained traces; objects are shared.
known={r['path'] for r in files}
for rel in sorted(refs-known):
 p=OUT/rel;assert p.is_file(),rel;files.append({'path':rel,'sha256':sha(p),'bytes':p.stat().st_size})
save(OUT/'manifest.json',{'schema':'stage-d-curated-view-manifest-v1','files':files})
verified=verify(OUT);save(ROOT/'eligible_verification.json',verified)
from pipeline.stages.evidence_builder.index import EvidenceIndex
index=EvidenceIndex(OUT);assert set(index.traces)==keep
assert not any(r['scores'] and 1 in r['scores'] and r['tool_steps']<10 for r in decisions if r['decision']=='retained')
assert all(sha(parent/r['trace_path'])==r['sha256'] for r in plan['traces'])
save(ROOT/'subset_checks.json',{'status':'passed','retained':len(keep),'excluded':len(excluded),'partition_covers_original_164':True,'exclusion_leakage':False,'all_164_trace_bytes_unchanged':True,'existing_evidence_index_loads':len(index.traces),'jsonl':jsonl_stats,'duckdb':dbchecks,'full_normalized_validation':verified['status']})
print(json.dumps({'eligible':len(keep),'verification':verified['status'],'files_verified':verified['files_verified'],'existing_evidence_index':len(index.traces),'jsonl':jsonl_stats},ensure_ascii=False),flush=True)
