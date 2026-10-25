#!/usr/bin/env python3
"""Run the trace-level, explainable Trace Diagnostician v3 baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RULE_VERSION = "trace-diagnostician-trace-baseline-v3"
RESULT_SCHEMA = "trace-diagnosis-result-v3"

ALLOWED_ROOT_CAUSES = [
    "agent_action_error",
    "agent_reasoning_error",
    "agent_state_error",
    "data_or_task_issue",
    "environment_or_system_error",
    "evaluator_or_annotation_issue",
    "trace_observability_issue",
    "no_failure",
    "uncertain",
]

SIGNAL_RULES: list[tuple[str, str, float, str]] = [
    (
        "environment_or_system_error",
        r"permission denied|operation not permitted|connection refused|connection reset|network is unreachable|dns|service unavailable|rate limit|\b429\b|\b502\b|\b503\b|\b504\b|no space left on device|out of memory|\boom\b|killed|exit code 137|timed? out|timeout",
        0.90,
        "The trace contains a runtime, service, resource, or connectivity failure signal.",
    ),
    (
        "agent_action_error",
        r"syntaxerror|nameerror|typeerror|keyerror|indexerror|attributeerror|modulenotfounderror|importerror|filenotfounderror|file not found|no such file|no such column|no such table|command not found|unknown option|invalid syntax|json\.decoder|decodeerror|assertionerror|parse error|ambiguous column|path does not exist|cannot open|invalid argument|invalid command",
        0.86,
        "The trace contains a malformed command, query, program, or tool action signal.",
    ),
    (
        "data_or_task_issue",
        r"empty dataset|dataset is empty|no input data|missing input data|task is not executable|task cannot be completed|unsupported dataset|invalid task definition|input file is empty|no rows returned",
        0.82,
        "The trace contains a task or input-data availability/validity signal.",
    ),
]

FINAL_FAILURE_PATTERNS = re.compile(
    r"cannot complete|could not complete|unable to|i cannot|i couldn't|failed to|no answer|no final answer|not enough information|error occurred|gave up|did not produce|missing required|not found",
    re.IGNORECASE,
)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def clip(value: Any, limit: int = 900) -> str:
    result = as_text(value).replace("\x00", " ").strip()
    return result if len(result) <= limit else result[:limit] + "…"


def event_text(event: dict[str, Any]) -> str:
    return " ".join(
        as_text(event.get(key))
        for key in ("error", "stderr", "content", "tool_input", "stdout")
    ).lower()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_timelines(path: Path) -> dict[str, list[dict[str, Any]]]:
    timelines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                event = json.loads(line)
                timelines[event["trace_id"]].append(event)
    for events in timelines.values():
        events.sort(key=lambda event: (event.get("sequence", 0), event.get("event_id", "")))
    return dict(timelines)


def detailed_event_map(evidence: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    events = {event["event_id"]: dict(event) for event in timeline if event.get("event_id")}
    for event in evidence.get("evidence_events", []):
        event_id = event.get("event_id")
        if not event_id:
            continue
        events[event_id] = {**events.get(event_id, {}), **event}
    return events


def event_ids_with_reason(evidence: dict[str, Any], reason: str) -> list[str]:
    result: list[str] = []
    for event in evidence.get("evidence_events", []):
        if reason in (event.get("evidence_reasons") or []) and event.get("event_id"):
            result.append(event["event_id"])
    return result


def unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def call_ids(event: dict[str, Any]) -> list[str]:
    values = list(event.get("call_ids") or [])
    if event.get("call_id") and event["call_id"] not in values:
        values.insert(0, event["call_id"])
    return unique([str(value) for value in values])


def relation_context(
    first_id: str | None,
    event_map: dict[str, dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return connected evidence IDs and a compact causal chain for one trace."""
    if not first_id or first_id not in event_map:
        return [], []
    by_call: dict[str, list[str]] = defaultdict(list)
    by_group: dict[str, list[str]] = defaultdict(list)
    for event in timeline:
        event_id = event.get("event_id")
        if not event_id:
            continue
        for call_id in call_ids(event):
            by_call[call_id].append(event_id)
        if event.get("step_group_id"):
            by_group[str(event["step_group_id"])].append(event_id)

    first = event_map[first_id]
    related: list[str] = [first_id]
    chain: list[dict[str, Any]] = [
        {"event_id": first_id, "role": "first_actionable_failure", "relation": "first_failure_candidate"}
    ]

    parent_id = first.get("parent_event_id")
    if parent_id in event_map:
        related.append(parent_id)
        chain.insert(0, {"event_id": parent_id, "role": "parent_context", "relation": "parent_of_first_failure"})

    for call_id in call_ids(first):
        for event_id in by_call.get(call_id, []):
            if event_id == first_id:
                continue
            related.append(event_id)
            if len([item for item in chain if item["relation"] == "same_tool_call"]) < 4:
                chain.append({"event_id": event_id, "role": "tool_call_context", "relation": "same_tool_call", "call_id": call_id})

    group_id = first.get("step_group_id")
    if group_id:
        for event_id in by_group.get(str(group_id), []):
            if event_id == first_id:
                continue
            related.append(event_id)
            if len([item for item in chain if item["relation"] == "same_step_group"]) < 4:
                chain.append({"event_id": event_id, "role": "step_context", "relation": "same_step_group", "step_group_id": str(group_id)})

    return unique(related), chain


def final_output_event(evidence: dict[str, Any], timeline: list[dict[str, Any]], event_map: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    preferred_ids = event_ids_with_reason(evidence, "final_output_before_evaluation_failure")
    if preferred_ids:
        return event_map.get(preferred_ids[0])
    candidates = [
        event
        for event in event_map.values()
        if event.get("event_type") == "message" or event.get("actor") == "assistant"
    ]
    if not candidates:
        candidates = timeline
    return max(candidates, key=lambda event: (event.get("sequence", 0), event.get("event_id", ""))) if candidates else None


def explicit_failure_ids(evidence: dict[str, Any], timeline: list[dict[str, Any]], event_map: dict[str, dict[str, Any]]) -> list[str]:
    ids = list(evidence.get("candidate_event_ids") or [])
    for event in timeline:
        event_id = event.get("event_id")
        if not event_id:
            continue
        status = str(event.get("status") or "").lower()
        exit_code = event.get("exit_code")
        if status in {"failure", "failed", "error", "timeout", "timed_out"} or (isinstance(exit_code, int) and exit_code != 0):
            ids.append(event_id)
    for event_id, event in event_map.items():
        if event.get("error") not in (None, "", [], {}) or event.get("stderr") not in (None, "", [], {}):
            ids.append(event_id)
    return unique(ids)


def signal_matches(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined = "\n".join(event_text(event) for event in events)
    matches: list[dict[str, Any]] = []
    for category, pattern, confidence, explanation in SIGNAL_RULES:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            matches.append({
                "category": category,
                "confidence": confidence,
                "matched_text": match.group(0).lower(),
                "explanation": explanation,
            })
    priority = {
        "environment_or_system_error": 0,
        "data_or_task_issue": 1,
        "agent_action_error": 2,
    }
    return sorted(matches, key=lambda item: (priority[item["category"]], -item["confidence"]))


def evaluation_citation(evidence: dict[str, Any]) -> dict[str, Any]:
    evaluation = evidence.get("evaluation") or {}
    artifact = evidence.get("evaluation_artifact") or {}
    artifact_note = (
        f", evaluator_artifact={artifact.get('format')}, "
        f"artifact_failure_observed={artifact.get('failure_observed')}"
        if artifact
        else ""
    )
    return {
        "event_id": None,
        "sequence": None,
        "role": "task_evaluation",
        "field": "evaluation",
        "observation": (
            f"outcome={evaluation.get('outcome')}, score={evaluation.get('score')}, "
            f"standard={evaluation.get('standard')}, status={evaluation.get('status')}"
            f"{artifact_note}"
        ),
        "raw_source": artifact.get("path") or evaluation.get("artifact"),
    }


def evaluator_failure_citations(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    artifact = evidence.get("evaluation_artifact") or {}
    citations: list[dict[str, Any]] = []
    raw_source = artifact.get("path") or artifact.get("declared_path")
    for reason in (artifact.get("failure_reasons") or [])[:6]:
        citations.append({
            "event_id": None,
            "sequence": None,
            "role": "evaluator_failure_field",
            "field": "evaluation_artifact.failure_reasons",
            "observation": clip(reason),
            "raw_source": raw_source,
        })
    failed_fields = artifact.get("failed_fields") or []
    if failed_fields:
        citations.append({
            "event_id": None,
            "sequence": None,
            "role": "evaluator_failure_field",
            "field": "evaluation_artifact.failed_fields",
            "observation": "Evaluator reported mismatches in fields: " + ", ".join(map(str, failed_fields)),
            "raw_source": raw_source,
        })
    for check in (artifact.get("checks") or [])[:4]:
        if check.get("meaning"):
            citations.append({
                "event_id": None,
                "sequence": None,
                "role": "evaluator_comparison",
                "field": "evaluation_artifact.checks.meaning",
                "observation": clip(check["meaning"]),
                "raw_source": raw_source,
            })
    for turn in (artifact.get("turn_evaluations") or [])[:4]:
        if turn.get("reason"):
            citations.append({
                "event_id": None,
                "sequence": turn.get("turn_id"),
                "role": "evaluator_turn_reason",
                "field": "evaluation_artifact.turn_evaluations.reason",
                "observation": clip(turn["reason"]),
                "raw_source": turn.get("path") or raw_source,
            })
    return citations


def evaluator_failure_diagnosis(evidence: dict[str, Any]) -> dict[str, Any] | None:
    artifact = evidence.get("evaluation_artifact") or {}
    if not artifact.get("available"):
        return None
    hint = artifact.get("diagnostic_hint")
    if hint == "dataspace_reviewed_failure":
        failure_type = str((artifact.get("human_labels") or {}).get("failure_type") or "")
        root_cause_by_type = {
            "external_environment_noise_appledouble": ("environment_or_system_error", 0.92),
            "external_dependency_unavailable": ("environment_or_system_error", 0.90),
            "query_or_schema_error": ("agent_action_error", 0.90),
            "command_construction_error": ("agent_action_error", 0.90),
            "script_syntax_error": ("agent_action_error", 0.92),
            "script_or_data_parsing_error": ("agent_action_error", 0.88),
            "output_format_error": ("agent_action_error", 0.88),
            "visual_tool_invocation_error": ("agent_action_error", 0.86),
            "visual_artifact_missing": ("agent_action_error", 0.84),
            "media_processing_error": ("agent_action_error", 0.84),
            "agent_workspace_boundary_error": ("agent_action_error", 0.84),
            "missing_prediction": ("agent_action_error", 0.88),
            "data_artifact_missing": ("data_or_task_issue", 0.82),
            "evidence_to_answer_mismatch": ("agent_reasoning_error", 0.86),
            "incomplete_no_submission": ("agent_reasoning_error", 0.84),
        }
        if failure_type == "no_observed_failure":
            result = base_diagnosis(
                evidence,
                "not_applicable",
                "no_failure",
                0.91,
                "DATASPACE_REVIEW_NO_OBSERVED_FAILURE",
                None,
            )
            result["evaluation_alignment"] = "reviewed_no_observed_failure"
            result["evidence"].extend(evaluator_failure_citations(evidence))
            return result
        if failure_type in root_cause_by_type:
            root_cause, confidence = root_cause_by_type[failure_type]
            result = base_diagnosis(
                evidence,
                "diagnosed",
                root_cause,
                confidence,
                "DATASPACE_REVIEWED_FAILURE_TYPE",
                None,
            )
            result["evaluation_alignment"] = "dataspace_review_fields_explain_failure"
            result["evidence"].extend(evaluator_failure_citations(evidence))
            return result
        if failure_type == "uncertain_no_actionable_error":
            result = base_diagnosis(
                evidence,
                "uncertain",
                "uncertain",
                0.40,
                "DATASPACE_REVIEW_UNCERTAIN_FAILURE",
                "DataSpace review marks this trace as uncertain and does not provide an actionable failure type.",
            )
            result["evaluation_alignment"] = "dataspace_review_uncertain"
            result["evidence"].extend(evaluator_failure_citations(evidence))
            return result
    if hint == "agent_output_mismatch" and artifact.get("failure_observed"):
        result = base_diagnosis(
            evidence,
            "diagnosed",
            "agent_reasoning_error",
            0.84,
            "EVALUATOR_FAILURE_OUTPUT_MISMATCH",
            None,
        )
        result["evaluation_alignment"] = "evaluator_fields_explain_failure"
        result["evidence"].extend(evaluator_failure_citations(evidence))
        return result
    return None


def event_citation(event: dict[str, Any], role: str, observation: str | None = None) -> dict[str, Any]:
    fields = []
    for field in ("event_type", "actor", "stage", "status", "tool_name", "tool_input", "stderr", "error", "exit_code", "content", "stdout"):
        if event.get(field) not in (None, "", [], {}):
            fields.append(field)
    return {
        "event_id": event.get("event_id"),
        "sequence": event.get("sequence"),
        "role": role,
        "fields": fields,
        "observation": observation or clip(event_text(event)),
        "raw_source": event.get("raw_source"),
    }


def base_diagnosis(
    evidence: dict[str, Any],
    status: str,
    root_cause: str,
    confidence: float,
    rule_id: str,
    uncertainty: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "root_cause": root_cause,
        "confidence": confidence,
        "first_failure_event_id": None,
        "supporting_event_ids": [],
        "contributing_event_ids": [],
        "causal_chain": [],
        "evidence": [evaluation_citation(evidence)],
        "counterevidence": [],
        "uncertainty": uncertainty,
        "rule_id": rule_id,
        "rule_matches": [],
        "evaluation_alignment": "evaluation_unknown",
    }


def diagnose(evidence: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, Any]:
    event_map = detailed_event_map(evidence, timeline)
    evidence_event_ids = set(evidence.get("evidence_event_ids") or [])
    evaluation = evidence.get("evaluation") or {}
    outcome = evaluation.get("outcome", "unknown")
    event_failure = bool(evidence.get("event_failure_observed"))

    if not event_failure and outcome == "success":
        result = base_diagnosis(
            evidence,
            "not_applicable",
            "no_failure",
            0.98,
            "TRACE_SUCCESS_NO_EVENT_FAILURE",
            None,
        )
        result["evaluation_alignment"] = "successful_trace"
        result["evidence"].append({
            "event_id": None,
            "sequence": None,
            "role": "trace_summary",
            "field": "event_failure_observed",
            "observation": "The complete trace has no explicit event failure and the task evaluation succeeded.",
            "raw_source": None,
        })
        return result

    failure_ids = [
        event_id
        for event_id in explicit_failure_ids(evidence, timeline, event_map)
        if event_id in evidence_event_ids
    ]
    first_ids = [
        event_id
        for event_id in event_ids_with_reason(evidence, "first_failure_candidate")
        if event_id in evidence_event_ids
    ]
    first_id = first_ids[0] if first_ids else (failure_ids[0] if failure_ids else None)

    if not event_failure and outcome in {"failure", "unknown"}:
        evaluator_result = evaluator_failure_diagnosis(evidence)
        if evaluator_result:
            final = final_output_event(evidence, timeline, event_map)
            evidence_event_ids = set(evidence.get("evidence_event_ids") or [])
            if final and final.get("event_id") in evidence_event_ids:
                final_id = final["event_id"]
                evaluator_result["first_failure_event_id"] = final_id
                evaluator_result["supporting_event_ids"] = [final_id]
                evaluator_result["contributing_event_ids"] = [final_id]
                evaluator_result["causal_chain"] = [{
                    "event_id": final_id,
                    "role": "final_output_before_evaluation_failure",
                    "relation": "evaluator_reported_output_mismatch",
                }]
                evaluator_result["evidence"].append(event_citation(
                    final,
                    "final_output_before_evaluation_failure",
                    clip(final.get("content") or final.get("stdout") or final.get("error")),
                ))
            return evaluator_result
        if outcome == "unknown":
            result = base_diagnosis(
                evidence,
                "uncertain",
                "uncertain",
                0.30,
                "EVALUATION_UNKNOWN_NO_EVALUATOR_ARTIFACT",
                "The task evaluation outcome is unknown and no evaluator artifact is available for this trace.",
            )
            result["evaluation_alignment"] = "evaluation_unknown"
            return result
        final = final_output_event(evidence, timeline, event_map)
        if final and final.get("event_id"):
            if final["event_id"] in evidence_event_ids:
                failure_ids = unique([final["event_id"], *failure_ids])
        final_text = event_text(final or {})
        if final is None:
            result = base_diagnosis(
                evidence,
                "uncertain",
                "trace_observability_issue",
                0.34,
                "EVALUATION_FAILURE_NO_FINAL_OUTPUT",
                "Evaluation failed, but no final message or assistant output is available in the structured trace.",
            )
            result["evaluation_alignment"] = "evaluation_failure_not_explained"
            return result

        result = base_diagnosis(
            evidence,
            "uncertain",
            "agent_reasoning_error",
            0.55,
            "EVALUATION_FAILURE_TRACE_LEVEL_OUTPUT",
            "The evaluator reports failure without an explicit tool/runtime error; the final output is the strongest available agent-level evidence, but the exact rubric violation is not represented structurally.",
        )
        result["evaluation_alignment"] = "evaluation_failure_without_event_failure"
        result["first_failure_event_id"] = final.get("event_id")
        result["supporting_event_ids"] = [final["event_id"]]
        result["contributing_event_ids"] = [final["event_id"]]
        result["causal_chain"] = [{
            "event_id": final["event_id"],
            "role": "final_output_before_evaluation_failure",
            "relation": "last_agent_message_before_failed_evaluation",
        }]
        result["evidence"].append(event_citation(final, "final_output_before_evaluation_failure", clip(final.get("content") or final.get("stdout") or final_text)))
        if FINAL_FAILURE_PATTERNS.search(final_text):
            result["confidence"] = 0.66
            result["rule_id"] = "EVALUATION_FAILURE_FINAL_OUTPUT_SIGNAL"
            result["rule_matches"] = ["final_output_failure_signal"]
            result["uncertainty"] = "The final output contains an explicit inability/failure signal, but the evaluator-specific failure criterion is unavailable."
        return result

    first = event_map.get(first_id) if first_id else None
    connected_ids, causal_chain = relation_context(first_id, event_map, timeline)
    selected_ids = unique([event_id for event_id in [*failure_ids, *connected_ids] if event_id in evidence_event_ids])
    selected_events = [event_map[event_id] for event_id in selected_ids if event_id in event_map]
    matches = signal_matches(selected_events)
    if first and first.get("event_id") not in selected_ids:
        selected_ids.insert(0, first["event_id"])
    if not matches and first and first.get("stage") == "submit":
        matches = [{
            "category": "agent_reasoning_error",
            "confidence": 0.56,
            "matched_text": "submit-stage failure without technical signature",
            "explanation": "The failure occurs at submission without a more specific runtime signal.",
        }]

    if matches:
        match = matches[0]
        strong = match["confidence"] >= 0.80
        status = "diagnosed" if strong and outcome != "success" else "uncertain"
        root_cause = match["category"]
        uncertainty = None if status == "diagnosed" else (
            "An explicit event signal exists, but the task evaluation succeeded or the signal is not sufficient to establish that it caused the final outcome."
        )
        result = base_diagnosis(evidence, status, root_cause, match["confidence"], f"SIGNAL_{root_cause.upper()}", uncertainty)
        result["evaluation_alignment"] = (
            "explains_evaluation_failure" if outcome == "failure" else
            "contradicts_successful_evaluation" if outcome == "success" else
            "evaluation_unknown"
        )
        result["first_failure_event_id"] = first_id
        result["supporting_event_ids"] = selected_ids[:30]
        result["contributing_event_ids"] = selected_ids[:30]
        result["causal_chain"] = causal_chain
        result["rule_matches"] = [item["category"] for item in matches]
        if first:
            result["evidence"].append(event_citation(
                first,
                "first_actionable_failure",
                f"First candidate event: event_type={first.get('event_type')}, stage={first.get('stage')}, status={first.get('status')}, exit_code={first.get('exit_code')}",
            ))
        for event in selected_events:
            event_signal = event_text(event)
            if event_signal and any(item["matched_text"] in event_signal for item in matches):
                result["evidence"].append(event_citation(event, "matched_error_signal", clip(event_signal)))
                if len(result["evidence"]) >= 8:
                    break
        if len(matches) > 1:
            result["counterevidence"].append({
                "field": "competing_rule_matches",
                "observation": ", ".join(item["category"] for item in matches[1:]),
            })
        return result

    result = base_diagnosis(
        evidence,
        "uncertain",
        "uncertain",
        0.35,
        "INSUFFICIENT_TRACE_LEVEL_EVIDENCE",
        "A failure event exists, but the complete related event context has no reliable root-cause signal.",
    )
    result["evaluation_alignment"] = "explains_evaluation_failure" if outcome == "failure" else "evaluation_unknown"
    result["first_failure_event_id"] = first_id
    result["supporting_event_ids"] = selected_ids[:30]
    result["contributing_event_ids"] = selected_ids[:30]
    result["causal_chain"] = causal_chain
    if first:
        result["evidence"].append(event_citation(first, "first_actionable_failure"))
    return result


def build_result(evidence: dict[str, Any], diagnosis: dict[str, Any], input_path: Path, timeline_path: Path) -> dict[str, Any]:
    trace_id = evidence["trace_id"]
    diagnosis_id = "diagnosis:v3:" + hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:24]
    evidence_event_ids = set(evidence.get("evidence_event_ids") or [])
    references = unique([
        diagnosis.get("first_failure_event_id"),
        *diagnosis.get("supporting_event_ids", []),
        *diagnosis.get("contributing_event_ids", []),
        *(item.get("event_id") for item in diagnosis.get("causal_chain", [])),
    ])
    invalid_references = [event_id for event_id in references if event_id not in evidence_event_ids]
    return {
        "schema": RESULT_SCHEMA,
        "diagnosis_id": diagnosis_id,
        "trace_id": trace_id,
        "input_ref": {
            "evidence_id": evidence.get("evidence_id"),
            "evidence_status": evidence.get("evidence_status"),
            "evidence_path": str(input_path),
            "timeline_path": str(timeline_path),
        },
        "diagnosis": {
            key: diagnosis[key]
            for key in (
                "status",
                "root_cause",
                "confidence",
                "first_failure_event_id",
                "supporting_event_ids",
                "contributing_event_ids",
                "causal_chain",
                "evidence",
                "counterevidence",
                "uncertainty",
                "evaluation_alignment",
            )
        },
        "execution": {
            "model_id": None,
            "engine": RULE_VERSION,
            "run_id": RULE_VERSION,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "rule_id": diagnosis["rule_id"],
            "rule_matches": diagnosis["rule_matches"],
        },
        "validation": {
            "root_cause_allowed": diagnosis["root_cause"] in ALLOWED_ROOT_CAUSES,
            "event_references_valid": not invalid_references,
            "evidence_citations_present": bool(diagnosis.get("evidence")),
            "trace_level": True,
            "errors": [
                "Referenced event is absent from evidence_event_ids: " + event_id
                for event_id in invalid_references
            ],
        },
        "contract": {
            "allowed_diagnosis_statuses": ["diagnosed", "uncertain", "not_applicable", "error"],
            "allowed_root_causes": ALLOWED_ROOT_CAUSES,
            "analysis_unit": "trace",
            "recovery_required": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}; use --overwrite explicitly")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    evidence_rows = load_jsonl(args.evidence)
    timelines = load_timelines(args.timeline)
    status_counts: Counter[str] = Counter()
    root_counts: Counter[str] = Counter()
    rule_counts: Counter[str] = Counter()
    results: list[dict[str, Any]] = []
    with args.output.open("w", encoding="utf-8") as handle:
        for evidence in evidence_rows:
            trace_id = evidence["trace_id"]
            diagnosis = diagnose(evidence, timelines.get(trace_id, []))
            result = build_result(evidence, diagnosis, args.evidence, args.timeline)
            handle.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            results.append(result)
            status_counts[diagnosis["status"]] += 1
            root_counts[diagnosis["root_cause"]] += 1
            rule_counts[diagnosis["rule_id"]] += 1

    summary = {
        "schema": "trace-diagnosis-rule-baseline-summary-v3",
        "rule_version": RULE_VERSION,
        "input_evidence": str(args.evidence),
        "input_timeline": str(args.timeline),
        "output": str(args.output),
        "result_count": len(results),
        "unique_trace_count": len({result["trace_id"] for result in results}),
        "by_diagnosis_status": dict(sorted(status_counts.items())),
        "by_root_cause": dict(sorted(root_counts.items())),
        "by_rule": dict(sorted(rule_counts.items())),
        "validation_errors": sum(len(result["validation"]["errors"]) for result in results),
    }
    summary_path = args.output.with_name("diagnosis_results_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
