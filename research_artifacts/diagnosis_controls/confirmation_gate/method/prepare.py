import difflib, hashlib, importlib.util, json, shutil
from pathlib import Path
BASE=Path(__file__).resolve().parents[2]
WORK=Path(__file__).parent
ROOT=BASE/'outputs/diagnosis_confirmation_gate_v1_20261018'
OLD=BASE/'outputs/diagnosis_eight_native_tools_20261018'
ROUND1=BASE/'outputs/diagnosis_termination_control_v1_20261018'
ORIGINAL=BASE/'outputs/diagnosis_prompt_v1_20261018'
assert not ROOT.exists(), 'experiment_already_exists'
ROOT.mkdir()
CONTRACT=ROOT/'contract'
shutil.copytree(ORIGINAL,CONTRACT)
old_prompt=(ORIGINAL/'diagnosis.prompt.md').read_text()
# The only prompt change; no example, schema, causal rule or workflow changes.
GATE=('\n## Confirmation threshold\n\n'
      'Treat an issue as confirmed only when inspected original evidence supports a\n'
      'discrepancy between the agent\'s actual behavior and an explicit task requirement\n'
      'or data definition. If evidence is insufficient, the task leaves the interpretation\n'
      'open, or only a verification is missing, describe that limitation in summary;\n'
      'do not upgrade it to a definite error.\n')
anchor='\n# Step\n'
assert old_prompt.count(anchor)==1
new_prompt=old_prompt.replace(anchor,GATE+anchor)
(CONTRACT/'diagnosis.prompt.md').write_text(new_prompt)
(ROOT/'prompt.diff').write_text(''.join(difflib.unified_diff(old_prompt.splitlines(True),new_prompt.splitlines(True),fromfile='v1',tofile='v1+confirmation-threshold')))
(ROOT/'confirmation_rule.txt').write_text(GATE)
source=(BASE/'work/diagnosis_eight_native_tools_20261018/run.py').read_text()
source=source.replace("ROOT = BASE / 'outputs/diagnosis_eight_native_tools_20261018'", "ROOT = BASE / 'outputs/diagnosis_confirmation_gate_v1_20261018'")
source=source.replace("CONTRACT = BASE / 'outputs/diagnosis_prompt_v1_20261018'", "CONTRACT = ROOT / 'contract'")
start=source.index("MIRROR = Path(")
end=source.index('\n',start)
source=source[:start]+"MIRROR = BASE / 'work/diagnosis_confirmation_gate_v1_20261018/live'"+source[end:]
source=source.replace("'tools': TOOLS, 'tool_choice': 'auto',", "'tools': TOOLS, 'tool_choice': ('none' if int(directory.name) == 12 else 'auto'),",1)
# Native round-one probe cannot be reused after changing the system prompt.
start=source.index("            if rid == 'rawio-ea688bde06c727ee43b1' and round_no == 1:")
end=source.index("            state['model_rounds'] = round_no",start)
source=source[:start]+"            native, finish, receipt = client.ask(messages, directory / 'calls' / f'{round_no:02}')\n"+source[end:]
source=source.replace("            if calls:\n                # Preserve", "            if calls and round_no == 12:\n                state.update(status='provider_disobeyed_no_tools', reason='tools_requested_at_submission_round')\n                break\n            if calls:\n                # Preserve",1)
source=source.replace('诊断八条验证 · 固定prompt v1','第二轮诊断验证 · 仅增加已确认问题门槛')
source=source.replace("'prompt_unchanged': sha(CONTRACT / 'diagnosis.prompt.md') == plan['prompt_sha256'],", "'prepared_prompt_unchanged_during_run': sha(CONTRACT / 'diagnosis.prompt.md') == plan['prompt_sha256'],")
(WORK/'run.py').write_text(source)
spec=importlib.util.spec_from_file_location('confirmation_gate_runner',WORK/'run.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
old=b.read(OLD/'plan.json')
for job in old['jobs']:
 shutil.copy2(OLD/'inputs'/(job['record_id']+'.json'), ROOT/'inputs'/(job['record_id']+'.json')) if (ROOT/'inputs').exists() else None
(ROOT/'inputs').mkdir(exist_ok=True)
for job in old['jobs']:shutil.copy2(OLD/'inputs'/(job['record_id']+'.json'),ROOT/'inputs'/(job['record_id']+'.json'))
plan={**old,'schema':'diagnosis-confirmation-threshold-single-factor-v1','prepared_at':b.now(),
 'prompt_path':str(CONTRACT/'diagnosis.prompt.md'),'prompt_sha256':b.sha(CONTRACT/'diagnosis.prompt.md'),
 'original_v1_prompt_sha256':b.sha(ORIGINAL/'diagnosis.prompt.md'),'runner_sha256':b.sha(WORK/'run.py'),
 'comparison_baseline':'outputs/diagnosis_termination_control_v1_20261018',
 'single_changed_factor':'one generic confirmation-threshold paragraph; no example or causal-rule change',
 'termination_control':'round12 tool_choice=none; no new reminder messages; maximum12 rounds',
 'comparison_design':'eight fresh treatment runs; six historical completed round1 controls; two historical transport cases excluded from paired semantic comparison',
 'probe_reuse':None,'prompt_transformation':'insert one confirmation threshold before Step; original examples/schema/other text byte-identical',
 'preserved_control_hashes':{str(p.relative_to(BASE)):b.sha(p) for root in [OLD,ROUND1] for p in root.rglob('*') if p.is_file() and p.name in ['plan.json','diagnosis.json','semantic_review.json','result.json']},
 'preregistered_review':{'independent_gold':False,'reviewer':'local source-checked author review; not blinded',
 'unit':'deduplicated substantive problem assertions; ambiguous-only summary notes are not confirmed errors',
 'separate_buckets':['unsupported confirmed discrepancy','unsupported causal claim (unchanged rules)','unsupported technical/factual claim','supported discrepancy retained/lost','schema/citation defects','transport failures'],
 'compare_only_cases_with_two_final_outputs':True,'no_counting_empty_findings_as_accuracy':True,
 'no_claim_causal_prompt_effect_from_one_nonrandom_repetition':True}}
b.save(ROOT/'plan.json',plan)
b.save(ROOT/'tools.json',b.TOOLS)
index=b.EvidenceIndex(b.BUNDLE)
checks=b.offline_checks(index)
assert len(checks)==8
# Exact same initial input bytes and rendered initial packet, all eight.
for job in plan['jobs']:
 assert b.sha(ROOT/'inputs'/(job['record_id']+'.json'))==job['input_sha256']
 assert b.SafeEvidence(index,job['record_id']).initial()==b.read(ROOT/'inputs'/(job['record_id']+'.json'))
# Freeze examples and schema; remove only inserted gate to reconstruct v1 exactly.
assert new_prompt.replace(GATE,'',1)==old_prompt
for p in ORIGINAL.rglob('*'):
 if p.is_file() and p.name!='diagnosis.prompt.md':assert b.sha(p)==b.sha(CONTRACT/p.relative_to(ORIGINAL))
checks_summary={'initial_packets_and_bytes_identical':8,'source_and_negative_citation_checks':checks,
 'schema_and_examples_unchanged':True,'single_insert_reconstructs_v1':True,
 'round12_tool_choice':'none','round1_11_tool_choice':'auto','new_calls_so_far':0,
 'frozen_processor_files_unchanged':b.verify_processor()}
b.save(ROOT/'offline_checks.json',checks_summary)
(ROOT/'method').mkdir()
shutil.copy2(WORK/'run.py',ROOT/'method/run.py')
shutil.copy2(Path(__file__),ROOT/'method/prepare.py')
b.verify_plan()
print(json.dumps({'prepared':8,'v1_reconstructed':True,'frozen_files':checks_summary['frozen_processor_files_unchanged'],'prompt_bytes_added':len(GATE.encode())}))
