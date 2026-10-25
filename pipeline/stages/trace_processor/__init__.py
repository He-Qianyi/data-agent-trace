"""Deterministic source index, or explicitly pinned semantic method launcher."""
from pathlib import Path
from ...common import rows, digest, fresh_output, write
from ...config import STAGES
from ... import frozen_method


def plan(config):
    if config["method"]["entrypoint"]:
        return frozen_method.plan(config)
    source = Path(config["input"]["manifest"])
    if not source.is_file():
        raise FileNotFoundError("Source-index mode requires input.manifest")
    return {"operation": "deterministic_source_index_only", "manifest": str(source),
            "manifest_sha256": digest(source), "semantic_annotation": False,
            "model_requests": 0, "source_content_copied": False}


def execute(config, selection):
    if config["method"]["entrypoint"]:
        return frozen_method.execute(config, selection)
    output = fresh_output(config, STAGES["trace_processor"])
    if digest(selection["manifest"]) != selection["manifest_sha256"]:
        raise RuntimeError("Input manifest changed")
    index = []
    for row_index, row in rows(selection["manifest"]):
        for source_index, source in enumerate(row.get("sources", [])):
            path = Path(source["path"])
            index.append({"trace_id": row["trace_id"], "manifest_line_index": row_index,
                          "source_index": source_index, "path": str(path),
                          "exists": path.is_file(), "sha256": digest(path) if path.is_file() else None,
                          "registered_sha256": row.get("source_hashes", {}).get(str(path))})
    write(output / "source-index.json", {"plan": selection, "sources": index,
                                         "semantic_values_filled_by_script": False})
    return {"output": str(output), "indexed_sources": len(index), "semantic_annotation": False, "model_requests": 0}
