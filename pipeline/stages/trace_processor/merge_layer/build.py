"""Merge frozen A/B/C results. No network, code execution, or annotation repair."""
import argparse
import copy
import json
import shutil
import re
from collections import Counter
from pathlib import Path

from ..fact_layer.sources import Sources, digest, pointer
from ..fact_layer.build import validate as validate_facts
from ..semantic_layer.evidence import encode, operation_input, requirement_input, validate_evidence_catalog
from ..semantic_layer.validate import schema_check, requirements as validate_requirements
from .citations import citations, locate, operation_issues


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checked(path, expected=None):
    actual = digest(path)
    if expected and actual != expected:
        raise ValueError("Frozen input hash mismatch: " + str(path))
    return actual


def validated_task(native, view, existing_review=None):
    """Replay a previously recorded, explicitly authorized description edit.

    This is not automatic model-output repair: no new patch is created here,
    and code/quotes/IDs/source positions cannot be changed by this path.
    """
    accepted = validate_requirements(native, view)
    if existing_review is None:
        return accepted
    edits = existing_review.get("annotation_patch", [])
    if len(edits) != 1 or not existing_review.get("authorization"):
        raise ValueError("Unsupported or unaudited historical review")
    edit = edits[0]
    if edit.get("op") != "replace" or not re.fullmatch(r"/requirements/[0-9]+/description", edit.get("path", "")):
        raise ValueError("Historical review may only replace a requirement description")
    index = int(edit["path"].split("/")[2])
    requirement = accepted["requirements"][index]
    if requirement["description"] != existing_review["before"] or requirement["requirement_id"] != existing_review["requirement_id"]:
        raise ValueError("Historical review before-value or requirement ID mismatch")
    if edit["value"] != existing_review["after"]:
        raise ValueError("Historical review after-value mismatch")
    requirement["description"] = edit["value"]
    return accepted


class Bundle:
    def __init__(self, root):
        self.root = Path(root)
        self.inputs = {}

    def add(self, path, expected=None):
        path = Path(path).resolve()
        sha = checked(path, expected)
        relative = Path("objects") / (sha + path.suffix)
        dest = self.root / relative
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        checked(dest, sha)
        record = {"original_path": str(path), "bundle_path": str(relative), "sha256": sha,
                  "bytes": path.stat().st_size}
        self.inputs[str(path)] = record
        return record


def bundled_sources(trace, root):
    root = Path(root).resolve()
    entries = copy.deepcopy(trace["facts"]["sources"])
    by_id = {r["source_id"]: r for r in trace["provenance"]["source_files"]}
    for entry in entries:
        if entry["exists"]:
            entry["path"] = str(root / by_id[entry["source_id"]]["bundle_path"])
    return Sources.from_entries(entries)


def validate_catalog(facts, view, sources):
    """Check every display block, including those without a fact pointer."""
    validate_evidence_catalog(facts, view["evidence"])
    # Rebuild the entire deterministic display with the bundled source registry.
    # Only source paths change in this temporary reconstruction; final facts do not.
    temporary = copy.deepcopy(facts)
    temporary["sources"] = sources.entries
    rebuilt = operation_input(temporary)
    if rebuilt != view:
        raise ValueError("Operation evidence/units differ from deterministic reconstruction")


def merge_one(row, source_root, jobs, bundle, operation_schema, allow_missing=False):
    rid = row["record_id"]
    fact_path = Path(row["fact_path"])
    fact_file = bundle.add(fact_path, row["fact_sha256"])
    facts = read(fact_path)
    source_files = []
    for source in facts["sources"]:
        if source["exists"]:
            source_files.append({"source_id": source["source_id"], **bundle.add(source["path"], source["sha256"])})
    shared = source_root / "shared" / rid
    inputs = {name: bundle.add(shared / name) for name in (
        "operation_evidence.json", "task_evidence.json", "requirements_input.json",
        "requirements_native.json", "requirements_accepted.json")}
    for path in sorted((shared / "requirements_call").glob("*.json")):
        inputs["requirements_call/" + path.name] = bundle.add(path)
    if (shared / "requirements_reuse.json").exists():
        inputs["requirements_reuse.json"] = bundle.add(shared / "requirements_reuse.json")
    review = None
    if (shared / "requirements_review.json").exists():
        inputs["requirements_review.json"] = bundle.add(shared / "requirements_review.json")
        review = read(shared / "requirements_review.json")
    view = read(shared / "operation_evidence.json")
    checked(shared / "operation_evidence.json", row["operation_view_sha256"])
    task_view = read(shared / "task_evidence.json")
    if task_view != requirement_input(facts):
        raise ValueError("Task evidence differs from fact instruction")
    task = read(shared / "requirements_accepted.json")
    if validated_task(read(shared / "requirements_native.json"), task_view, review) != task:
        raise ValueError("Accepted requirements differ from validated native output")
    annotations = {"task": task, "units": []}
    trace = {"schema_version": "data-agent-normalized-trace-v1", "facts": facts,
             "annotations": annotations, "evidence_catalog": task_view["evidence"] + view["evidence"],
             "citation_index": [], "analysis_index": [],
             "provenance": {"fact_file": fact_file, "source_files": source_files, "annotation_inputs": inputs,
                            "packets": [], "source_cohort": str(source_root)},
             "quality": {"semantic_correctness_certified": False, "diagnosis_accuracy_evaluated": False,
                         "quote_repair_performed": False, "fact_mutation_performed": False,
                         "missing_recording_content_recovered": False}}
    sources = bundled_sources(trace, bundle.root)
    validate_facts(facts, sources)
    validate_catalog(facts, view, sources)
    catalog = {e["evidence_id"]: e for e in trace["evidence_catalog"]}
    if len(catalog) != len(trace["evidence_catalog"]):
        raise ValueError("Duplicate evidence identifier")
    for path, citation in citations(task, "/annotations/task"):
        trace["citation_index"].append({"annotation_pointer": path,
            **locate(citation, catalog, {e["evidence_id"] for e in task_view["evidence"]})})
    targets = {u["unit_id"]: u for u in view["units"]}
    requirement_ids = {r["requirement_id"] for r in task["requirements"]}
    seen = set()
    for job in jobs:
        folder = source_root / job["directory"]
        packet_file = bundle.add(folder / "input.json", job["input_sha256"])
        output_file = bundle.add(folder / "llm_output.json")
        packet, native = read(folder / "input.json"), read(folder / "llm_output.json")
        schema_check(native, operation_schema)
        packet_targets = {u["unit_id"]: u for u in packet["target_units"]}
        returned = [u["unit_id"] for u in native["units"]]
        if len(set(returned)) != len(returned) or set(returned) != set(packet_targets) or set(returned) != set(job["target_units"]):
            raise ValueError("Native packet unit coverage mismatch")
        packet_ids = {e["evidence_id"] for e in packet["evidence"]}
        for block in packet["evidence"]:
            if block != {k: catalog[block["evidence_id"]][k] for k in block}:
                raise ValueError("Packet evidence differs from source catalog")
        packet_audit = {"packet": job["packet"], "input": packet_file, "native_output": output_file,
                        "original_result": bundle.add(folder / "result.json"), "call_files": []}
        if (folder / "reuse.json").exists():
            packet_audit["reuse_projection"] = bundle.add(folder / "reuse.json")
            packet_audit["original_native_output"] = bundle.add(folder / "original_native_output.json")
        for path in sorted((folder / "call").glob("*.json")):
            packet_audit["call_files"].append(bundle.add(path))
        trace["provenance"]["packets"].append(packet_audit)
        for native_index, native_unit in enumerate(native["units"]):
            uid = native_unit["unit_id"]
            if uid in seen or packet_targets[uid] != targets[uid]:
                raise ValueError("Duplicate or changed annotation target")
            seen.add(uid)
            target = targets[uid]
            allowed = (set(target["evidence_ids"]) | set(target["context_evidence_ids"])) & packet_ids
            unit = copy.deepcopy(native_unit)
            index = len(annotations["units"])
            base = f"/annotations/units/{index}"
            unit.update(step_id=target["step_id"], model_exchange_ref=target["model_exchange_ref"],
                        native_origin={"packet": job["packet"], "bundle_path": output_file["bundle_path"],
                                       "sha256": output_file["sha256"], "json_pointer": f"/units/{native_index}"})
            issues = []
            for n, op in enumerate(unit["operations"]):
                issues.extend({"annotation_pointer": base + f"/operations/{n}", "issue": issue}
                              for issue in operation_issues(op, target, catalog, requirement_ids))
            resolutions = [{"annotation_pointer": path, "unit_id": uid, **locate(c, catalog, allowed)}
                           for path, c in citations(native_unit, base)]
            trace["citation_index"].extend(resolutions)
            issues.extend({"annotation_pointer": c["annotation_pointer"], "issue": issue}
                          for c in resolutions for issue in c["issues"])
            unit["validation"] = {"status": "with_warnings" if issues else "mechanically_valid",
                                  "issues": issues, "semantic_correctness_certified": False}
            annotations["units"].append(unit)
    if seen != set(targets) and not allow_missing:
        raise ValueError("Missing native annotations; Stage D does not invent them")
    for i, step in enumerate(facts["steps"]):
        attached = [(n, u) for n, u in enumerate(annotations["units"]) if u["step_id"] == step["step_id"]]
        trace["analysis_index"].append({"step_id": step["step_id"], "fact_pointer": f"/facts/steps/{i}",
            "model_exchange_ref": step["model_exchange_ref"], "tool_call_id": step["tool_call_id"],
            "unit_ids": [u["unit_id"] for _, u in attached],
            "operation_pointers": [f"/annotations/units/{n}/operations/{j}" for n, u in attached for j in range(len(u["operations"]))],
            "artifact_refs": step["artifact_refs"],
            "order_scope": "recorded model response/block order; shared exchange does not establish serial execution"})
    counts = Counter(c["status"] for c in trace["citation_index"])
    trace["quality"].update(status="incomplete_annotations" if seen != set(targets) else "with_warnings" if counts["unresolved"] or any(u["validation"]["issues"] for u in annotations["units"]) else "mechanically_valid",
        citation_counts=dict(counts), units_retained=len(seen),
        units_mechanically_valid=sum(not u["validation"]["issues"] for u in annotations["units"]),
        tool_steps_retained=len(facts["steps"]), model_exchanges_retained=len(facts["model_exchanges"]),
        semantic_operations_retained=sum(len(u["operations"]) for u in annotations["units"]),
        original_recording_limits=copy.deepcopy(facts["quality"]),
        content_completeness_scope="All frozen A facts and all native B/C annotations; no guarantee of unrecorded content or exhaustive semantic interpretation")
    if allow_missing:
        trace["quality"].update(planned_units=len(targets), missing_unit_ids=sorted(set(targets)-seen))
    return trace


def validate_final(trace, bundle_root, original_fact):
    """Read persisted output and resolve only bundled sources, without raw paths."""
    facts = trace["facts"]
    if facts != original_fact:
        raise ValueError("Fact roundtrip mismatch")
    sources = bundled_sources(trace, bundle_root)
    validate_facts(facts, sources)
    inputs = trace["provenance"]["annotation_inputs"]
    archived_task = read(Path(bundle_root) / inputs["requirements_accepted.json"]["bundle_path"])
    if trace["annotations"]["task"] != archived_task:
        raise ValueError("Task semantic content was modified")
    view = read(Path(bundle_root) / inputs["operation_evidence.json"]["bundle_path"])
    validate_catalog(facts, view, sources)
    refs = 0
    def walk(value):
        nonlocal refs
        if isinstance(value, dict):
            if "source_id" in value and ("json_pointer" in value or "line" in value):
                sources.resolve(value)
                refs += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(facts)
    for x in facts["model_exchanges"]:
        sources.resolve(x["request_ref"])
        sources.resolve(x["response_ref"])
    native_count = 0
    for unit in trace["annotations"]["units"]:
        origin = unit["native_origin"]
        path = Path(bundle_root) / origin["bundle_path"]
        checked(path, origin["sha256"])
        native = pointer(read(path), origin["json_pointer"])
        if {k: unit[k] for k in native} != native:
            raise ValueError("Native semantic content was modified")
        packet = next(p for p in trace["provenance"]["packets"] if p["packet"] == origin["packet"])
        if "reuse_projection" in packet:
            reuse = read(Path(bundle_root) / packet["reuse_projection"]["bundle_path"])
            original = read(Path(bundle_root) / packet["original_native_output"]["bundle_path"])
            if original["units"][reuse["source_unit_index"]] != native:
                raise ValueError("Reused native unit differs from original full response")
        native_count += 1
    for c in trace["citation_index"]:
        supplied = pointer(trace, c["annotation_pointer"])
        if any(c[k] != supplied[k] for k in ("evidence_id", "quote")):
            raise ValueError("Citation was modified")
        if c["eligible_as_literal_evidence"]:
            block = next(e for e in trace["evidence_catalog"] if e["evidence_id"] == c["evidence_id"])
            for span in c["block_char_spans"]:
                if block["text"][span["start"]:span["end"]] != c["quote"]:
                    raise ValueError("Invalid citation character range")
        if "unit_id" in c:
            target = next(u for u in view["units"] if u["unit_id"] == c["unit_id"])
            allowed = set(target["evidence_ids"]) | set(target["context_evidence_ids"])
        else:
            allowed = {e["evidence_id"] for e in requirement_input(facts)["evidence"]}
        rebound = locate(supplied, {e["evidence_id"]: e for e in trace["evidence_catalog"]}, allowed)
        if any(c[k] != rebound[k] for k in rebound):
            raise ValueError("Citation qualification differs from recomputed exact/scope check")
    return {"fact_roundtrip": "passed", "native_annotations_unchanged": native_count,
            "bundled_source_hashes": "passed", "source_reference_occurrences_resolved": refs,
            "full_model_request_response_pairs_resolved": len(facts["model_exchanges"]),
            "citation_ranges": "passed", "original_paths_required_for_resolution": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.source, args.output = args.source.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = Bundle(args.output)
    cohort_audit = {name: bundle.add(args.source / name) for name in (
        "selected_traces.json", "jobs.json", "conclusion.json", "native_integrity_checks.json",
        "frozen_source_anchors.json", "source_anchor_outputs.json", "source_anchor_comparison.json")}
    save(args.output / "source_cohort_provenance.json", cohort_audit)
    rows, jobs = read(args.source / "selected_traces.json"), read(args.source / "jobs.json")
    contracts = {p.name: bundle.add(p) for p in sorted((args.source / "contracts").glob("*")) if p.is_file()}
    schema = read(args.source / "contracts" / "operations.schema.json")
    from .schema import schema as final_schema
    output_schema = final_schema(read(Path(__file__).parents[1] / "fact_layer" / "schema.json"), schema)
    save(args.output / "normalized_trace.schema.json", output_schema)
    reports = []
    for row in rows:
        trace = merge_one(row, args.source, [j for j in jobs if j["record_id"] == row["record_id"]], bundle, schema)
        relative = Path("traces") / (row["record_id"] + ".json")
        trace["provenance"]["frozen_contracts"] = contracts
        save(args.output / relative, trace)
        persisted = read(args.output / relative)
        schema_check(persisted, output_schema)
        report = validate_final(persisted, args.output, read(row["fact_path"]))
        report["normalized_schema"] = "passed"
        reports.append({"record_id": row["record_id"], "benchmark": row["benchmark"], "agent_id": row["agent_id"],
                        "task_id": row["task_id"], "trace_path": str(relative), "trace_sha256": digest(args.output / relative),
                        "quality": trace["quality"], "validation": report})
        print(row["task_id"], trace["quality"]["status"], flush=True)
    save(args.output / "validation.json", reports)
    # Detect changes to ANY consumed input over the full merge, including the
    # historical failed packets whose native annotations are retained here.
    for record in bundle.inputs.values():
        checked(record["original_path"], record["sha256"])
    save(args.output / "input_manifest.json", list(bundle.inputs.values()))
    from .downstream import export
    export(args.output)


if __name__ == "__main__":
    main()
