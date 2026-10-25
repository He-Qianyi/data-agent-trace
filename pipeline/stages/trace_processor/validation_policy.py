"""Acceptance overlay: retain unknown C fields without changing native outputs.

The frozen extraction, prompts, required fields and known-field constraints stay
unchanged. Extra fields are retained as uninterpreted model output, not promoted
to canonical facts or required semantics.
"""
import copy

POLICY_ID = "trace-processor-allow-extra-fields-v1-20261019"


def allow_extra_fields(frozen_operation_schema):
    """Relax only additionalProperties in a separate operation-schema copy."""
    schema = copy.deepcopy(frozen_operation_schema)

    def visit(node):
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                node["additionalProperties"] = True
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema
