"""Explicit standalone CLI; leaves the legacy semantic pipeline unchanged."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import time

from . import SCHEMA_VERSION
from .build import build
from .sources import digest


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline fact-layer construction; no model calls, no recorded code execution")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True, help="Must be a new directory")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    manifest = Path(args.manifest).resolve()
    manifest_sha = digest(manifest)
    with manifest.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if args.limit is not None:
        if args.limit <= 0:
            parser.error("--limit must be positive")
        rows = rows[:args.limit]
    ids = [r["record_id"] for r in rows]
    if not all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", i) for i in ids):
        parser.error("record_id must be a safe filename component")
    if len(ids) != len(set(ids)):
        parser.error("Duplicate record_id in input manifest")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "traces").mkdir()
    source = Path(__file__).parent
    code_hashes = {p.name: digest(p) for p in sorted(source.iterdir()) if p.suffix in (".py", ".json", ".md")}
    scripts = source.parents[3] / "scripts"
    verifier_hashes = {str(scripts / name): digest(scripts / name) for name in (
        "curate_complete_traces.py", "claude_execution_capture.py", "codex_complete_capture.py",
        "build_trace_processor_v2_manifest.py")}
    write(output / "run_config.json", {"schema_version": SCHEMA_VERSION, "manifest": str(manifest),
                                      "manifest_sha256": manifest_sha, "selected_records": len(rows),
                                      "implementation_sha256": code_hashes, "capture_verifier_sha256": verifier_hashes, "model_requests": 0,
                                      "recorded_code_executed": False})
    started = time.monotonic()
    index = []
    for i, row in enumerate(rows, 1):
        try:
            trace = build(row)
            path = output / "traces" / (row["record_id"] + ".json")
            write(path, trace)
            record = {k: row[k] for k in ("record_id", "benchmark", "agent_id", "task_id")}
            record.update(path=str(path.relative_to(output)), sha256=digest(path), status=trace["quality"]["status"],
                          adapter=trace["trace_metadata"]["adapter"], model_exchanges=len(trace["model_exchanges"]),
                          steps=len(trace["steps"]), unobserved_generated_calls=len(trace["generated_calls_without_observed_submission"]),
                          data_operations=sum(len(s["action"]["static_extraction"]["data_operations"]) for s in trace["steps"]),
                          artifacts=len(trace["deliverables"]), warnings=trace["quality"]["warnings"])
        except Exception as exc:
            # Isolate a failed trace without certifying a partial output. Original files untouched.
            record = {"record_id": row["record_id"], "status": "failed", "error_type": type(exc).__name__, "error": str(exc)}
        index.append(record)
        with (output / "manifest.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"processed": i, "total": len(rows), **record}, ensure_ascii=False), flush=True)
    failures = [r for r in index if r["status"] == "failed"]
    summary = {"schema_version": SCHEMA_VERSION, "records": len(rows), "succeeded": len(rows)-len(failures),
               "failed": len(failures), "by_adapter": dict(Counter(r.get("adapter") for r in index if r["status"] != "failed")),
               "model_exchanges": sum(r.get("model_exchanges", 0) for r in index), "steps": sum(r.get("steps", 0) for r in index),
               "unobserved_generated_calls": sum(r.get("unobserved_generated_calls", 0) for r in index),
               "warnings": sum(len(r.get("warnings", [])) for r in index), "failures": failures,
               "elapsed_seconds": round(time.monotonic()-started, 3), "model_requests": 0}
    if digest(manifest) != manifest_sha:
        summary["input_changed"] = True
    if any(digest(source / name) != h for name, h in code_hashes.items()):
        summary["implementation_changed_during_run"] = True
    if any(digest(path) != h for path, h in verifier_hashes.items()):
        summary["implementation_changed_during_run"] = True
    write(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if failures or summary.get("input_changed") or summary.get("implementation_changed_during_run") else 0


if __name__ == "__main__":
    raise SystemExit(main())
