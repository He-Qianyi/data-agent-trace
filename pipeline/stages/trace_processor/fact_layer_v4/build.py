"""Construct a deterministic fact layer from strictly eligible completed recordings."""
import csv
import importlib
import json
from pathlib import Path
import re
import sys

from . import SCHEMA_VERSION
from .adapters import select, content_text, canonical
from .extract import extract_action, python_facts, sha_text
from .sources import Sources, child
from .recovery import recorded_cwd, invocation_cwd, resolve_directory


def fresh_capture_check(path):
    # Reuse the project's read-only capture verifier; never invoke extract/apply/rerun.
    scripts = str(Path(__file__).resolve().parents[4] / "scripts")
    sys.path.insert(0, scripts)
    try:
        return importlib.import_module("curate_complete_traces").check_ledger(Path(path))
    finally:
        sys.path.remove(scripts)


def observation(receipts):
    priority = {"native_tool_result": 0, "framework_tool_result": 1, "model_request_replay": 2}
    receipts = sorted(receipts, key=lambda r: priority[r["kind"]])
    result = {"variants": receipts, "preferred_variant": 0 if receipts else None,
              "tool_status_as_recorded": None, "is_error_as_recorded": None,
              "exit_code_as_recorded": None, "truncation": {"status": "not_explicitly_marked"}}
    if not receipts:
        result["availability"] = "missing"
        return result
    first = receipts[0]
    payload, meta = first["payload"], first.get("metadata")
    output = payload
    if isinstance(payload, dict):
        if payload.get("type") == "direct_response":
            output = payload.get("response_item", {}).get("output")
        elif payload.get("type") == "error":
            output = payload.get("error")
        else:
            output = payload.get("content", payload.get("output"))
        if "is_error" in payload:
            result["is_error_as_recorded"] = payload["is_error"]
    if first["kind"] == "native_tool_result":
        result["tool_status_as_recorded"] = meta["status"]
    text = content_text(output)
    result.update(availability="recorded", output=output, text=text)
    # Read only known harness envelope positions, not arbitrary printed 'exit' words.
    codex = re.match(r"^Chunk ID: [^\n]+\nWall time: [^\n]+\nProcess exited with code (-?\d+)\n", text)
    claude = re.match(r"^Exit code: (-?\d+)\n", text)
    if codex or claude:
        result["exit_code_as_recorded"] = int((codex or claude).group(1))
        result["exit_code_evidence"] = {"kind": "tool_harness_text_envelope", "source_ref": first["source_ref"]}
    if first["kind"] == "native_tool_result":
        match = re.search(r"(?m)^(?:Warning: )?(?:[Oo]utput (?:was |is )?truncated|[Tt]runcated output|…[0-9]+ (?:chars|tokens) truncated)", text)
        if match:
            result["truncation"] = {"status": "explicit_marker", "marker": match.group(0), "source_ref": first["source_ref"]}
    if "<system>ERROR: Tool execution failed.</system>" in text:
        result["error_wrapper_present"] = True
    if isinstance(meta, dict) and first["kind"] == "framework_tool_result":
        result["framework_fields_as_recorded"] = meta
    result["failure_signals"] = {"native_status_failed": result["tool_status_as_recorded"] == "failed",
                                 "is_error_true": result["is_error_as_recorded"] is True,
                                 "nonzero_exit_code": result["exit_code_as_recorded"] not in (None, 0),
                                 "explicit_error_wrapper": result.get("error_wrapper_present", False)}
    # Variants remain separate: replay wrappers and native receipts are not interchangeable.
    texts = []
    for receipt in receipts:
        p = receipt["payload"]
        if isinstance(p, dict):
            if p.get("type") == "direct_response":
                p = p.get("response_item", {}).get("output")
            elif p.get("type") == "error":
                p = p.get("error")
            else:
                p = p.get("content", p.get("output"))
        texts.append(content_text(p))
    result["different_recorded_text_variants"] = len(set(texts)) > 1
    if any(text.startswith("<system>ERROR: Tool execution failed.</system>") for text in texts):
        result["error_wrapper_present"] = True
        result["failure_signals"]["explicit_error_wrapper"] = True
    if isinstance(meta, dict) and meta.get("persistedOutputPath") and result["truncation"]["status"] == "not_explicitly_marked":
        result["truncation"] = {"status": "persisted_output_path_recorded", "path_as_recorded": meta["persistedOutputPath"],
                                "full_output_availability_not_checked": True, "source_ref": first.get("metadata_ref")}
    return result


def normalized_path(path, cwd):
    if not isinstance(path, str):
        return None
    p = Path(path)
    if p.is_absolute():
        if cwd:
            try:
                return str(p.relative_to(cwd))
            except ValueError:
                pass
        return str(p)
    # lexical only; do not dereference historical paths or current symlinks here
    import posixpath
    return posixpath.normpath(path)


def link_script_revisions(steps, cwd):
    state, revisions = {}, []
    for step in steps:
        extraction = step["action"]["static_extraction"]
        failed = any(step["observation"].get("failure_signals", {}).values())
        for file in extraction["file_actions"]:
            if file["kind"] not in ("write", "edit") or not file["path"].endswith((".py", ".sql")):
                continue
            path = normalized_path(file["path"], cwd)
            prior = state.get(path)
            content = file.get("content")
            status = "recorded_definition"
            if file["kind"] == "edit":
                old, new = file.get("old_string"), file.get("new_string")
                content = None
                if prior and isinstance(prior.get("content"), str) and isinstance(old, str) and old and isinstance(new, str):
                    n = prior["content"].count(old)
                    if n == 1 or (n > 0 and file.get("replace_all")):
                        content = prior["content"].replace(old, new, -1 if file.get("replace_all") else 1)
                        status = "reconstructed_from_recorded_edit"
                if content is None:
                    status = "edit_recorded_content_unknown"
            rid = f"V{len(revisions) + 1:04d}"
            revision = {"revision_id": rid, "step_ref": step["step_id"], "path_as_written": file["path"],
                        "path_key": path, "previous_revision_ref": prior["revision_id"] if prior else None,
                        "status": status, "content": content, "content_sha256": sha_text(content) if content is not None else None,
                        "source_ref": file["source_ref"], "write_success_certified": False,
                        "failed_tool_signal": failed, "runtime_file_hash_certified": False}
            if file.get("shell_expansion_possible"):
                revision["shell_expansion_possible"] = True
            revisions.append(revision)
            file["revision_ref"] = rid
            # A known failed attempt cannot replace the last recorded candidate.
            if not failed:
                state[path] = revision
        for run in extraction["script_invocations"]:
            scope = invocation_cwd(step, run, cwd)
            resolved = resolve_directory(run["path"], scope) if scope else run["path"]
            candidate = state.get(normalized_path(resolved, cwd))
            run.update(candidate_revision_ref=candidate["revision_id"] if candidate else None,
                       binding="earlier_recorded_path_match" if candidate else "no_recorded_definition",
                       exact_runtime_content_verified=False)
            if candidate and candidate["content"] is not None and run["path"].endswith(".py"):
                ops, parse_status = python_facts(candidate["content"])
                for op in ops:
                    op.update(revision_ref=candidate["revision_id"], code_context="candidate_script_revision_at_invocation",
                              result_scope="enclosing_tool_result_only", runtime_content_verified=False)
                extraction["data_operations"].extend(ops)
                run["candidate_static_parse"] = parse_status
    return revisions


def document(path, role, sources):
    if not Path(path).is_file():
        sources.add(path, role)
        return None, None
    ref = sources.ref(path, role)
    return sources.resolve(ref), ref


def build(row, verify_capture=True):
    if row.get("eligible_strict_completed") is not True or row.get("strict_recording_complete") is not True:
        raise ValueError("Only strictly complete, normally ended recordings are eligible")
    if row.get("completion", {}).get("state") != "ended_normal":
        raise ValueError("Recording has no normal completion receipt")
    sources = Sources()
    warnings = []
    for source in row["sources"]:
        sources.add(source["path"], source["role"])
    # Hash manifest-declared files again before processing. Missing optional metadata is reported.
    for path, expected in row.get("declared_source_hashes", {}).items():
        entry = sources.by_path.get(str(Path(path).absolute()))
        if entry and entry["exists"] and expected and entry["sha256"] != expected:
            raise ValueError("Source differs from audited hash: " + path)
    check = fresh_capture_check(row["model_io_path"]) if verify_capture else {"errors": [], "test_fixture_only": True}
    if check["errors"]:
        raise ValueError("Fresh capture verification failed: " + ",".join(check["errors"]))
    records = list(sources.records(row["model_io_path"], "model_io"))
    if not records:
        raise ValueError("Empty model exchange ledger")
    adapter = select(records[0][0], sources)
    if row["recording_level"] != adapter.level:
        raise ValueError("Manifest recording level differs from ledger shape")
    for value, ref in records:
        if not adapter.matches(value):
            raise ValueError("Mixed recording shapes within one ledger")
        adapter.ingest(value, ref)
    attempt = Path(row["attempt_directory"])
    transcript_paths = list(dict.fromkeys(s["path"] for s in row["sources"] if s["role"] == "transcript"))
    cwd = None
    cwd_ref = None
    for path in transcript_paths:
        transcript = Path(path)
        if not transcript.is_file() and transcript.name == "events.jsonl" and transcript.with_suffix(".json").is_file():
            transcript = transcript.with_suffix(".json")
            warnings.append({"code": "transcript_manifest_path_relocated", "declared": path, "used": str(transcript)})
        if transcript.is_file():
            tr = list(sources.records(transcript, "transcript"))
            for e, ref in tr:
                if e.get("type") == "system" and e.get("subtype") == "init" and e.get("cwd"):
                    cwd, cwd_ref = e["cwd"], child(ref, "/cwd")
                    break
            adapter.transcript(tr)
    if hasattr(adapter, "native_tools"):
        native = attempt / "tool_execution_io.jsonl"
        if native.is_file():
            adapter.native_tools(sources.records(native, "native_tool_execution_ledger"))
        else:
            warnings.append({"code": "native_tool_sidecar_absent", "result_basis": "recorded_model_request_replay"})
    if adapter.name == "codex-native-serialized-v1":
        native_session = next((s["path"] for s in row["sources"] if s["role"] == "raw_native_session" and Path(s["path"]).is_file()), None)
        if native_session:
            for event, ref in sources.records(native_session, "raw_native_session"):
                if event.get("type") == "session_meta" and isinstance(event.get("payload", {}).get("cwd"), str):
                    cwd = event["payload"]["cwd"]
                    cwd_ref = child(ref, "/payload/cwd")
                    break
    if cwd is None:
        cwd, cwd_ref = recorded_cwd(records)
    # A returned result proves submission even when a historical assistant event is absent.
    observed_ids = adapter.accepted | set(adapter.receipts)
    unknown = observed_ids - set(adapter.calls)
    if unknown:
        raise ValueError("Observed tool call/result without model generation: " + ",".join(sorted(unknown)))
    steps, unobserved = [], []
    for cid, call in adapter.calls.items():
        if cid not in observed_ids:
            unobserved.append(call)
            continue
        obs = observation(adapter.receipts.get(cid, []))
        if obs["availability"] != "recorded":
            raise ValueError("Submitted call has no recorded result: " + cid)
        if call["model_exchange_ref"] is None:
            raise ValueError("Call not found in any recorded model response: " + cid)
        action_call = call
        if any(v["tool_name"] != call["tool_name"] or v["arguments"] != call["arguments"] for v in call.get("submission_variants", [])):
            preferred = min(call["submission_variants"], key=lambda v: 0 if v["kind"] == "framework_submission" else 1)
            action_call = {**call, **{k: preferred[k] for k in ("tool_name", "arguments_raw", "arguments", "arguments_parse_error", "source_ref", "arguments_ref")},
                           "generated_call": {k: call[k] for k in ("tool_name", "arguments_raw", "arguments", "source_ref", "arguments_ref")},
                           "argument_selection_basis": preferred["kind"],
                           "generated_and_submitted_arguments_differ": (preferred["tool_name"], preferred["arguments"]) != (call["tool_name"], call["arguments"])}
            warnings.append({"code": "call_argument_surfaces_differ", "tool_call_id": cid,
                             "selection_basis": preferred["kind"], "both_surfaces_preserved": True})
        steps.append({"step_id": f"S{len(steps) + 1:04d}", "model_exchange_ref": call["model_exchange_ref"],
                      "tool_call_id": cid, "action": {**action_call, "static_extraction": extract_action(action_call)},
                      "observation": obs, "artifact_refs": [], "annotations": {"status": "not_run"}})
    if verify_capture:
        if len(records) != check["model_requests"] or len(steps) != check["tool_calls"]:
            raise ValueError("Adapter counts differ from independent capture verifier")
    result, result_ref = document(attempt / "result.json", "run_result", sources)
    task_doc, task_ref = document(attempt / "task.json", "task_metadata", sources)
    prompt_path = next((s["path"] for s in row["sources"] if s["role"] == "task_prompt" and Path(s["path"]).is_file()), None)
    instruction_ref = {**sources.ref(prompt_path, "task_prompt"), "representation": "text"} if prompt_path else None
    instruction = sources.resolve(instruction_ref) if instruction_ref else None
    task = {"instruction": instruction, "instruction_ref": instruction_ref,
            "metadata_as_recorded": task_doc, "metadata_ref": task_ref}
    revisions = link_script_revisions(steps, cwd)
    data_resources = []
    for name in (task_doc or {}).get("data_sources", []):
        data_resources.append({"path_as_recorded": name, "basis": "task_declared_data_source", "source_ref": task_ref,
                               "access_observed": None})
    input_manifest, input_ref = document(attempt / "input_data_manifest.json", "input_data_manifest", sources)
    if input_manifest:
        data_resources.append({"basis": "recorded_input_manifest", "declarations": input_manifest, "source_ref": input_ref})
    for step in steps:
        for op in step["action"]["static_extraction"]["data_operations"]:
            if op["operation"] in ("data_read_api", "connection_api", "file_open_api"):
                data_resources.append({"path_as_recorded": op.get("path_literal"), "basis": "static_api_argument",
                                       "step_ref": step["step_id"], "api_spelling": op["api_spelling"],
                                       "code_ref": op.get("code_ref"), "revision_ref": op.get("revision_ref"),
                                       "access_observed": None})
                if "connection_parameters_as_written" in op:
                    data_resources[-1].update(connection_driver_as_written=op["connection_driver_as_written"],
                                              connection_parameters_as_written=op["connection_parameters_as_written"],
                                              connection_options_as_recorded=op["options_literal"],
                                              connection_identity_scope=op["connection_identity_scope"])
                    if "expanded_keyword_arguments_as_written" in op:
                        data_resources[-1]["expanded_keyword_arguments_as_written"] = op["expanded_keyword_arguments_as_written"]
    deliverables = collect_artifacts(attempt, task_doc, task_ref, steps, cwd, result, sources)
    final = {"text": None, "source_ref": None, "selection_basis": "no_explicit_final_response_found"}
    if isinstance(result, dict) and isinstance(result.get("final_text"), str):
        final = {"text": result["final_text"], "source_ref": child(result_ref, "/final_text"), "selection_basis": "runner_recorded_final_text"}
    elif (attempt / "final_message.txt").is_file():
        ref = {**sources.ref(attempt / "final_message.txt", "final_response"), "representation": "text"}
        final = {"text": sources.resolve(ref), "source_ref": ref, "selection_basis": "runner_saved_final_message"}
    elif adapter.final_messages:
        message = adapter.final_messages[-1]
        final = {"text": content_text(message["content"]), "source_ref": message["source_ref"],
                 "selection_basis": "last_recorded_assistant_message_candidate", "model_exchange_ref": message["exchange_id"]}
    evaluations = []
    for path in row.get("historical_evaluation", {}).get("paths", []):
        payload, ref = document(path, "official_evaluation", sources)
        if payload is not None:
            evaluations.append({"payload": payload, "source_ref": ref, "available_to_agent": False})
            summary = payload.get("summary") if isinstance(payload, dict) else None
            if isinstance(summary, str):
                candidate = attempt / summary
                if candidate.is_file() and candidate.resolve().is_relative_to(attempt.resolve()):
                    detail, dr = document(candidate, "official_evaluation_detail", sources)
                    evaluations.append({"payload": detail, "source_ref": dr, "available_to_agent": False})
    if not evaluations and isinstance(result, dict) and result.get("evaluation"):
        evaluations.append({"payload": result["evaluation"], "source_ref": child(result_ref, "/evaluation"), "available_to_agent": False})
    missing = [e["path"] for e in sources.entries if not e["exists"]]
    # Optional probes aren't falsely reported as missing raw source files.
    required_paths = {s["path"] for s in row["sources"]}
    for path in missing:
        if path in required_paths:
            warnings.append({"code": "manifest_source_missing", "path": path})
    trace = {"schema_version": SCHEMA_VERSION,
             "trace_metadata": {k: row.get(k) for k in ("record_id", "benchmark", "task_id", "agent_id", "run_id", "attempt_directory", "recording_level")},
             "task": task, "data_resources": data_resources, "steps": steps, "script_revisions": revisions,
             "model_exchanges": adapter.exchanges, "generated_calls_without_observed_submission": unobserved,
             "native_internal_tool_records": adapter.native_internal,
             "deliverables": deliverables, "final_response": final,
             "evaluation": {"records": evaluations, "recomputed": False}, "sources": sources.entries,
             "quality": {"status": "passed_with_warnings" if warnings else "passed", "warnings": warnings,
                         "capture_check": check, "semantic_annotation_run": False,
                         "source_reference_validation": "pending", "original_sources_copied": False}}
    trace["trace_metadata"].update(adapter=adapter.name, working_directory_as_recorded=cwd,
                                   working_directory_ref=cwd_ref,
                                   model_label_as_recorded=records[0][0].get("incoming_anthropic_request", records[0][0].get("model_input", {})).get("model"),
                                   completion_as_audited=row["completion"],
                                   ordering="model response order, then content order; no serial-execution claim within an exchange")
    validate(trace, sources)
    sources.assert_unchanged()
    trace["quality"]["source_reference_validation"] = "passed"
    return trace


def collect_artifacts(attempt, task, task_ref, steps, cwd, result, sources):
    candidates = {}
    def add(path, basis, step=None):
        key = normalized_path(path, cwd)
        if not key:
            return
        item = candidates.setdefault(key, {"path_as_recorded": path, "path_key": key, "evidence": []})
        evidence = {"basis": basis}
        if step:
            evidence["step_ref"] = step["step_id"]
        item["evidence"].append(evidence)
    names = (task or {}).get("output_file_name", [])
    for name in names if isinstance(names, list) else [names]:
        if isinstance(name, str):
            add(name, "task_declared_output")
    for step in steps:
        ex = step["action"]["static_extraction"]
        for file in ex["file_actions"]:
            if file["kind"] in ("write", "edit"):
                add(file["path"], "recorded_file_write_or_edit_attempt", step)
        for op in ex["data_operations"]:
            if op["operation"] == "data_write_api" and op.get("path_literal"):
                add(op["path_literal"], "static_data_write_api_argument", step)
    if (attempt / "answer.txt").is_file() or (attempt / "workspace" / "answer.txt").is_file():
        add("answer.txt", "runner_answer_file_snapshot")
    artifacts = []
    for item in candidates.values():
        aid = f"A{len(artifacts) + 1:04d}"
        item.update(artifact_id=aid, snapshot={"availability": "not_found_in_preserved_workspace", "time_scope": "post_run_current_snapshot"},
                    agent_creation_certified=False)
        key = Path(item["path_key"])
        if not key.is_absolute() and ".." not in key.parts:
            candidate = attempt / "workspace" / key
            if not candidate.is_file() and key == Path("answer.txt"):
                candidate = attempt / "answer.txt"
            if candidate.is_file():
                sid = sources.add(candidate, "artifact_snapshot")
                entry = sources.by_path[str(candidate.absolute())]
                snap = {"availability": "present", "source_id": sid, "sha256": entry["sha256"], "bytes": entry["bytes"],
                        "time_scope": "post_run_current_snapshot", "not_an_execution_time_measurement": True}
                if candidate.suffix.lower() == ".csv":
                    try:
                        with candidate.open(encoding="utf-8-sig", newline="") as f:
                            rows = csv.reader(f)
                            snap["columns_as_stored"] = next(rows, [])
                            snap["data_rows_as_stored"] = sum(1 for _ in rows)
                            snap["csv_dialect_assumption"] = "comma-delimited; no data-type or metric inference"
                    except (UnicodeError, csv.Error) as exc:
                        snap["inspection_error"] = type(exc).__name__
                if candidate.suffix.lower() in (".txt", ".py", ".sql") and entry["bytes"] <= 1024 * 1024:
                    ref = {"source_id": sid, "json_pointer": "", "representation": "text"}
                    snap["content"] = sources.resolve(ref)
                    snap["content_ref"] = ref
                item["snapshot"] = snap
        if item["path_key"] == "answer.txt" and isinstance(result, dict) and "agent_created_answer_file" in result:
            item["runner_reported_agent_created_answer_file"] = result["agent_created_answer_file"]
        for evidence in item["evidence"]:
            if "step_ref" in evidence:
                steps[int(evidence["step_ref"][1:])-1]["artifact_refs"].append(aid)
        artifacts.append(item)
    return artifacts


def validate(trace, sources):
    exchange_ids = {x["exchange_id"] for x in trace["model_exchanges"]}
    artifact_ids = {x["artifact_id"] for x in trace["deliverables"]}
    revision_ids = {x["revision_id"] for x in trace["script_revisions"]}
    step_ids = {x["step_id"] for x in trace["steps"]}
    call_ids = set()
    for step in trace["steps"]:
        cid = step["tool_call_id"]
        if cid in call_ids or step["model_exchange_ref"] not in exchange_ids:
            raise ValueError("Duplicate call or dangling exchange reference")
        call_ids.add(cid)
        action = step["action"]
        if sources.resolve(action["arguments_ref"]) != action["arguments_raw"]:
            raise ValueError("Arguments differ from source")
        for variant in action.get("serialization_variants", []) + action.get("submission_variants", []):
            if sources.resolve(variant["arguments_ref"]) != variant["arguments_raw"]:
                raise ValueError("Argument variant differs from source")
        if action.get("generated_call") and sources.resolve(action["generated_call"]["arguments_ref"]) != action["generated_call"]["arguments_raw"]:
            raise ValueError("Generated arguments differ from source")
        if set(step["artifact_refs"]) - artifact_ids:
            raise ValueError("Dangling artifact reference")
        for invocation in action["static_extraction"]["script_invocations"]:
            if invocation["candidate_revision_ref"] is not None and invocation["candidate_revision_ref"] not in revision_ids:
                raise ValueError("Dangling revision reference")
        for receipt in step["observation"]["variants"]:
            if sources.resolve(receipt["source_ref"]) != receipt["payload"]:
                raise ValueError("Tool output differs from source")
        for unit in action["static_extraction"]["code_units"]:
            # shlex-decoded -c payload differs from the raw quoted shell span by design.
            if unit.get("extraction") != "shell_quoted_argument_decoding" and unit["code"] != sources.resolve(unit["source_ref"]):
                raise ValueError("Extracted code differs from source span")
    if call_ids & {c["tool_call_id"] for c in trace["generated_calls_without_observed_submission"]}:
        raise ValueError("Unobserved generation incorrectly promoted to a step")
    for revision in trace["script_revisions"]:
        if revision["step_ref"] not in step_ids:
            raise ValueError("Dangling revision step reference")
    def walk(value):
        if isinstance(value, dict):
            if "source_id" in value and ("json_pointer" in value or "line" in value):
                sources.resolve(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(trace)
