"""Script-built, hash-bound LLM views; evaluator/final snapshots never enter them."""
import hashlib
import json
from pathlib import Path
import re

from ..fact_layer.sources import pointer, Sources


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def chunks(text, max_bytes):
    """Lossless non-overlapping character ranges bounded by actual UTF-8 bytes."""
    start = 0
    while start < len(text):
        end, count = start, 0
        while end < len(text):
            size = len(text[end].encode("utf-8"))
            if count + size > max_bytes:
                break
            end, count = end + 1, count + size
        if end == start:
            raise ValueError("Byte budget too small for one character")
        if end < len(text):
            newline = text.rfind("\n", start + (end-start)//2, end)
            if newline >= start:
                end = newline + 1
        yield start, end, text[start:end]
        start = end


def requirement_input(trace):
    text = trace["task"].get("instruction")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Task requirement stage needs an original full instruction")
    evidence = []
    # Split along physical prompt lines, then bounded length; this is indexing, not interpretation.
    start = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            for left, right, part in chunks(line, 4000):
                evidence.append({"evidence_id": f"T{len(evidence)+1:04d}", "role": "original_task_instruction", "text": part,
                                 "fact_pointer": "/task/instruction", "char_start": start+left, "char_end": start+right,
                                 "original_source_ref": trace["task"].get("instruction_ref")})
        start += len(line)
    return {"task_prompt_sha256": text_hash(text), "evidence": evidence}


def _field(key):
    return key.replace("~", "~0").replace("/", "~1")


def operation_input(trace, fragment_bytes=14000):
    """Include current assistant content, arguments, returns and flags. No history replay."""
    evidence, units = [], []
    exchange_by_id = {x["exchange_id"]: x for x in trace["model_exchanges"]}
    sources = None
    def add(text, role, step, fact_pointer=None, original_ref=None, representation="text"):
        records = []
        for start, end, part in chunks(text, fragment_bytes):
            r = {"evidence_id": f"E{len(evidence)+1:05d}", "step_id": step["step_id"], "role": role, "text": part,
                 "fact_pointer": fact_pointer, "char_start": start, "char_end": end,
                 "representation": representation, "original_source_ref": original_ref}
            evidence.append(r); records.append(r)
        return records
    assistant_cache = {}
    for i, step in enumerate(trace["steps"]):
        action, obs = step["action"], step["observation"]
        parts = []
        args = action.get("arguments")
        if isinstance(args, dict) and args:
            for key, value in args.items():
                text = value if isinstance(value, str) else encode(value)
                parts.extend(add(text or "\"\"", "argument:"+key, step, f"/steps/{i}/action/arguments/{_field(key)}",
                                 action["arguments_ref"], "text" if isinstance(value, str) and value else "canonical_json"))
        else:
            value = action["arguments_raw"]
            parts.extend(add(value if isinstance(value,str) and value else encode(value), "raw_arguments", step,
                             f"/steps/{i}/action/arguments_raw", action["arguments_ref"], "text" if isinstance(value,str) and value else "canonical_json"))
        if obs.get("text"):
            parts.extend(add(obs["text"], "tool_observation", step, f"/steps/{i}/observation/text",
                             obs["variants"][0]["source_ref"]))
        signal = {k: obs.get(k) for k in ("availability","tool_status_as_recorded","is_error_as_recorded","exit_code_as_recorded","truncation")}
        signal["nontext_payload_is_not_shown"] = not bool(obs.get("text"))
        # Derived display metadata is checked against the frozen fact trace during validation.
        parts.extend(add(encode(signal), "recording_signals", step, None, None, "derived_recording_signals"))
        # Current response ONLY; never send entire system/request histories to the annotator.
        xid = step["model_exchange_ref"]
        if xid not in assistant_cache:
            if sources is None:
                sources = Sources.from_entries(trace["sources"], verify_hashes=False)
            ref = exchange_by_id[xid]["response_ref"]
            response = sources.resolve(ref)
            texts = []
            for block in response.get("content",response.get("output_items",response.get("output",[]))):
                if block.get("type") == "text" and isinstance(block.get("text"),str):
                    texts.append(block["text"])
                if block.get("type") == "message":
                    for c in block.get("content",[]):
                        if c.get("type") in ("output_text","text") and isinstance(c.get("text"),str):
                            texts.append(c["text"])
            assistant_cache[xid] = ("\n".join(texts), ref)
        assistant_text, assistant_ref = assistant_cache[xid]
        context = []
        if assistant_text:
            # All content is indexed. Context chunks need not be repeated in every operation unit.
            context = add(assistant_text, "assistant_claimed_intent", step, None, assistant_ref, "current_assistant_text")
        groups, group, size = [], [], 0
        for part in parts:
            part_size = len(encode(part).encode("utf-8"))
            if group and size + part_size > 24000:
                groups.append(group); group, size = [], 0
            group.append(part); size += part_size
        if group:
            groups.append(group)
        for group in groups:
            units.append({"unit_id": f"U{len(units)+1:05d}", "step_id": step["step_id"], "tool_name": action["tool_name"],
                          "model_exchange_ref": xid, "evidence_ids": [part["evidence_id"] for part in group],
                          "context_evidence_ids": [c["evidence_id"] for c in context]})
    return {"evidence": evidence, "units": units,
            "excluded_inputs": ["evaluation", "deliverables snapshots", "final_response", "task.metadata evaluator/gold fields", "replayed model history"]}


def batches(view, requirements, max_bytes=70000, max_units=16):
    by_id = {e["evidence_id"]: e for e in view["evidence"]}
    batch = []
    def make(items):
        ids = list(dict.fromkeys(eid for u in items for eid in u["evidence_ids"] + u["context_evidence_ids"]))
        return {"requirements": requirements, "target_units": items,
                "evidence": [{k:v for k,v in by_id[eid].items() if k in ("evidence_id","step_id","role","text")} for eid in ids]}
    for unit in view["units"]:
        candidate = make(batch + [unit])
        if batch and (len(encode(candidate).encode("utf-8")) > max_bytes or len(batch) >= max_units):
            yield make(batch)
            batch = []
        batch.append(unit)
        if len(encode(make(batch)).encode("utf-8")) > max_bytes:
            raise ValueError("One semantic unit plus requirements exceeds the frozen packet budget")
    if batch:
        yield make(batch)


def validate_evidence_catalog(trace, evidence):
    for e in evidence:
        if e.get("fact_pointer") is not None:
            value = pointer(trace, e["fact_pointer"])
            text = value if e.get("representation", "text") == "text" else encode(value)
            if text[e["char_start"]:e["char_end"]] != e["text"]:
                raise ValueError("Evidence differs from frozen fact field: " + e["evidence_id"])
