"""CodeTracer detailed-compatible acceptance, separate from evidence/semantic audits.

Step IDs keep the local recorder's string type. No output is rewritten. The
historical strict validators remain pinned to their original experiment plans.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

POLICY_ID = "codetracer-detailed-compatible-v1"
UPSTREAM_COMMIT = "2d302191dd07e7c0c2da6f7a5e9451c7cbb62d34"
UPSTREAM_URL = (
    "https://github.com/NJU-LINK/CodeTracer/blob/"
    + UPSTREAM_COMMIT
    + "/src/codetracer/services/validation.py"
)
CORE_FIELDS = (
    "root_cause_chain", "critical_decision_points", "correct_strategy",
    "stage_labels", "summary",
)


def validate_diagnosis(data: Any, valid_step_ids: Iterable[Any]) -> dict[str, Any]:
    """Apply the checks in CodeTracer's _validate_detailed.

    Additional keys, evidence citations and causal correctness do not gate this
    acceptance result. Malformed containers that would crash upstream fail
    gracefully; otherwise the same checks and permissiveness are retained.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        errors.append(f"Expected JSON object, got {type(data).__name__}")
    else:
        for key in CORE_FIELDS:
            if key not in data:
                errors.append(f"Missing required key '{key}'")
        valid_ids = set(valid_step_ids)
        try:
            for i, point in enumerate(data.get("critical_decision_points", [])):
                if isinstance(point, dict):
                    sid = point.get("step_id")
                    if sid is not None and sid not in valid_ids:
                        errors.append(f"Decision point {i}: step_id {sid} not in trajectory")
        except TypeError:
            errors.append("Malformed decision points or unhashable step_id")
    return {
        "passed": not errors,
        "errors": errors,
        "acceptance_standard": POLICY_ID,
        "evidence_audit_affects_acceptance": False,
        "semantic_correctness_evaluated": False,
        "output_modified": False,
    }


def validate_diagnosis_text(text: str, valid_step_ids: Iterable[Any]) -> dict[str, Any]:
    """Require an actual JSON object, without extracting or repairing content."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "passed": False,
            "errors": [f"Invalid JSON: {exc}"],
            "acceptance_standard": POLICY_ID,
            "evidence_audit_affects_acceptance": False,
            "semantic_correctness_evaluated": False,
            "output_modified": False,
        }
    return validate_diagnosis(data, valid_step_ids)
