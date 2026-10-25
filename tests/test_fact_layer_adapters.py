"""Offline fixtures test evidence boundaries, not semantic labels or benchmark answers."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from pipeline.stages.trace_processor.fact_layer.adapters import (
    AnthropicRelayAdapter, CodexNativeAdapter, DirectResponsesAdapter, select)
from pipeline.stages.trace_processor.fact_layer.build import build, observation, link_script_revisions, validate
from pipeline.stages.trace_processor.fact_layer.extract import extract_action, python_facts, shell_facts
from pipeline.stages.trace_processor.fact_layer.sources import Sources, digest


def dump(path, value, lines=False):
    path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in value) if lines else json.dumps(value, ensure_ascii=False), encoding="utf-8")


def anthropic(call=None, history=None, rid="r1"):
    return {"incoming_anthropic_request": {"model": "fixture", "system": "system", "tools": [{"name": "Bash"}],
                                          "messages": history or [{"role": "user", "content": "task"}]},
            "outgoing_anthropic_response": {"id": rid, "content": [call] if call else [{"type": "text", "text": "done"}],
                                            "stop_reason": "tool_use" if call else "end_turn"},
            "outgoing_soclaas_responses_request": {"model": "fixture", "input": "task"},
            "incoming_soclaas_responses_response": {"status": "completed", "output": []}}


class FactLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = Sources()

    def feed(self, adapter_cls, rows, name="model.jsonl"):
        p = self.root / name
        dump(p, rows, lines=True)
        adapter = adapter_cls(self.sources)
        for row, ref in self.sources.records(p, "model_io"):
            adapter.ingest(row, ref)
        return adapter

    def call(self, arguments, name="Bash"):
        return {"type": "tool_use", "id": "c1", "name": name, "input": arguments}

    def fixture(self):
        call = self.call({"command": "python3 -c 'print(1)'"})
        result = {"type": "tool_result", "tool_use_id": "c1", "content": "1\n", "is_error": False}
        ledger = self.root / "turn_model_io.jsonl"
        dump(ledger, [anthropic(call), anthropic(history=[{"role": "assistant", "content": [call]}, {"role": "user", "content": [result]}], rid="r2")], lines=True)
        dump(self.root / "events.jsonl", [{"type": "assistant", "message": {"content": [call]}},
                                           {"type": "user", "message": {"content": [result]}, "tool_use_result": {"stdout": "1\n", "stderr": ""}}], lines=True)
        dump(self.root / "result.json", {"return_code": 0, "timed_out": False, "final_text": "done"})
        (self.root / "prompt.txt").write_text("Task: calculate 1", encoding="utf-8")
        row = {"record_id": "fixture", "attempt_directory": str(self.root), "recording_level": "actual_request_response",
               "model_io_path": str(ledger), "eligible_strict_completed": True, "strict_recording_complete": True,
               "completion": {"state": "ended_normal"}, "sources": [
                   {"path": str(ledger), "role": "model_io"}, {"path": str(self.root / "events.jsonl"), "role": "transcript"},
                   {"path": str(self.root / "prompt.txt"), "role": "task_prompt"}],
               "declared_source_hashes": {str(ledger): digest(ledger)}}
        return row

    def test_unicode_separators_do_not_split_jsonl(self):
        p = self.root / "unicode.jsonl"
        dump(p, [{"text": "a\u2028b\u0085c\u2029d"}, [{"text": "last"}]], lines=True)
        values = list(self.sources.records(p, "fixture"))
        self.assertEqual(len(values), 2)
        self.assertEqual(values[1][1]["line"], 2)
        for value, ref in values:
            self.assertEqual(self.sources.resolve(ref), value)

    def test_json_array_and_escaped_pointers(self):
        p = self.root / "array.json"
        dump(p, [{"a/b": {"~name": 7}}])
        value, ref = next(self.sources.records(p, "fixture"))
        self.assertEqual(self.sources.resolve({**ref, "json_pointer": "/0/a~1b/~0name"}), 7)

    def test_saved_registry_can_reopen_jsonl_request(self):
        p = self.root / "saved.jsonl"
        dump(p, [{"request": {"text": "full\u2028context"}}, {"response": "yes"}], lines=True)
        records = list(self.sources.records(p, "model_io"))
        reopened = Sources.from_entries(copy.deepcopy(self.sources.entries))
        self.assertEqual(reopened.resolve({**records[0][1], "json_pointer": "/request"}), {"text": "full\u2028context"})

    def test_sources_detect_mutation(self):
        p = self.root / "raw.txt"
        p.write_text("a")
        self.sources.add(p, "fixture")
        p.write_text("b")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            self.sources.assert_unchanged()

    def test_recording_shape_not_benchmark_selects_adapter(self):
        self.assertIsInstance(select(anthropic(), self.sources), AnthropicRelayAdapter)
        self.assertIsInstance(select({"native_capture_type": "codex_rollout_trace"}, self.sources), CodexNativeAdapter)
        self.assertIsInstance(select({"native_capture_type": "direct_serialized_fetch"}, self.sources), DirectResponsesAdapter)
        with self.assertRaises(ValueError):
            select({"messages": []}, self.sources)

    def test_generated_call_without_receipt_not_execution(self):
        adapter = self.feed(AnthropicRelayAdapter, [anthropic(self.call({"command": "ls"}))])
        self.assertEqual(set(adapter.calls), {"c1"})
        self.assertFalse(adapter.accepted)
        self.assertFalse(adapter.receipts)

    def test_repeated_request_histories_are_not_new_steps(self):
        c = self.call({"command": "ls"})
        r = {"type": "tool_result", "tool_use_id": "c1", "content": "actual"}
        history = [{"role": "assistant", "content": [c]}, {"role": "user", "content": [r]}]
        adapter = self.feed(AnthropicRelayAdapter, [anthropic(c), anthropic(history=history, rid="r2"), anthropic(history=history, rid="r3")])
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(len(adapter.receipts["c1"]), 1)

    def test_generated_and_submitted_arguments_remain_separate(self):
        generated, submitted = self.call({"command": "cd /work && ls"}), self.call({"command": "ls"})
        adapter = self.feed(AnthropicRelayAdapter, [anthropic(generated), anthropic(history=[{"role": "assistant", "content": [submitted]}], rid="r2")])
        self.assertEqual(adapter.calls["c1"]["arguments"]["command"], "cd /work && ls")
        self.assertEqual(adapter.calls["c1"]["submission_variants"][0]["arguments"]["command"], "ls")

    def test_duplicate_generated_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate tool identity"):
            self.feed(AnthropicRelayAdapter, [anthropic(self.call({"command": "ls"})), anthropic(self.call({"command": "pwd"}), rid="r2")])

    def test_direct_responses_serialization_variants(self):
        c = {"type": "function_call", "call_id": "c1", "name": "Bash", "arguments": '{"command": "ls"}'}
        replay = {**c, "arguments": '{"command":"ls"}'}
        adapter = self.feed(DirectResponsesAdapter, [
            {"model_input": {"input": []}, "model_output": {"id": "r1", "output": [c]}},
            {"model_input": {"input": [replay, {"type": "function_call_output", "call_id": "c1", "output": "ok"}]}, "model_output": {"id": "r2", "output": []}}])
        self.assertEqual(adapter.calls["c1"]["arguments_raw"], c["arguments"])
        self.assertEqual(len(adapter.calls["c1"]["serialization_variants"]), 1)

    def test_native_parse_error_is_retained_without_response_item(self):
        c = {"type": "function_call", "call_id": "c1", "name": "write_stdin", "arguments": '{"session_id":"bad"}'}
        adapter = self.feed(CodexNativeAdapter, [{"model_input": {"input": []}, "model_output": {"response_id": "r1", "output_items": [c]}}])
        p = self.root / "tools.jsonl"
        dump(p, [{"schema": "codex-native-tool-execution-v1", "tool_call_id": "n1", "model_visible_call_id": "c1", "status": "failed",
                  "invocation": {"tool_name": "write_stdin", "payload": {"arguments": c["arguments"]}},
                  "result": {"type": "error", "error": "invalid argument type"}}], lines=True)
        adapter.native_tools(self.sources.records(p, "native_tool_execution_ledger"))
        obs = observation(adapter.receipts["c1"])
        self.assertEqual(obs["text"], "invalid argument type")
        self.assertTrue(obs["failure_signals"]["native_status_failed"])
        self.assertIsNone(obs["exit_code_as_recorded"])
        self.assertNotIn("response_item", obs["variants"][0]["payload"])

    def test_native_host_internal_calls_are_not_agent_steps(self):
        adapter = CodexNativeAdapter(self.sources)
        adapter.native_tools([({"schema": "codex-native-tool-execution-v1", "tool_call_id": "internal"}, {"source_id": "fixture"})])
        self.assertEqual(len(adapter.native_internal), 1)
        self.assertFalse(adapter.accepted)

    def test_output_surfaces_are_not_rewritten(self):
        receipts = [
            {"kind": "framework_tool_result", "payload": {"content": "Error: x"}, "source_ref": {}},
            {"kind": "model_request_replay", "payload": {"output": "<system>ERROR: Tool execution failed.</system>\nError: x"}, "source_ref": {}}]
        obs = observation(receipts)
        self.assertTrue(obs["different_recorded_text_variants"])
        self.assertEqual(obs["text"], "Error: x")
        self.assertEqual(obs["variants"][1]["payload"], receipts[1]["payload"])
        self.assertTrue(obs["failure_signals"]["explicit_error_wrapper"])

    def test_native_completed_and_failed_process_are_distinct(self):
        obs = observation([{"kind": "native_tool_result", "metadata": {"status": "completed"}, "source_ref": {},
                            "payload": {"type": "direct_response", "response_item": {"output": "Chunk ID: abc\nWall time: 1 seconds\nProcess exited with code 1\nOriginal token count: 2\nOutput:\nfail"}}}])
        self.assertEqual(obs["tool_status_as_recorded"], "completed")
        self.assertTrue(obs["failure_signals"]["nonzero_exit_code"])
        self.assertFalse(obs["failure_signals"]["native_status_failed"])

    def test_stdout_words_do_not_invent_an_exit_code(self):
        obs = observation([{"kind": "framework_tool_result", "source_ref": {}, "payload": {"content": "Example: Process exited with code 9\n"}}])
        self.assertIsNone(obs["exit_code_as_recorded"])

    def test_truncation_marker_retained(self):
        obs = observation([{"kind": "native_tool_result", "metadata": {"status": "completed"}, "source_ref": {},
                            "payload": {"type": "direct_response", "response_item": {"output": "Warning: truncated output (original token count: 20)\nactual"}}}])
        self.assertEqual(obs["truncation"]["status"], "explicit_marker")

    def test_heredoc_definition_is_not_execution(self):
        cmd = "cat > solution.py <<'PY'\nimport pandas as pd\ndf = pd.read_csv('input.csv')\nPY\npython3 solution.py"
        units, files, runs, _ = shell_facts(cmd, {"source_id": "fixture", "json_pointer": ""})
        self.assertEqual(units[0]["context"], "file_definition")
        self.assertEqual(files[0]["path"], "solution.py")
        self.assertEqual(runs[0]["path"], "solution.py")
        self.assertEqual(cmd[units[0]["source_ref"]["char_start"]:units[0]["source_ref"]["char_end"]], units[0]["code"])

    def test_quoted_python_text_is_not_a_shell_invocation(self):
        _, _, runs, _ = shell_facts("echo 'python3 nonexistent.py'", {})
        self.assertFalse(runs)

    def test_sql_is_retained_with_enclosing_result_scope(self):
        ops, _ = python_facts("import sqlite3\nc = sqlite3.connect('db.sqlite')\nc.execute('SELECT SUM(x) FROM t')\n")
        sql = next(o for o in ops if o["operation"] == "sql_api")
        self.assertEqual(sql["sql_text"], "SELECT SUM(x) FROM t")
        self.assertFalse(sql["statement_execution_verified"])
        self.assertEqual(sql["individual_result_binding"], "not_recorded_separately")

    def test_dynamic_sql_is_not_evaluated(self):
        ops, _ = python_facts('c.execute(f"SELECT * FROM {table}")')
        self.assertIsNone(ops[0]["sql_text"])
        self.assertIn("table", ops[0]["sql_expression"])

    def test_reassigned_sql_variable_is_not_substituted(self):
        ops, _ = python_facts("q='SELECT 1'\nc.execute(q)\nq='SELECT 2'\nc.execute(q)")
        self.assertTrue(all(o["sql_text"] is None for o in ops))

    def test_future_sql_assignment_is_not_substituted(self):
        ops, _ = python_facts("c.execute(q)\nq='SELECT 2'")
        self.assertIsNone(ops[0]["sql_text"])

    def test_invalid_python_preserves_unparsed_status(self):
        ops, status = python_facts("print(unterminated")
        self.assertEqual(ops, [])
        self.assertEqual(status["status"], "unparsed")

    def test_stdin_language_is_unknown(self):
        a = {"tool_name": "write_stdin", "arguments_raw": {"chars": "print(1)"}, "arguments": {"chars": "print(1)"}, "arguments_ref": {}}
        self.assertEqual(extract_action(a)["code_units"][0]["language"], "unknown_interactive_input")

    def test_build_preserves_request_and_result_and_no_semantics(self):
        row = self.fixture()
        before = digest(row["model_io_path"])
        trace = build(row)
        self.assertEqual(len(trace["model_exchanges"]), 2)
        self.assertEqual(len(trace["steps"]), 1)
        self.assertEqual(trace["steps"][0]["observation"]["text"], "1\n")
        self.assertFalse(trace["quality"]["semantic_annotation_run"])
        self.assertEqual(digest(row["model_io_path"]), before)

    def test_build_uses_framework_arguments_and_keeps_generated_body(self):
        row = self.fixture()
        c = self.call({"command": "cd /work && python3 -c 'print(1)'"})
        ledger = self.root / "turn_model_io.jsonl"
        with ledger.open() as f:
            values = [json.loads(line) for line in f]
        values[0]["outgoing_anthropic_response"]["content"] = [c]
        dump(ledger, values, lines=True)
        row["declared_source_hashes"] = {}
        trace = build(row)
        action = trace["steps"][0]["action"]
        self.assertEqual(action["arguments"]["command"], "python3 -c 'print(1)'")
        self.assertEqual(action["generated_call"]["arguments"]["command"], c["input"]["command"])
        self.assertTrue(trace["quality"]["warnings"])

    def test_source_mismatch_is_detected_by_output_validator(self):
        row = self.fixture()
        trace = build(row)
        reopened = Sources.from_entries(trace["sources"])
        trace["steps"][0]["observation"]["variants"][0]["payload"]["content"] = "invented"
        with self.assertRaisesRegex(ValueError, "Tool output differs"):
            validate(trace, reopened)

    def test_audited_hash_drift_rejected(self):
        row = self.fixture()
        row["declared_source_hashes"][row["model_io_path"]] = "bad"
        with self.assertRaisesRegex(ValueError, "audited hash"):
            build(row)

    def test_context_only_recording_excluded(self):
        row = self.fixture()
        row["eligible_strict_completed"] = False
        with self.assertRaisesRegex(ValueError, "strictly complete"):
            build(row)

    def test_missing_return_does_not_pass(self):
        row = self.fixture()
        c = self.call({"command": "ls"})
        dump(self.root / "turn_model_io.jsonl", [anthropic(c)], lines=True)
        dump(self.root / "events.jsonl", [{"type": "assistant", "message": {"content": [c]}}], lines=True)
        row["declared_source_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "no recorded result"):
            build(row, verify_capture=False)

    def test_official_zero_score_remains_zero(self):
        row = self.fixture()
        p = self.root / "evaluation.json"
        dump(p, {"score": 0, "status": "evaluated"})
        row["historical_evaluation"] = {"paths": [str(p)]}
        trace = build(row)
        self.assertEqual(trace["evaluation"]["records"][0]["payload"]["score"], 0)
        self.assertFalse(trace["evaluation"]["recomputed"])
        self.assertFalse(trace["evaluation"]["records"][0]["available_to_agent"])

    def test_csv_snapshot_is_after_run_only(self):
        row = self.fixture()
        dump(self.root / "task.json", {"output_file_name": ["output.csv"]})
        (self.root / "workspace").mkdir()
        (self.root / "workspace" / "output.csv").write_text('x,y\n1,"two\nlines"\n')
        trace = build(row)
        artifact = trace["deliverables"][0]
        self.assertEqual(artifact["snapshot"]["data_rows_as_stored"], 1)
        self.assertTrue(artifact["snapshot"]["not_an_execution_time_measurement"])
        self.assertFalse(artifact["agent_creation_certified"])


if __name__ == "__main__":
    unittest.main()
