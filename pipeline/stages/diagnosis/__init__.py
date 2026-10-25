"""Shared evidence inventory; semantic diagnosis remains a pinned method."""
from collections import Counter
from pathlib import Path
from ...common import rows, digest, write, fresh_output
from ...config import STAGES
from ... import frozen_method


def plan(config):
    if config["method"]["entrypoint"]:
        return frozen_method.plan(config)
    source = Path(config["input"]["manifest"])
    if not source.is_file():
        raise FileNotFoundError("Evidence inventory requires input.manifest")
    return {"operation": "diagnosis_evidence_inventory_only", "manifest": str(source),
            "manifest_sha256": digest(source), "semantic_diagnosis": False, "model_requests": 0}


def execute(config, selection):
    if config["method"]["entrypoint"]:
        return frozen_method.execute(config, selection)
    output = fresh_output(config, STAGES["diagnosis"])
    if digest(selection["manifest"]) != selection["manifest_sha256"]:
        raise RuntimeError("Manifest changed")
    groups = Counter()
    for _, row in rows(selection["manifest"]):
        score = row.get("evaluation_score")
        bucket = "missing_score" if score is None else "zero_score" if score == 0 else "recorded_nonzero_score"
        groups[(row.get("benchmark"), row.get("agent_id"), bucket)] += 1
    data = [{"benchmark": key[0], "agent": key[1], "score_bucket": key[2], "count": count} for key, count in groups.items()]
    write(output / "evidence-inventory.json", {"plan": selection, "groups": data,
                                              "agent_failure_causes_inferred": False})
    return {"output": str(output), "groups": len(groups), "semantic_diagnosis": False, "model_requests": 0}
