"""Verify a copied Stage D bundle using its portable files only."""
import argparse
from pathlib import Path

from .build import checked, read, save, validate_final
from ..semantic_layer.validate import schema_check


def verify(root):
    root = Path(root)
    manifest = read(root / "manifest.json")
    for record in manifest["files"]:
        checked(root / record["path"], record["sha256"])
    reports = []
    schema = read(root / "normalized_trace.schema.json")
    for row in read(root / "validation.json"):
        path = root / row["trace_path"]
        checked(path, row["trace_sha256"])
        trace = read(path)
        schema_check(trace, schema)
        archived_fact = root / trace["provenance"]["fact_file"]["bundle_path"]
        checked(archived_fact, trace["provenance"]["fact_file"]["sha256"])
        result = validate_final(trace, root, read(archived_fact))
        reports.append({"record_id": row["record_id"], "normalized_schema": "passed", **result})
    return {"status": "passed", "traces": len(reports), "files_verified": len(manifest["files"]),
            "uses_original_source_paths": False, "reports": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.bundle)
    if args.report:
        save(args.report, result)
    print(result["status"], result["traces"], "traces", result["files_verified"], "files")
