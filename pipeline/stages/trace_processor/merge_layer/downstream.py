"""Lossless event adapter for existing Evidence Builder / diagnosis readers.

Runs the existing Evidence Builder, not a diagnostic model or rule classifier.
Full histories stay retrievable through portable normalized-trace references.
"""
import copy
import sys
from pathlib import Path
from collections import Counter

from .build import read, save


def event_input(trace, trace_path):
    facts = trace["facts"]
    rid = facts["trace_metadata"]["record_id"]
    events = []

    def add(key, event_type, actor, fact_pointer, **fields):
        event = {"trace_id": rid, "event_id": rid + ":" + key, "sequence": len(events),
                 "event_type": event_type, "actor": actor, "status": "recorded",
                 "normalized_trace_ref": {"path": trace_path, "json_pointer": fact_pointer},
                 "raw_source": {"path": trace_path, "json_pointer": fact_pointer}, **fields}
        events.append(event)
        return event

    add("task", "message", "user", "/facts/task", content=facts["task"]["instruction"],
        data_agent_extension={"task_requirements": trace["annotations"]["task"],
                              "interpretation_is_llm_generated": True})
    step_event_ids = {}
    for xi, exchange in enumerate(facts["model_exchanges"]):
        xid = exchange["exchange_id"]
        # This event is a model request/response pair. It is not a tool execution.
        current = [e["text"] for e in trace["evidence_catalog"]
                   if e.get("role") == "assistant_claimed_intent" and any(
                       s["step_id"] == e.get("step_id") and s["model_exchange_ref"] == xid for s in facts["steps"])]
        event = add(xid, "model_exchange", "assistant", f"/facts/model_exchanges/{xi}",
            content="\n".join(dict.fromkeys(current)), call_ids=exchange["tool_call_ids"],
            step_group_id=xid, full_request_ref=exchange["request_ref"], full_response_ref=exchange["response_ref"],
            content_scope="Current assistant text for navigation; full recorded request/response accessible through source refs")
        for si, step in enumerate(facts["steps"]):
            if step["model_exchange_ref"] != xid:
                continue
            obs = step["observation"]
            units = [u for u in trace["annotations"]["units"] if u["step_id"] == step["step_id"]]
            selected = {u["unit_id"] for u in units}
            statuses = [c for c in trace["citation_index"] if c.get("unit_id") in selected]
            payload = add(step["step_id"], "tool_step", "tool", f"/facts/steps/{si}",
                status="failure" if any(obs.get("failure_signals", {}).values()) else "recorded",
                tool_name=step["action"]["tool_name"], tool_input=step["action"]["arguments_raw"],
                content=obs.get("text"), exit_code=obs["exit_code_as_recorded"],
                call_id=step["tool_call_id"], call_ids=[step["tool_call_id"]],
                step_group_id=xid, parent_event_id=event["event_id"], step_id=step["step_id"],
                data_agent_extension={"semantic_units": units,
                    "literal_citations": [c for c in statuses if c["eligible_as_literal_evidence"]],
                    "unresolved_citations": [c for c in statuses if not c["eligible_as_literal_evidence"]],
                    "static_extraction": step["action"]["static_extraction"],
                    "artifact_refs": step["artifact_refs"], "recording_signals": {k: obs.get(k) for k in (
                        "failure_signals", "availability", "truncation", "tool_status_as_recorded", "is_error_as_recorded")},
                    "semantics_are_claims_not_verified_facts": True})
            step_event_ids[step["step_id"]] = payload["event_id"]
    add("final", "message", "assistant", "/facts/final_response", content=facts["final_response"]["text"],
        data_agent_extension={"deliverables": facts["deliverables"],
                              "snapshot_time_scope": "post_run_current_snapshot", "creation_by_agent_not_certified": True})
    for i, record in enumerate(facts["evaluation"]["records"]):
        add(f"evaluation{i+1}", "evaluation", "evaluator", f"/facts/evaluation/records/{i}",
            content=copy.deepcopy(record["payload"]), available_to_agent=False)
    first_payload = facts["evaluation"]["records"][0]["payload"] if facts["evaluation"]["records"] else {}
    raw_score = first_payload.get("score") if isinstance(first_payload, dict) else None
    score = raw_score if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool) else None
    # Explicit compatibility policy for this cohort's three benchmarks (maximum 1).
    # Keep the original evaluation untouched and never use it as an operation label.
    outcome = "success" if score == 1 else "failure" if score is not None and 0 <= score < 1 else "unknown"
    failures = [e for e in events if e["status"] == "failure"]
    evaluation_events = [e for e in events if e["event_type"] == "evaluation"]
    sample = {"schema": "trace-diagnosis-input-v2", "trace_id": rid, "diagnosis_id": "diagnosis:" + rid,
        "diagnosis_status": "pending" if failures or outcome == "failure" else "no_failure_observed" if outcome == "success" else "evaluation_unknown",
        "analysis_unit": "trace", "event_failure_observed": bool(failures),
        "task": copy.deepcopy(facts["task"]), "run_metadata": copy.deepcopy(facts["trace_metadata"]),
        "evaluation": {"score": score, "outcome": outcome, "status": first_payload.get("status"),
            "standard": first_payload.get("method"), "maximum_score_policy": 1,
            "original_evaluation_ref": {"path": trace_path, "json_pointer": "/facts/evaluation"}},
        "trace_summary": {"event_count": len(events), "tool_event_count": len(facts["steps"]),
            "model_exchange_count": len(facts["model_exchanges"]), "failure_event_count": len(failures),
            "event_type_counts": dict(Counter(e["event_type"] for e in events))},
        "first_failure": failures[0] if failures else None, "failure_candidates": failures,
        "evaluation_events": evaluation_events, "context_events": copy.deepcopy(events),
        "trace_timeline_ref": {"path": "downstream/timeline.jsonl", "trace_id": rid, "event_count": len(events)},
        "diagnosis_contract": {"diagnosis_unit": "one_complete_trace", "root_cause_assigned": False,
            "failure_candidate_is_not_root_cause": True, "requirement_link_is_not_satisfaction": True},
        "data_agent_extension": {"schema": "data-agent-diagnosis-extension-v1", "normalized_trace_path": trace_path,
            "quality": trace["quality"], "sources": trace["provenance"]["source_files"],
            "all_steps_and_model_exchanges_retrievable": True,
            "literal_match_is_not_semantic_entailment": True,
            "order_scope": "Recorded response/block order; same exchange does not imply serial execution"}}
    return sample, events


def export(root):
    root = Path(root)
    scripts = Path(__file__).resolve().parents[4] / "scripts"
    sys.path.insert(0, str(scripts))
    from build_trace_level_evidence import build_evidence_sample, materialize
    from run_trace_diagnostician_v3_baseline import detailed_event_map, evaluation_citation
    from ..fact_layer.sources import pointer
    reports, samples, evidence_outputs, timeline_rows, edge_rows = [], [], [], [], []
    for row in read(root / "validation.json"):
        trace = read(root / row["trace_path"])
        sample, timeline = event_input(trace, row["trace_path"])
        evidence, edges = build_evidence_sample(sample, timeline, Path("downstream/edges.jsonl"))
        evidence["data_agent_extension"] = sample["data_agent_extension"]
        evidence["task"] = sample["task"]
        # Actual existing diagnosis readers, without invoking its historical rules.
        event_map = detailed_event_map(evidence, timeline)
        if set(event_map) != {e["event_id"] for e in timeline}:
            raise ValueError("Existing diagnosis reader lost timeline events")
        by_id = {e["event_id"]: e for e in evidence["evidence_events"]}
        if set(by_id) != set(event_map):
            raise ValueError("Existing Evidence Builder lost complete context events")
        for event in timeline:
            if pointer(trace, event["normalized_trace_ref"]["json_pointer"]) is None and event["event_type"] != "message":
                raise ValueError("Unresolvable event fact pointer")
            for key, value in event.items():
                if by_id[event["event_id"]].get(key) != value:
                    raise ValueError("Evidence Builder changed event payload: " + key)
            if event.get("parent_event_id") and event["parent_event_id"] not in event_map:
                raise ValueError("Dangling parent event")
        if any(e["source_event_id"] not in event_map for e in edges):
            raise ValueError("Dangling edge source")
        if any(e["target_id"] not in event_map for e in edges if e["relation"] == "parent_event"):
            raise ValueError("Dangling edge parent target")
        final_event = next(e for e in timeline if e["event_id"].endswith(":final"))
        if final_event["content"] != trace["facts"]["final_response"]["text"]:
            raise ValueError("Final answer was lost")
        # Check every operation/detail/link remains reachable after the old builder.
        units = [u for e in evidence["evidence_events"] for u in e.get("data_agent_extension", {}).get("semantic_units", [])]
        if units != [u for step in trace["facts"]["steps"] for u in trace["annotations"]["units"] if u["step_id"] == step["step_id"]]:
            raise ValueError("Downstream operation annotations changed or disappeared")
        # Query-time retrieval smoke test of exact SQL/code, raw result, requirements,
        # artifacts, and evaluation; no keyword-derived root-cause classification.
        report = {"record_id": row["record_id"], "existing_evidence_builder": "passed",
            "existing_diagnosis_event_reader": "passed", "event_count": len(timeline),
            "tool_steps": sample["trace_summary"]["tool_event_count"], "edges": len(edges),
            "all_event_payloads_unchanged": True, "all_native_units_reachable": len(units),
            "model_io_reachable_by_source_refs": len(trace["facts"]["model_exchanges"]),
            "evaluation_reader": evaluation_citation(evidence),
            "unresolved_citations_quarantined": sum(len(e.get("data_agent_extension", {}).get("unresolved_citations", [])) for e in timeline),
            "diagnostic_root_cause_inference_run": False, "diagnostic_accuracy_measured": False}
        samples.append(sample); evidence_outputs.append(evidence); timeline_rows.extend(timeline); edge_rows.extend(edges); reports.append(report)
    for name, values in (("diagnosis_inputs.jsonl", samples), ("evidence_samples.jsonl", evidence_outputs),
                         ("timeline.jsonl", timeline_rows), ("edges.jsonl", edge_rows)):
        path = root / "downstream" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
    save(root / "downstream" / "validation.json", reports)
    materialized = materialize(root / "downstream", root / "downstream/evidence_samples.jsonl", root / "downstream/edges.jsonl")
    save(root / "downstream/materialization.json", materialized)
    # Check the old query store too: inference of nested JSON types must not
    # alter code, tool arguments, or observed output text.
    import duckdb
    import json
    connection = duckdb.connect(str(root / "downstream/trace_evidence.duckdb"), read_only=True)
    try:
        original = {e["trace_id"]: {v["event_id"]: v for v in e["evidence_events"]} for e in evidence_outputs}
        checked_events = 0
        for rid, encoded in connection.execute("SELECT trace_id, to_json(evidence_events) FROM trace_evidence").fetchall():
            for event in json.loads(encoded):
                for field in ("content", "tool_input", "exit_code"):
                    if field in original[rid][event["event_id"]] and event[field] != original[rid][event["event_id"]][field]:
                        raise ValueError("Query materialization changed event payload")
                checked_events += 1
        if checked_events != len(timeline_rows):
            raise ValueError("Query materialization lost events")
        save(root / "downstream/query_validation.json", {"status": "passed", "events_checked": checked_events,
             "trace_count": materialized["trace_count"], "edge_count": materialized["edge_count"],
             "fields_compared": ["content", "tool_input", "exit_code"], "root_cause_inference_run": False})
    finally:
        connection.close()
    return reports
