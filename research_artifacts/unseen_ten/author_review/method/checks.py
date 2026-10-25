"""Offline claim checks. No captured code execution, no model calls, no data writes."""
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal
import csv,hashlib,json,re,statistics
BASE=Path(__file__).resolve().parents[2]
ROOT=BASE/'work/diagnosis_unseen_ten_semantic_review_20261020'
SOURCE=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_20261020'
CLEAN=BASE/'artifacts/05_diagnosis/diagnosis_unseen_ten_v5_format_revalidation_20261020'
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def trace(rid):return read(SOURCE/'source_view/traces'/f'{rid}.json')
def catalog(rid):return {e['evidence_id']:e for e in trace(rid)['evidence_catalog']}
assert Path('/Volumes/Public HD').is_mount()
checks={};inputs=[]
# Check exact source text rather than the model's paraphrases.
c=catalog('rawio-c05784a0b4925c35b147')
actual=re.findall(r'Fee (\d+):.*?capture=immediate,',c['E00050']['text'])
answer=set(map(int,re.findall(r'\d+',c['E00068']['text'])))
gold=set(map(int,re.findall(r'\d+',trace('rawio-c05784a0b4925c35b147')['facts']['evaluation']['records'][0]['payload']['gold_answer'])))
checks['1753_immediate_count']={'source':'E00050','actual_ids':list(map(int,actual)),'actual_count':len(actual),'diagnosis_count':7,
 'answer_count':len(answer),'recorded_official_gold_count':len(gold),'answer_minus_official_gold':sorted(answer-gold),
 'official_gold_minus_answer':sorted(gold-answer),'removing_actual_immediate_rules_matches_recorded_gold':answer-set(map(int,actual))==gold,
 'gold_used_posthoc_only':True}
# Regex counterexample and evidence-based bound on the unidentified review population.
y=catalog('rawio-e7c485f571630e650d08')
s='Located at 9916 Clayton Rd in St. Louis, MO, this establishment offers services.'
checks['yelp_regex']={'pattern':r'in\s+[\w\s]+,\s+([A-Z]{2})','recorded_businessid_47_description_matches':bool(re.search(r'in\s+[\w\s]+,\s+([A-Z]{2})',s)),
 'expected_state':'MO','recorded_business_review_count':81,'control_matches_Goleta_CA':bool(re.search(checks.get('_unused_pattern',r'in\s+[\w\s]+,\s+([A-Z]{2})'),'Located in Goleta, CA, this facility.'))}
counts={m[0]:int(m[1]) for m in re.findall(r'^\s+([A-Z]{2}): avg_rating=[\d.]+, total_reviews=(\d+)$',y['E00016']['text'],re.M)}
checks['yelp_review_population']={'recorded_counts':counts,'assigned_review_rows':sum(counts.values()),'total_review_rows':2000,
 'unassigned_review_rows':2000-sum(counts.values()),'PA_count':counts['PA'],'MO_count':counts['MO'],
 'largest_non_PA_plus_all_unassigned':max(n for s,n in counts.items() if s!='PA')+2000-sum(counts.values()),
 'PA_still_largest_even_if_all_unassigned_reviews_go_to_single_other_state':counts['PA']>max(n for s,n in counts.items() if s!='PA')+2000-sum(counts.values()),
 'ranking_basis':'recorded review-row counts, not business.review_count'}
# Planned query never ran: do not equate predicted slowness with observed timeout.
g=catalog('rawio-28b2f965e92b9d64d640')
checks['github_unexecuted_loop']={'S0014_command_is_heredoc_only':g['E00042']['text'].rstrip().endswith('PYEOF'),
 'S0014_observation':g['E00043']['text'],'recorded_final':trace('rawio-28b2f965e92b9d64d640')['facts']['final_response']['text'],
 'runtime_of_loop_observed':False}
# Rolling sensitivity changed both price AND volatility; row difference is not isolated.
a=catalog('rawio-747fdd4b9b059ac3c74c');code=a['E00116']['text']
checks['agriculture28_confounded_comparison']={'simple_price_annual_mean':"'AvgPrice': ['mean', 'std']" in code,
 'rolling_price_last_observed_month':"'AvgPrice': year_data['AvgPrice']" in code,
 'rolling_window_12_observations':'.rolling(window=12, min_periods=1).std()' in code,
 'both_price_and_volatility_changed':True,'single_factor_causal_inference_supported':False}
# Supplemental current input files, never presented as additional model-time reads.
fees_path=Path('/Volumes/Public HD/DABstep/data/context/fees.json')
pay_path=Path('/Volumes/Public HD/DABstep/data/context/payments.csv')
cr_path=Path('/Volumes/Public HD/AgenticDataBench/testbed/datasets/agriculture/Crop Recommendation using Soil Properties and Weather Prediction.csv')
for p in [fees_path,pay_path,cr_path]:
 assert p.is_file(),str(p);inputs.append({'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size,'scope':'supplementary_current_local_input_not_sent_to_diagnostic_model'})
fees=read(fees_path)
explicit=[f for f in fees if f['card_scheme']=='GlobalCard' and 'H' in (f['account_type'] or []) and 5812 in (f['merchant_category_code'] or [])]
wildcard=[f for f in fees if f['card_scheme']=='GlobalCard' and (not f['account_type'] or 'H' in f['account_type']) and (not f['merchant_category_code'] or 5812 in f['merchant_category_code'])]
def avg(fs):return sum(Decimal(str(f['fixed_amount']))+Decimal(str(f['rate']))*Decimal(10)/Decimal(10000) for f in fs)/len(fs)
checks['1305_empty_list_sensitivity']={'explicit_ids':[f['ID'] for f in explicit],'explicit_average':str(avg(explicit)),
 'null_or_empty_as_wildcard_count':len(wildcard),'null_or_empty_as_wildcard_average':str(avg(wildcard)),
 'rounded_six_decimals':f'{avg(wildcard):.6f}','wildcard_assumption':'Treat [] as unrestricted along with null; documented null rule alone does not establish [] semantics.',
 'introduced_as_posthoc_sensitivity_not_diagnostic_input':True}
selected=[286,381,454,473,477,536,572,602,709,741,813,895]
checks['kimi1681_monthly_rules_in_returned_list']=[{k:f[k] for k in ['ID','monthly_fraud_level','monthly_volume']} for f in fees if f['ID'] in selected and (f['monthly_fraud_level'] is not None or f['monthly_volume'] is not None)]
totals=Counter();frauds=Counter();jan_total=jan_fraud=Decimal(0)
with pay_path.open(newline='') as f:
 for r in csv.DictReader(f):
  totals[r['ip_country']]+=1
  if r['has_fraudulent_dispute']=='True':frauds[r['ip_country']]+=1
  if r['merchant']=='Belles_cookbook_store' and r['year']=='2023' and 1<=int(r['day_of_year'])<=31:
   jan_total+=Decimal(r['eur_amount'])
   if r['has_fraudulent_dispute']=='True':jan_fraud+=Decimal(r['eur_amount'])
checks['49_counts_vs_rates']={'total_records':sum(totals.values()),'fraud_records':sum(frauds.values()),'country_total':dict(totals),'country_fraud_count':dict(frauds),
 'country_fraud_rate':{k:frauds[k]/n for k,n in totals.items()},'count_leader':max(frauds,key=frauds.get),'rate_leader':max(totals,key=lambda k:frauds[k]/totals[k]),
 'task_text_alone_fixes_count_vs_rate':False}
checks['kimi1681_january_supplemental']={'monthly_eur_volume':str(jan_total),'fraud_eur_volume':str(jan_fraud),'fraud_amount_ratio':str(jan_fraud/jan_total),
 'interpretation':'if task is 10th day of 2023, monthly conditions use January; no claim to resolve ambiguous ordinal from hidden gold'}
with cr_path.open(newline='') as f:cr=list(csv.DictReader(f))
def num(r,k):
 try:return float(r[k])
 except (ValueError,TypeError):return None
def valid(r):return num(r,'Ph') is not None and 5<=num(r,'Ph')<=8.5 and all(num(r,k) is not None for k in ['N','P','K'])
filtered=[r for r in cr if valid(r)]
checks['agriculture29_temperature']={'all_rows':len(cr),'soil_filtered_rows':len(filtered),'per_crop':{}}
for crop in sorted({r['label'] for r in cr}):
 allrows=[r for r in cr if r['label']==crop];rows=[r for r in filtered if r['label']==crop]
 def stats(rs):
  xs=[num(r,'T2M_MAX-Su') for r in rs];ys=[num(r,'T2M_MIN-W') for r in rs]
  assert all(x is not None and y is not None for x,y in zip(xs,ys))
  return {'rows':len(rs),'mean_of_differences':statistics.mean(x-y for x,y in zip(xs,ys)),
          'difference_of_means':statistics.mean(xs)-statistics.mean(ys),'temperature_pair_missing':0}
 checks['agriculture29_temperature']['per_crop'][crop]={'all_samples':stats(allrows),'soil_filtered_samples':stats(rows)}
for inp in inputs:assert sha(Path(inp['path']))==inp['sha256']
checks['supplemental_inputs']=inputs
(ROOT/'checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
for k,v in checks.items():
 if k not in ['supplemental_inputs','agriculture29_temperature']:print(k,json.dumps(v,ensure_ascii=False))
print('agriculture29_Potato',json.dumps(checks['agriculture29_temperature']['per_crop']['Potato']))
