"""Mechanical contracts and literal evidence checks, never an LLM quality verdict."""
import copy
import re

import jsonschema

from .contract import REQUIREMENT_SCHEMA, OPERATION_SCHEMA


def schema_check(value, schema):
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        error = errors[0]
        raise ValueError("schema:" + "/".join(str(p) for p in error.path) + ":" + error.validator)


def bind_citation(citation, evidence_by_id):
    eid, quote = citation["evidence_id"], citation["quote"]
    if eid not in evidence_by_id:
        raise ValueError("unknown_evidence_id:" + eid)
    evidence = evidence_by_id[eid]
    if not quote.strip() or quote not in evidence["text"]:
        raise ValueError("quote_not_exact_substring:" + eid)
    matches = [m.start() for m in re.finditer(re.escape(quote), evidence["text"])]
    return {**citation, "location": {k:evidence.get(k) for k in (
        "role", "step_id", "fact_pointer", "representation", "original_source_ref")},
        "match_count": len(matches), "candidate_char_spans": [
            {"char_start": evidence.get("char_start",0)+n, "char_end": evidence.get("char_start",0)+n+len(quote)} for n in matches[:20]],
        "candidate_spans_partial": len(matches)>20,
        "interpretation_is_llm_generated": True}


def _bind_all(value, by_id):
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "evidence":
                value[key] = [bind_citation(c,by_id) for c in item]
            else:
                _bind_all(item,by_id)
    elif isinstance(value,list):
        for item in value:
            _bind_all(item,by_id)


def requirements(parsed, view):
    schema_check(parsed, REQUIREMENT_SCHEMA)
    by_id = {e["evidence_id"]:e for e in view["evidence"]}
    coverage = [c["evidence_id"] for c in parsed["coverage"]]
    if len(coverage) != len(set(coverage)) or set(coverage) != set(by_id):
        raise ValueError("task_block_coverage_mismatch")
    cited = {c["evidence_id"] for r in parsed["requirements"] for c in r["evidence"]}
    for block in parsed["coverage"]:
        if block["disposition"] == "requirement" and block["evidence_id"] not in cited:
            raise ValueError("task_requirement_block_not_supported:" + block["evidence_id"])
    result = copy.deepcopy(parsed)
    _bind_all(result, by_id)
    for i, requirement in enumerate(result["requirements"],1):
        requirement["requirement_id"] = f"R{i:04d}"
        requirement["hard_requirement_certified"] = False
    result.update(task_prompt_sha256=view["task_prompt_sha256"],
                  validation={"schema":"passed","literal_evidence":"passed","task_block_accounting":"passed",
                              "semantic_completeness_certified":False})
    return result


def operations(parsed, packet, full_catalog):
    schema_check(parsed, OPERATION_SCHEMA)
    targets = {u["unit_id"]:u for u in packet["target_units"]}
    returned = [u["unit_id"] for u in parsed["units"]]
    if len(returned) != len(set(returned)) or set(returned) != set(targets):
        raise ValueError("operation_unit_coverage_mismatch")
    allowed = {e["evidence_id"] for e in packet["evidence"]}
    by_id = {e["evidence_id"]:e for e in full_catalog if e["evidence_id"] in allowed}
    requirement_ids = {r["requirement_id"] for r in packet["requirements"]}
    for unit in parsed["units"]:
        target_ids = set(targets[unit["unit_id"]]["evidence_ids"])
        context_ids = set(targets[unit["unit_id"]]["context_evidence_ids"])
        for op in unit["operations"]:
            cited_ids = {c["evidence_id"] for c in op["evidence"]}
            if not cited_ids & target_ids:
                raise ValueError("operation_missing_target_evidence:" + unit["unit_id"])
            if cited_ids - target_ids - context_ids:
                raise ValueError("operation_cites_another_step:" + unit["unit_id"])
            if op["phase"] == "observed_in_result" and not any(by_id[e]["role"] == "tool_observation" for e in cited_ids if e in by_id):
                raise ValueError("observed_phase_without_observation_evidence")
            for detail in op["details"]:
                if {c["evidence_id"] for c in detail["evidence"]} - target_ids - context_ids:
                    raise ValueError("detail_cites_another_step")
            for link in op["requirement_links"]:
                if link["requirement_id"] not in requirement_ids:
                    raise ValueError("unknown_requirement_id:" + link["requirement_id"])
                if {c["evidence_id"] for c in link["evidence"]} - target_ids - context_ids:
                    raise ValueError("requirement_link_cites_another_step")
    result = copy.deepcopy(parsed)
    _bind_all(result,by_id)
    for unit in result["units"]:
        unit["step_id"] = targets[unit["unit_id"]]["step_id"]
        for i,op in enumerate(unit["operations"],1):
            op["operation_id"] = unit["unit_id"] + f"-O{i:03d}"
            op["execution_or_correctness_certified"] = False
    return result
