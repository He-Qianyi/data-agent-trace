"""Output shape contract, composed with the frozen fact and operation contracts."""
import copy


def schema(fact_schema, operation_schema):
    fact_schema = copy.deepcopy(fact_schema)
    fact_schema.pop("$id", None)
    fact_schema.pop("$schema", None)
    definitions = fact_schema.pop("$defs")
    unit = copy.deepcopy(operation_schema["properties"]["units"]["items"])
    unit["required"] += ["step_id", "model_exchange_ref", "native_origin", "validation"]
    unit["properties"].update(step_id={"type": "string"}, model_exchange_ref={"type": "string"},
        native_origin={"type": "object", "required": ["packet", "bundle_path", "sha256", "json_pointer"]},
        validation={"type": "object", "required": ["status", "issues", "semantic_correctness_certified"],
                    "properties": {"status": {"enum": ["mechanically_valid", "with_warnings"]},
                                   "issues": {"type": "array"}, "semantic_correctness_certified": {"const": False}}})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": definitions,
        "title": "Data Agent normalized trace v1: facts plus qualified semantic overlay",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "facts", "annotations", "evidence_catalog", "citation_index", "analysis_index", "provenance", "quality"],
        "properties": {"schema_version": {"const": "data-agent-normalized-trace-v1"}, "facts": fact_schema,
            "annotations": {"type": "object", "additionalProperties": False, "required": ["task", "units"],
                "properties": {"task": {"type": "object", "required": ["task_summary", "requirements", "coverage", "ambiguities", "validation"]},
                               "units": {"type": "array", "items": unit}}},
            "evidence_catalog": {"type": "array", "items": {"type": "object", "required": ["evidence_id", "role", "text", "char_start", "char_end", "original_source_ref"]}},
            "citation_index": {"type": "array", "items": {"type": "object", "required": ["annotation_pointer", "evidence_id", "quote", "status", "issues", "eligible_as_literal_evidence", "block_char_spans", "semantic_entailment_verified"],
                "properties": {"status": {"enum": ["exact_unique", "exact_multiple", "unresolved"]}, "eligible_as_literal_evidence": {"type": "boolean"}, "semantic_entailment_verified": {"const": False}}}},
            "analysis_index": {"type": "array", "items": {"type": "object", "required": ["step_id", "fact_pointer", "model_exchange_ref", "tool_call_id", "unit_ids", "operation_pointers", "artifact_refs", "order_scope"]}},
            "provenance": {"type": "object", "required": ["fact_file", "source_files", "annotation_inputs", "packets", "source_cohort", "frozen_contracts"]},
            "quality": {"type": "object", "required": ["semantic_correctness_certified", "diagnosis_accuracy_evaluated", "quote_repair_performed", "fact_mutation_performed", "citation_counts", "units_retained", "units_mechanically_valid"],
                "properties": {k: {"const": False} for k in ("semantic_correctness_certified", "diagnosis_accuracy_evaluated", "quote_repair_performed", "fact_mutation_performed")}}}}
