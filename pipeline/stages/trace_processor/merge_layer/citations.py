"""Locate literal evidence without repairing or endorsing model statements."""
import re


def citations(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key == "evidence" and isinstance(item, list):
                for i, citation in enumerate(item):
                    if isinstance(citation, dict) and "quote" in citation:
                        yield child + f"/{i}", citation
            else:
                yield from citations(item, child)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from citations(item, path + f"/{i}")


def locate(citation, catalog, allowed):
    """Exact matches only; an exact quote in a different block is not relocated."""
    eid, quote = citation["evidence_id"], citation["quote"]
    issues = []
    block = catalog.get(eid)
    if block is None:
        issues.append("unknown_evidence_id")
    if eid not in allowed:
        issues.append("out_of_unit_scope")
    offsets = []
    if block:
        offsets = [m.start() for m in re.finditer(re.escape(quote), block["text"])] if quote.strip() else []
        if not offsets:
            issues.append("quote_not_exact_substring")
    exact = bool(offsets) and not issues
    return {
        "evidence_id": eid, "quote": quote,
        "status": ("exact_unique" if len(offsets) == 1 else "exact_multiple") if exact else "unresolved",
        "issues": issues,
        "eligible_as_literal_evidence": exact,
        "match_count": len(offsets),
        "block_char_spans": [{"start": n, "end": n + len(quote)} for n in offsets] if exact else [],
        "field_char_spans": [{"start": block.get("char_start", 0) + n,
                              "end": block.get("char_start", 0) + n + len(quote)} for n in offsets] if exact else [],
        "fact_pointer": block.get("fact_pointer") if block else None,
        "original_source_ref": block.get("original_source_ref") if block else None,
        "coordinate_system": "Unicode character offsets in the declared fact/display representation; not raw file byte offsets",
        "semantic_entailment_verified": False,
    }


def operation_issues(operation, target, catalog, requirement_ids):
    """Mechanical association/phase checks; never infer task success or root cause."""
    issues = []
    own = set(target["evidence_ids"])
    ids = {c["evidence_id"] for c in operation["evidence"]}
    if not ids & own:
        issues.append("operation_missing_target_evidence")
    if operation["phase"] == "observed_in_result" and not any(
        catalog.get(eid, {}).get("role") == "tool_observation" for eid in ids & own
    ):
        issues.append("observed_phase_without_local_observation")
    for link in operation["requirement_links"]:
        if link["requirement_id"] not in requirement_ids:
            issues.append("unknown_requirement_id:" + link["requirement_id"])
    return issues
