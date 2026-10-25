#!/usr/bin/env python3
"""Build trace-level evidence bundles without assigning a root cause."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb


EVIDENCE_SCHEMA = "trace-level-evidence-v2"
EDGE_SCHEMA = "trace-evidence-edge-v1"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def add_reason(reasons: dict[str, list[str]], event_id: str | None, reason: str) -> None:
    if event_id:
        if reason not in reasons[event_id]:
            reasons[event_id].append(reason)


def evidence_status(sample: dict[str, Any]) -> str:
    event_failure = bool(sample.get("event_failure_observed"))
    outcome = (sample.get("evaluation") or {}).get("outcome", "unknown")
    if event_failure and outcome == "failure":
        return "event_and_evaluation_failure"
    if event_failure and outcome == "success":
        return "event_failure_evaluation_success"
    if event_failure:
        return "event_failure_evaluation_unknown"
    if outcome == "failure":
        return "evaluation_failure_without_event_failure"
    if outcome == "success":
        return "no_event_failure_evaluation_success"
    return "evaluation_unknown"


def compact_trace_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: event[key]
        for key in (
            "event_id",
            "sequence",
            "event_type",
            "actor",
            "stage",
            "status",
            "timestamp",
            "turn_id",
            "tool_name",
            "call_id",
            "call_ids",
            "parent_event_id",
            "step_group_id",
        )
        if key in event and event[key] not in (None, "", [], {})
    }


def load_timeline(path: Path) -> dict[str, list[dict[str, Any]]]:
    timelines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in load_jsonl(path):
        timelines[event["trace_id"]].append(event)
    for events in timelines.values():
        events.sort(key=lambda event: (event.get("sequence", 0), event.get("event_id", "")))
    return dict(timelines)


def build_evidence_sample(
    sample: dict[str, Any],
    timeline: list[dict[str, Any]],
    edge_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_id = {event["event_id"]: event for event in timeline}
    by_group: dict[str, list[str]] = defaultdict(list)
    by_call: dict[str, list[str]] = defaultdict(list)
    edges: list[dict[str, Any]] = []
    for event in timeline:
        event_id = event["event_id"]
        edges.append({
            "schema": EDGE_SCHEMA,
            "trace_id": sample["trace_id"],
            "source_event_id": event_id,
            "target_id": f"trace:{sample['trace_id']}",
            "relation": "trace_membership",
            "relation_key": sample["trace_id"],
        })
        parent_id = event.get("parent_event_id")
        if parent_id:
            edges.append({
                "schema": EDGE_SCHEMA,
                "trace_id": sample["trace_id"],
                "source_event_id": event_id,
                "target_id": parent_id,
                "relation": "parent_event",
                "relation_key": None,
            })
        call_ids = event.get("call_ids") or []
        if event.get("call_id") and event["call_id"] not in call_ids:
            call_ids = [event["call_id"], *call_ids]
        for call_id in dict.fromkeys(str(value) for value in call_ids if value not in (None, "")):
            by_call[call_id].append(event_id)
            edges.append({
                "schema": EDGE_SCHEMA,
                "trace_id": sample["trace_id"],
                "source_event_id": event_id,
                "target_id": f"call:{call_id}",
                "relation": "call_membership",
                "relation_key": call_id,
            })
        group_id = event.get("step_group_id")
        if group_id:
            group_id = str(group_id)
            by_group[group_id].append(event_id)
            edges.append({
                "schema": EDGE_SCHEMA,
                "trace_id": sample["trace_id"],
                "source_event_id": event_id,
                "target_id": f"step_group:{group_id}",
                "relation": "step_group_membership",
                "relation_key": group_id,
            })

    first_failure = sample.get("first_failure") or {}
    candidate_ids: list[str] = []
    reasons: dict[str, list[str]] = defaultdict(list)

    def candidate(event_id: str | None, reason: str) -> None:
        if not event_id or event_id not in by_id:
            return
        if event_id not in candidate_ids:
            candidate_ids.append(event_id)
        add_reason(reasons, event_id, reason)

    candidate(first_failure.get("event_id"), "first_failure_candidate")
    for event in sample.get("failure_candidates", []):
        candidate(event.get("event_id"), "failure_candidate")
    for event in sample.get("evaluation_events", []):
        candidate(event.get("event_id"), "evaluation_event")

    evaluation = sample.get("evaluation") or {}
    if not sample.get("event_failure_observed") and evaluation.get("outcome") == "failure":
        final_event = next(
            (
                event
                for event in reversed(timeline)
                if event.get("actor") == "assistant" or event.get("event_type") == "message"
            ),
            timeline[-1] if timeline else None,
        )
        if final_event:
            candidate(final_event.get("event_id"), "final_output_before_evaluation_failure")

    selected_ids: set[str] = set(candidate_ids)
    for event in sample.get("context_events", []):
        if event.get("event_id") in by_id:
            selected_ids.add(event["event_id"])
            add_reason(reasons, event["event_id"], "failure_context_window")

    for event_id in list(candidate_ids):
        event = by_id[event_id]
        parent_id = event.get("parent_event_id")
        if parent_id in by_id:
            selected_ids.add(parent_id)
            add_reason(reasons, parent_id, "parent_of_candidate")
        group_id = event.get("step_group_id")
        for related_id in by_group.get(str(group_id), []):
            selected_ids.add(related_id)
            add_reason(reasons, related_id, "same_step_group")
        call_ids = event.get("call_ids") or []
        if event.get("call_id") and event["call_id"] not in call_ids:
            call_ids = [event["call_id"], *call_ids]
        for call_id in call_ids:
            for related_id in by_call.get(str(call_id), []):
                selected_ids.add(related_id)
                add_reason(reasons, related_id, "same_call_id")

    # Detailed payloads are already present in the input context. Add the
    # relation-only timeline nodes only when they are not already detailed.
    detailed_by_id: dict[str, dict[str, Any]] = {}
    for event in (
        sample.get("context_events", [])
        + sample.get("failure_candidates", [])
        + sample.get("evaluation_events", [])
    ):
        if event.get("event_id"):
            detailed_by_id[event["event_id"]] = event
    evidence_events: list[dict[str, Any]] = []
    for event_id in sorted(selected_ids, key=lambda value: (by_id[value].get("sequence", 0), value)):
        event = detailed_by_id.get(event_id) or compact_trace_event(by_id[event_id])
        evidence_events.append({
            **event,
            "evidence_reasons": reasons.get(event_id, []),
        })

    relation_counts = Counter(edge["relation"] for edge in edges)
    output = {
        "schema": EVIDENCE_SCHEMA,
        "evidence_id": f"evidence:{sample['trace_id']}",
        "trace_id": sample["trace_id"],
        "analysis_unit": "trace",
        "evidence_status": evidence_status(sample),
        "event_failure_observed": sample.get("event_failure_observed", False),
        "evaluation": evaluation,
        "evaluation_artifact": sample.get("evaluation_artifact", {}),
        "trace_summary": sample.get("trace_summary", {}),
        "candidate_event_ids": candidate_ids,
        "evidence_event_ids": [event["event_id"] for event in evidence_events],
        "evidence_events": evidence_events,
        "relation_counts": dict(sorted(relation_counts.items())),
        "relation_edge_ref": {
            "path": str(edge_path),
            "trace_id": sample["trace_id"],
            "relation_types": [
                "trace_membership",
                "parent_event",
                "call_membership",
                "step_group_membership",
            ],
        },
        "trace_timeline_ref": sample.get("trace_timeline_ref"),
        "builder_contract": {
            "root_cause_assigned": False,
            "event_payloads_are_evidence_only": True,
            "all_events_reachable_by_trace_id": True,
        },
    }
    return output, edges


def materialize(output_dir: Path, sample_path: Path, edge_path: Path) -> dict[str, Any]:
    duckdb_path = output_dir / "trace_evidence.duckdb"
    sample_parquet = output_dir / "trace_evidence.parquet"
    edge_parquet = output_dir / "trace_evidence_edges.parquet"
    temp_dir = output_dir / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(duckdb_path))
    try:
        connection.execute("SET preserve_insertion_order=false")
        connection.execute("SET threads=4")
        connection.execute("SET temp_directory=?", [str(temp_dir)])
        for view in ("failure_evidence", "evaluation_mismatch_evidence"):
            connection.execute(f"DROP VIEW IF EXISTS {view}")
        for table in ("trace_evidence", "trace_evidence_edges", "conversion_metadata"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute(
            """
            CREATE TABLE trace_evidence AS
            SELECT * FROM read_json_auto(?, format='newline_delimited', sample_size=10000, union_by_name=true)
            """,
            [str(sample_path)],
        )
        connection.execute(
            """
            CREATE TABLE trace_evidence_edges AS
            SELECT * FROM read_json_auto(?, format='newline_delimited', sample_size=-1, union_by_name=true)
            """,
            [str(edge_path)],
        )
        connection.execute("COPY trace_evidence TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(sample_parquet)])
        connection.execute("COPY trace_evidence_edges TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(edge_parquet)])
        connection.execute(
            "CREATE VIEW failure_evidence AS SELECT * FROM trace_evidence WHERE evidence_status LIKE '%failure%'"
        )
        connection.execute(
            "CREATE VIEW evaluation_mismatch_evidence AS SELECT * FROM trace_evidence WHERE evidence_status = 'evaluation_failure_without_event_failure'"
        )
        metadata = {
            "schema": "trace-evidence-query-materialization-v1",
            "sample_path": str(sample_path),
            "edge_path": str(edge_path),
            "duckdb": str(duckdb_path),
            "sample_parquet": str(sample_parquet),
            "edge_parquet": str(edge_parquet),
            "trace_count": connection.execute("SELECT count(*) FROM trace_evidence").fetchone()[0],
            "edge_count": connection.execute("SELECT count(*) FROM trace_evidence_edges").fetchone()[0],
        }
        connection.execute("CREATE TABLE conversion_metadata (key VARCHAR, value VARCHAR)")
        connection.executemany("INSERT INTO conversion_metadata VALUES (?, ?)", list(metadata.items()))
        connection.commit()
        return metadata
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    samples = load_jsonl(args.inputs)
    timelines = load_timeline(args.timeline)
    sample_path = args.output_dir / "trace_evidence.jsonl"
    edge_path = args.output_dir / "trace_evidence_edges.jsonl"
    status_counts: Counter[str] = Counter()
    with sample_path.open("w", encoding="utf-8") as sample_handle, edge_path.open("w", encoding="utf-8") as edge_handle:
        for sample in samples:
            evidence, edges = build_evidence_sample(
                sample,
                timelines.get(sample["trace_id"], []),
                edge_path,
            )
            sample_handle.write(json.dumps(evidence, ensure_ascii=False, default=str) + "\n")
            for edge in edges:
                edge_handle.write(json.dumps(edge, ensure_ascii=False, default=str) + "\n")
            status_counts[evidence["evidence_status"]] += 1

    metadata = materialize(args.output_dir, sample_path, edge_path)
    metadata["builder_schema"] = EVIDENCE_SCHEMA
    metadata["evidence_status_counts"] = dict(sorted(status_counts.items()))
    (args.output_dir / "summary.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
