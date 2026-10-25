"""Read-only structural checks of preserved declared task inputs; never execute task code."""
from pathlib import Path
import os,json,hashlib,csv,sqlite3,time,collections,ast
BASE=Path(__file__).resolve().parents[2];ROOT=BASE/'artifacts/02_recording_quality/trace_curation_environment_short_v1_20261020'
assert Path('/Volumes/Public HD').is_mount()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(4*1024*1024),b''):h.update(c)
 return h.hexdigest()
catalog=json.loads((ROOT/'trace_catalog.json').read_text());refs=collections.defaultdict(set);aliases=collections.defaultdict(set);gaps=[]
def add(p,rid):
 if p.name.startswith('._'):return
 if p.is_file():refs[str(p.resolve())].add(rid);aliases[str(p.resolve())].add(str(p))
 elif not p.exists():gaps.append({'record_id':rid,'path':str(p),'scope':'current_preserved_input_path_missing_not_historical_proof'})
def expand(p,rid):
 if not p.exists():return
 if p.is_file():add(p,rid);return
 for d,dirs,files in os.walk(p,followlinks=True):
  dirs[:]=[x for x in dirs if not x.startswith('._') and x not in ['.git','__pycache__']]
  for n in files:add(Path(d)/n,rid)
for item in catalog:
 rid=item['record_id'];workspace=Path(item['workspace'])
 for name in ['context','data']:expand(workspace/name,rid)
 if workspace.exists():
  for d,dirs,files in os.walk(workspace,followlinks=False):
   dirs[:]=[n for n in dirs if n not in ['.git','node_modules','__pycache__'] and not n.startswith('._')]
   for n in files+dirs:
    p=Path(d)/n
    if p.is_symlink() and ('testbed/datasets/' in str(p.resolve()) or '/DABstep/data/context/' in str(p.resolve())):expand(p,rid)
 for manifest in item['input_manifests']:
  for n,entry in manifest['payload'].items():
   if isinstance(entry,dict) and entry.get('source'):add(Path(entry['source']),rid)
results=[];csv.field_size_limit(512*1024*1024)
for path,rids in sorted(refs.items()):
 p=Path(path);before=p.stat();row={'path':path,'aliases':sorted(aliases[path]),'record_ids':sorted(rids),'bytes':before.st_size,'sha256':sha(p)};started=time.monotonic()
 try:
  with p.open('rb') as f:head=f.read(32)
  if head.startswith(b'SQLite format 3'):
   row['format']='sqlite';conn=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);cutoff=time.monotonic()+60
   conn.set_progress_handler(lambda: int(time.monotonic()>cutoff),10000)
   try:
    tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall();check=conn.execute('PRAGMA quick_check(20)').fetchall()
    row.update(check='quick_check',tables=[x[0] for x in tables],result=check,status='passed' if check==[('ok',)] else 'failed')
   except sqlite3.OperationalError as exc:
    if str(exc)=='interrupted':row.update(check='bounded_quick_check_60s',status='bounded_incomplete',reason='time_budget_reached')
    else:raise
   finally:conn.close()
  elif p.suffix in ['.db','.duckdb','.sqlite']:
   import duckdb
   row['format']='duckdb';conn=duckdb.connect(str(p),read_only=True)
   try:
    tables=conn.execute('SHOW TABLES').fetchall();row['tables']=[x[0] for x in tables]
    for (n,) in tables:conn.execute('SELECT * FROM "'+n.replace('"','""')+'" LIMIT 1').fetchall()
   finally:conn.close()
   row.update(check='read_only_catalog_and_one_row_per_table',status='passed')
  elif p.suffix.lower()=='.csv':
   row['format']='csv';counts=collections.Counter()
   with p.open(encoding='utf-8-sig',newline='') as f:
    for vals in csv.reader(f,strict=True):counts[len(vals)]+=1
   row.update(check='full_stream_csv_parse',row_width_counts=dict(counts),status='passed')
  elif p.suffix.lower() in ['.json','.jsonl']:
   row['format']='json_or_jsonl';count=0
   if p.stat().st_size<20*1024*1024:
    try:
     with p.open(encoding='utf-8-sig') as f:json.load(f)
     row['format']='json';count=1
    except json.JSONDecodeError:
     with p.open(encoding='utf-8-sig') as f:
      for line in f:
       if line.strip():json.loads(line);count+=1
     row['format']='jsonl'
   else:
    with p.open(encoding='utf-8-sig') as f:
     for line in f:
      if line.strip():json.loads(line);count+=1
    row['format']='jsonl'
   row.update(check='full_stream_parse',documents=count,status='passed')
  elif p.suffix.lower()=='.shp':
   import shapefile
   with shapefile.Reader(str(p),encoding='latin-1') as reader:
    count=0
    for rec in reader.iterShapeRecords():count+=1
   row.update(format='shapefile',check='full_shape_record_read',records=count,status='passed')
  elif p.suffix.lower() in ['.shx','.dbf','.prj','.cpg']:
   row.update(format='shapefile_companion',check='nonempty_companion_shp_checked_separately',status='passed' if p.stat().st_size else 'failed')
  elif p.suffix.lower() in ['.txt','.md','.py','.ipynb']:
   with p.open(encoding='utf-8-sig') as f:
    for chunk in iter(lambda:f.read(1024*1024),''):pass
   row.update(format='utf8_text',check='full_utf8_decode',status='passed')
  else:row.update(format='other',check='readable_nonempty_bytes',status='passed' if p.stat().st_size else 'failed')
 except Exception as exc:row.update(status='failed',error_type=type(exc).__name__,error=str(exc)[:800])
 after=p.stat();row['stable_during_check']=(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns);row['elapsed_seconds']=round(time.monotonic()-started,3);results.append(row)
 save(ROOT/'input_checks_progress.json',{'checked':len(results),'total':len(refs),'last_file':path,'status_counts':dict(collections.Counter(r['status'] for r in results))})
 print(len(results),'/',len(refs),row['status'],p.name,flush=True)
save(ROOT/'input_file_checks.json',results);save(ROOT/'input_path_gaps.json',gaps)
summary={'unique_files':len(results),'bytes':sum(r['bytes'] for r in results),'status_counts':dict(collections.Counter(r['status'] for r in results)),'present_state_only':True,'network_services_contacted':False,'original_code_executed':False,'source_files_modified':False,'gaps':len(gaps)}
save(ROOT/'input_check_summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
