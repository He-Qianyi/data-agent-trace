"""Offline regression of command boundaries and conservative script lineage."""
import unittest

from pipeline.stages.trace_processor.fact_layer.extract import shell_facts as frozen_shell_facts
from pipeline.stages.trace_processor.fact_layer_v2.extract import shell_facts, extract_action
from pipeline.stages.trace_processor.fact_layer_v2.build import link_script_revisions


class ShellBoundaryTests(unittest.TestCase):
    def runs(self, command):
        return [r["path"] for r in shell_facts(command, {})[2]]

    def test_newlines_and_control_operators(self):
        for separator in ("\n", "\n\n", "\n\n\n", "\n \t\n", ";", "&&", "||", "|", "&", ";\n\n"):
            with self.subTest(separator=repr(separator)):
                self.assertEqual(self.runs("echo ready " + separator + " python3 analysis.py"), ["analysis.py"])

    def test_old_reproduction_is_fixed(self):
        command = "echo ready\n\npython3 analysis.py"
        self.assertEqual(frozen_shell_facts(command, {})[2], [])
        self.assertEqual(self.runs(command), ["analysis.py"])

    def test_multiple_script_commands_end_at_each_boundary(self):
        self.assertEqual(self.runs("python3 first.py\n\npython3 second.py\npython3 third.py"),
                         ["first.py", "second.py", "third.py"])

    def test_quoted_boundaries_are_arguments_not_commands(self):
        for argument in (";", "&&", "||", "\n", "\n\n", "a\npython3 fake.py", "python3 fake.py"):
            for quote in ("'", '"'):
                with self.subTest(argument=repr(argument), quote=quote):
                    self.assertEqual(self.runs("echo " + quote + argument + quote + " python3 fake.py"), [])

    def test_quoted_script_argument_does_not_end_at_a_literal_newline(self):
        self.assertEqual(self.runs("python3 '\n\n'"), ["\n\n"])

    def test_comments_do_not_create_invocations(self):
        for command in ("# python3 fake.py", "echo ready # ; python3 fake.py", "echo ready\n# python3 fake.py"):
            self.assertEqual(self.runs(command), [])
        self.assertEqual(self.runs("# python3 fake.py\n\npython3 real.py"), ["real.py"])

    def test_escaped_separator_stays_in_argument(self):
        self.assertEqual(self.runs(r"echo \; python3 fake.py"), [])

    def test_shell_line_continuation(self):
        self.assertEqual(self.runs("python3 \\\nanalysis.py"), ["analysis.py"])
        self.assertEqual(self.runs("echo prefix\\\npython3 fake.py"), [])

    def test_quoted_inline_python_is_preserved(self):
        code = "print('one'); print('python3 fake.py')\nprint('two')"
        units, files, runs, _ = shell_facts('python3 -c "' + code + '"', {})
        self.assertEqual(units[0]["code"], code)
        self.assertEqual(units[0]["context"], "submitted_inline_program")
        self.assertEqual(files, [])
        self.assertEqual(runs, [])

    def test_quoted_sql_is_not_split_at_semicolon(self):
        units, _, runs, _ = shell_facts('sqlite3 db.sqlite "SELECT 1; SELECT 2;"\n\npython3 check.py', {})
        self.assertEqual(units[0]["code"], "SELECT 1; SELECT 2;")
        self.assertEqual([r["path"] for r in runs], ["check.py"])

    def test_heredoc_body_is_definition_and_offsets_stay_exact(self):
        command = "cat > analysis.py <<'PY'\n# python3 fake.py\nprint('hello')\nPY\n\npython3 analysis.py"
        units, files, runs, _ = shell_facts(command, {})
        self.assertEqual([r["path"] for r in runs], ["analysis.py"])
        self.assertEqual(units[0]["context"], "file_definition")
        self.assertEqual(files[0]["content"], units[0]["code"])
        ref = units[0]["source_ref"]
        self.assertEqual(command[ref["char_start"]:ref["char_end"]], units[0]["code"])

    def test_write_only_has_no_run(self):
        self.assertEqual(self.runs("cat > analysis.py <<'PY'\nprint('python3 fake.py')\nPY\n\n"), [])

    def test_inline_heredoc_does_not_invent_nested_shell_invocation(self):
        units, _, runs, _ = shell_facts("python3 <<'PY'\nprint('python3 fake.py')\nPY\n", {})
        self.assertEqual(units[0]["context"], "submitted_inline_program")
        self.assertEqual(runs, [])

    def test_heredoc_like_quoted_text_is_not_a_file_write(self):
        for command in ("echo 'cat > fake.py <<EOF\npython3 fake.py\nEOF'",
                        "# cat > fake.py <<EOF\n# python3 fake.py\n# EOF"):
            units, files, runs, _ = shell_facts(command, {})
            self.assertEqual((units, files, runs), ([], [], []))

    def test_environment_prefix_and_absolute_interpreter(self):
        self.assertEqual(self.runs("X=one Y='two words' /opt/venv/bin/python3 '/tmp/my analysis.py'"),
                         ["/tmp/my analysis.py"])
        self.assertEqual(self.runs("echo X=one python3 fake.py"), [])

    def test_unbalanced_quotes_are_not_certified(self):
        units, _, runs, status = shell_facts("echo ready\n\npython3 'broken.py", {})
        self.assertEqual(runs, [])
        self.assertEqual(status, "unparsed_shell_quoting")

    def step(self, command, failed=False):
        call = {"tool_name": "Bash", "arguments": {"command": command},
                "arguments_raw": {"command": command}, "arguments_ref": {}}
        return {"step_id": "S0001", "action": {**call, "static_extraction": extract_action(call)},
                "observation": {"failure_signals": {"nonzero_exit_code": failed}}}

    def test_same_command_write_run_has_candidate_not_runtime_certification(self):
        step = self.step("cat > analysis.py <<'PY'\nimport pandas as pd\npd.read_csv('input.csv')\nPY\n\npython3 analysis.py")
        revisions = link_script_revisions([step], "/work")
        run = step["action"]["static_extraction"]["script_invocations"][0]
        self.assertEqual(run["candidate_revision_ref"], revisions[0]["revision_id"])
        self.assertFalse(run["exact_runtime_content_verified"])
        self.assertFalse(revisions[0]["write_success_certified"])

    def test_failed_result_does_not_erase_submitted_invocation(self):
        step = self.step("cat > analysis.py <<'PY'\nraise RuntimeError()\nPY\n\npython3 analysis.py", failed=True)
        link_script_revisions([step], "/work")
        run = step["action"]["static_extraction"]["script_invocations"][0]
        self.assertEqual(run["path"], "analysis.py")
        self.assertIsNone(run["candidate_revision_ref"])
        self.assertFalse(run["exact_runtime_content_verified"])

    def test_write_edit_run_keeps_the_latest_recorded_candidate(self):
        steps = []
        for sid, name, args in (
            ("S0001", "Write", {"file_path": "analysis.py", "content": "print('v1')"}),
            ("S0002", "Edit", {"file_path": "analysis.py", "old_string": "v1", "new_string": "v2"}),
        ):
            call = {"tool_name": name, "arguments": args, "arguments_raw": args, "arguments_ref": {}}
            steps.append({"step_id": sid, "action": {**call, "static_extraction": extract_action(call)},
                          "observation": {"failure_signals": {}}})
        run_step = self.step("echo ready\n\npython3 /work/analysis.py")
        run_step["step_id"] = "S0003"
        steps.append(run_step)
        revisions = link_script_revisions(steps, "/work")
        run = run_step["action"]["static_extraction"]["script_invocations"][0]
        self.assertEqual(run["candidate_revision_ref"], "V0002")
        self.assertEqual(revisions[1]["previous_revision_ref"], "V0001")
        self.assertEqual(revisions[1]["content"], "print('v2')")
        self.assertFalse(run["exact_runtime_content_verified"])


if __name__ == "__main__":
    unittest.main()
