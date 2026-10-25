"""Synthetic source-backed recovery tests; no task names, network or trace replay."""
import ast
import unittest
from pipeline.stages.trace_processor.fact_layer_v3.recovery import recorded_cwd, python_script_writes
from pipeline.stages.trace_processor.fact_layer_v3.extract import literal, shell_facts, extract_action, python_facts
from pipeline.stages.trace_processor.fact_layer_v3.build import link_script_revisions


def action(command):
    return {"tool_name": "Bash", "arguments_raw": {"command": command}, "arguments": {"command": command},
            "arguments_ref": {"source_id": "source", "json_pointer": "/args"}}


def step(n, command, failed=False):
    a=action(command);a['static_extraction']=extract_action(a)
    return {'step_id':f'S{n:04d}', 'action':a, 'observation':{'failure_signals':{'explicit_failure':failed}}}


class NullRecoveryTests(unittest.TestCase):
    def test_cwd_is_exactly_source_slice(self):
        text='Rules.\nThe current working directory is `/mnt/Project With Spaces`';ref={'source_id':'s','line':3,'json_pointer':''}
        cwd,source=recorded_cwd([({'model_input':{'instructions':text}},ref)])
        self.assertEqual(cwd,'/mnt/Project With Spaces');self.assertEqual(text[source['char_start']:source['char_end']],cwd)
        self.assertEqual(source['json_pointer'],'/model_input/instructions')

    def test_cwd_does_not_search_task_messages_or_later_rows(self):
        self.assertEqual(recorded_cwd([({'model_input':{'input':[{'text':'The current working directory is `/fake`'}]}}, {})]),(None,None))
        rows=[({'model_input':{}},{}),({'model_input':{'instructions':'The current working directory is `/later`'}},{})]
        self.assertEqual(recorded_cwd(rows),(None,None))

    def test_ambiguous_or_relative_directory_remains_unknown(self):
        for text in ['The current working directory is `relative`','The current working directory is `/a`\nThe current working directory is `/b`']:
            self.assertEqual(recorded_cwd([({'model_input':{'instructions':text}}, {})]),(None,None))

    def test_constant_fstrings_and_parameter_placeholders(self):
        ops,_=python_facts('cursor.execute(f"SELECT * FROM any_table WHERE key = %s", args)')
        self.assertEqual(ops[0]['sql_text'],'SELECT * FROM any_table WHERE key = %s')
        self.assertEqual(ops[0]['parameters_expression'],'args')

    def test_interpolation_is_not_guessed(self):
        for expr in ['f"SELECT * FROM {name}"','f"SELECT {7}"','f"SELECT {value!r}"']:
            self.assertIsNone(literal(ast.parse(expr,mode='eval').body,{}))

    def test_shell_path_escaping_and_redirection_positions(self):
        for header in [r"cat > /mnt/Project\ Space/job.py <<'END'",r"cat <<'END' > /mnt/Project\ Space/job.py",'cat > "/mnt/Project Space/job.py" <<\'END\'']:
            _,files,_,_=shell_facts(header+'\nprint(4)\nEND\n',{})
            self.assertEqual(files[0]['path'],'/mnt/Project Space/job.py');self.assertEqual(files[0]['content'],'print(4)\n')

    def test_shell_path_is_not_repaired_by_appearance(self):
        _,files,_,_=shell_facts(r"cat > /mnt/Project\Space/job.py <<'END'"+'\nprint(4)\nEND\n',{})
        self.assertEqual(files[0]['path'],'/mnt/ProjectSpace/job.py')

    def test_guarded_cd_binds_known_definition(self):
        steps=[step(1,"cat > '/data/Other Space/job.py' <<'END'\nprint(3)\nEND\n"),step(2,"cd '/data/Other Space' && python3 job.py")]
        versions=link_script_revisions(steps,'/root/work');run=steps[1]['action']['static_extraction']['script_invocations'][0]
        self.assertEqual(run['candidate_revision_ref'],versions[0]['revision_id']);self.assertFalse(run['exact_runtime_content_verified'])

    def test_tool_workdir_is_used_for_matching(self):
        steps=[step(1,"cat > /somewhere/job.py <<'END'\nprint(3)\nEND\n"),step(2,'python3 job.py')]
        steps[1]['action']['arguments']['workdir']='/somewhere';link_script_revisions(steps,'/elsewhere')
        self.assertEqual(steps[1]['action']['static_extraction']['script_invocations'][0]['candidate_revision_ref'],'V0001')

    def test_cd_quoted_text_or_or_branch_does_not_establish_directory(self):
        for cmd in ['echo "cd /other"; python3 job.py','cd /other || python3 job.py','cd "$ROOT" && python3 job.py']:
            runs=shell_facts(cmd,{})[2];self.assertNotIn('command_cwd_as_written',runs[0])

    def test_python_write_then_edit_reconstructs_candidate(self):
        code="script = 'print(3)\\n'\nwith open('/tmp/new_script.py', 'w') as output:\n    output.write(script)\n"
        steps=[step(1,"python3 <<'END'\n"+code+'END\n')]
        edit={'tool_name':'Edit','arguments_raw':{'file_path':'/tmp/new_script.py','old_string':'3','new_string':'4'},'arguments':{'file_path':'/tmp/new_script.py','old_string':'3','new_string':'4'},'arguments_ref':{'source_id':'s','json_pointer':'/args'}}
        edit['static_extraction']=extract_action(edit);steps.append({'step_id':'S0002','action':edit,'observation':{'failure_signals':{}}})
        versions=link_script_revisions(steps,'/tmp');self.assertEqual(len(versions),2);self.assertEqual(versions[1]['content'],'print(4)\n');self.assertEqual(versions[1]['status'],'reconstructed_from_recorded_edit')

    def test_definition_only_code_does_not_run_nested_writer(self):
        args={'file_path':'/tmp/generator.py','content':"with open('/tmp/generated.py','w') as f:\n f.write('print(2)')"}
        a={'tool_name':'Write','arguments_raw':args,'arguments':args,'arguments_ref':{}}
        self.assertEqual([x['path'] for x in extract_action(a)['file_actions']],['/tmp/generator.py'])

    def test_dynamic_and_conditional_python_writes_stay_unknown(self):
        for code in ["with open('/tmp/job.py','w') as f:\n f.write(dynamic)","if ready:\n with open('/tmp/job.py','w') as f:\n  f.write('print(1)')","for i in range(2):\n open('/tmp/job.py','w').write('print(1)')"]:
            self.assertEqual(python_script_writes(code,{},literal),[])

    def test_append_is_not_a_complete_definition(self):
        self.assertEqual(python_script_writes("with open('/tmp/job.py','a') as f:\n f.write('print(1)')",{},literal),[])

    def test_failure_keeps_candidate_unknown(self):
        steps=[step(1,"cat > /tmp/job.py <<'END'\nprint(1)\nEND\n",failed=True),step(2,'python3 /tmp/job.py')]
        link_script_revisions(steps,'/tmp');self.assertIsNone(steps[1]['action']['static_extraction']['script_invocations'][0]['candidate_revision_ref'])

    def test_sql_cli_dot_command_has_no_sql(self):
        units,_,_,_=shell_facts("sqlite3 file.db '.tables'",{});self.assertIsNone(units[0]['code']);self.assertEqual(units[0]['command_arguments_as_written'],['file.db','.tables'])

    def test_missing_exit_code_and_error_fields_are_not_guessed(self):
        from pipeline.stages.trace_processor.fact_layer_v3.build import observation
        o=observation([{'kind':'model_request_replay','payload':{'content':'done'},'source_ref':{}}])
        self.assertIsNone(o['exit_code_as_recorded']);self.assertIsNone(o['is_error_as_recorded'])


if __name__=='__main__':unittest.main()
