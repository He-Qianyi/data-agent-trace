"""Recording shapes, not benchmark or task identities, select these adapters."""
import json
from .sources import child


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(x.get("text", "") for x in value if isinstance(x, dict) and isinstance(x.get("text"), str))
    return ""


class Adapter:
    name = None
    level = None

    def __init__(self, sources):
        self.sources = sources
        self.exchanges = []
        self.calls = {}
        self.receipts = {}
        self.accepted = set()
        self.native_internal = []
        self.final_messages = []
        self.warnings = []
        self._receipt_keys = set()

    def call(self, block, ref, exchange=None, accepted=False, kind="model_request_replay"):
        cid = block.get("call_id", block.get("id"))
        if not cid:
            raise ValueError("Tool call has no identity")
        raw = block.get("input") if block.get("type") == "tool_use" else block.get("arguments", block.get("input"))
        args = raw
        parse_error = None
        if isinstance(raw, str) and block.get("type") != "custom_tool_call":
            try:
                args = json.loads(raw)
            except ValueError:
                args, parse_error = None, "invalid_json_arguments"
        value = {"tool_call_id": cid, "tool_name": block["name"], "arguments_raw": raw,
                 "arguments": args, "arguments_parse_error": parse_error, "source_ref": ref,
                 "arguments_ref": child(ref, "/input" if block.get("type") in ("tool_use", "custom_tool_call") else "/arguments"),
                 "model_exchange_ref": exchange}
        old = self.calls.get(cid)
        different = old and (old["tool_name"] != value["tool_name"] or old["arguments"] != args)
        if old and exchange is not None and old["model_exchange_ref"] not in (None, exchange):
            raise ValueError("Duplicate tool identity in different model responses: " + cid)
        if accepted and old:
            variants = old.setdefault("submission_variants", [])
            if not any(v["kind"] == kind and v["tool_name"] == value["tool_name"] and v["arguments_raw"] == raw for v in variants):
                variants.append({**value, "kind": kind})
        if different:
            if not accepted:
                raise ValueError("Conflicting model-generated call bodies: " + cid)
        elif old and old["arguments_raw"] != raw:
            variants = old.setdefault("serialization_variants", [])
            if not any(v["arguments_raw"] == raw for v in variants):
                variants.append({"arguments_raw": raw, "arguments_ref": value["arguments_ref"],
                                 "basis": "equal_decoded_arguments_different_serialization"})
        if old is None or (exchange is not None and old["model_exchange_ref"] is None):
            self.calls[cid] = value
        if accepted:
            self.accepted.add(cid)

    def receipt(self, cid, payload, ref, kind, metadata=None, metadata_ref=None):
        if not cid:
            raise ValueError("Result has no call identity")
        key = (cid, kind, canonical(payload), canonical(metadata))
        if key in self._receipt_keys:
            return
        self._receipt_keys.add(key)
        r = {"kind": kind, "payload": payload, "source_ref": ref}
        if metadata is not None:
            r.update(metadata=metadata, metadata_ref=metadata_ref)
        self.receipts.setdefault(cid, []).append(r)

    def exchange(self, row, ref, request_key, response_key, output_key):
        xid = f"X{len(self.exchanges) + 1:04d}"
        res = row[response_key]
        self.exchanges.append({"exchange_id": xid, "request_ref": child(ref, "/" + request_key),
                               "response_ref": child(ref, "/" + response_key), "record_ref": ref,
                               "response_id_as_recorded": res.get("id", res.get("response_id")),
                               "tool_call_ids": []})
        for i, block in enumerate(res.get(output_key, [])):
            br = child(ref, f"/{response_key}/{output_key}/{i}")
            if block.get("type") in ("tool_use", "function_call", "custom_tool_call"):
                self.call(block, br, xid)
                self.exchanges[-1]["tool_call_ids"].append(block.get("call_id", block.get("id")))
            if block.get("type") == "message" and block.get("role") == "assistant":
                self.final_messages.append({"exchange_id": xid, "content": block.get("content"), "source_ref": br})
        return xid

    def transcript(self, rows):
        pass


class AnthropicRelayAdapter(Adapter):
    name = "anthropic-four-surface-relay-v1"
    level = "actual_request_response"

    @staticmethod
    def matches(row):
        return all(k in row for k in ("incoming_anthropic_request", "outgoing_anthropic_response",
                                     "outgoing_soclaas_responses_request", "incoming_soclaas_responses_response"))

    def ingest(self, row, ref):
        xid = self.exchange(row, ref, "incoming_anthropic_request", "outgoing_anthropic_response", "content")
        for mi, msg in enumerate(row["incoming_anthropic_request"]["messages"]):
            blocks = msg.get("content", [])
            for bi, b in enumerate(blocks if isinstance(blocks, list) else []):
                br = child(ref, f"/incoming_anthropic_request/messages/{mi}/content/{bi}")
                if b.get("type") == "tool_use":
                    self.call(b, br, accepted=True)
                if b.get("type") == "tool_result":
                    self.receipt(b["tool_use_id"], b, br, "model_request_replay")
        response = row["outgoing_anthropic_response"]
        if response.get("stop_reason") == "end_turn":
            self.final_messages.append({"exchange_id": xid, "content": response["content"],
                                        "source_ref": child(ref, "/outgoing_anthropic_response/content")})

    def transcript(self, rows):
        for e, ref in rows:
            blocks = e.get("message", {}).get("content", [])
            for i, b in enumerate(blocks if isinstance(blocks, list) else []):
                br = child(ref, f"/message/content/{i}")
                if e.get("type") == "assistant" and b.get("type") == "tool_use":
                    self.call(b, br, accepted=True, kind="framework_submission")
                if e.get("type") == "user" and b.get("type") == "tool_result":
                    # tool_use_result belongs to the containing event; it is not fabricated stdout.
                    meta = e.get("tool_use_result") if len(blocks) == 1 else None
                    self.receipt(b["tool_use_id"], b, br, "framework_tool_result", meta,
                                 child(ref, "/tool_use_result") if meta is not None else None)


class ResponsesAdapter(Adapter):
    def ingest(self, row, ref):
        output_key = "output_items" if self.name == "codex-native-serialized-v1" else "output"
        self.exchange(row, ref, "model_input", "model_output", output_key)
        inputs = row["model_input"].get("input", [])
        for i, b in enumerate(inputs if isinstance(inputs, list) else []):
            br = child(ref, f"/model_input/input/{i}")
            if b.get("type") in ("function_call", "custom_tool_call"):
                self.call(b, br, accepted=True)
            if b.get("type") in ("function_call_output", "custom_tool_call_output"):
                self.receipt(b["call_id"], b, br, "model_request_replay")


class CodexNativeAdapter(ResponsesAdapter):
    name = "codex-native-serialized-v1"
    level = "native_serialized_request_response"

    @staticmethod
    def matches(row):
        return row.get("native_capture_type") == "codex_rollout_trace"

    def native_tools(self, rows):
        seen = set()
        for e, ref in rows:
            if e.get("schema") != "codex-native-tool-execution-v1":
                raise ValueError("Unsupported native tool ledger schema")
            cid = e.get("model_visible_call_id")
            if not cid:
                self.native_internal.append({"native_call_id": e["tool_call_id"], "source_ref": ref})
                continue
            if cid in seen:
                raise ValueError("Duplicate native tool receipt: " + cid)
            seen.add(cid)
            invocation = e["invocation"]
            c = self.calls.get(cid)
            if not c or invocation["tool_name"] != c["tool_name"]:
                raise ValueError("Native invocation has no matching model call: " + cid)
            payload = invocation["payload"]
            if payload.get("arguments", payload.get("input")) != c["arguments_raw"]:
                raise ValueError("Native invocation argument mismatch: " + cid)
            self.accepted.add(cid)
            self.receipt(cid, e["result"], child(ref, "/result"), "native_tool_result",
                         {"status": e["status"], "native_call_id": e["tool_call_id"]}, ref)


class DirectResponsesAdapter(ResponsesAdapter):
    name = "direct-responses-serialized-v1"
    level = "actual_serialized_request_response"

    @staticmethod
    def matches(row):
        return row.get("native_capture_type") == "direct_serialized_fetch"

    def transcript(self, rows):
        for e, ref in rows:
            if e.get("role") == "assistant":
                for i, c in enumerate(e.get("tool_calls", [])):
                    # Native OpenAI chat tool-call shape differs from Responses call shape.
                    cid = c["id"]
                    old = self.calls.get(cid)
                    try:
                        decoded = json.loads(c["function"]["arguments"])
                    except ValueError:
                        decoded = None
                    if not old or old["tool_name"] != c["function"]["name"] or old["arguments"] != decoded:
                        raise ValueError("Transcript call differs from recorded response: " + cid)
                    if old["arguments_raw"] != c["function"]["arguments"]:
                        old.setdefault("serialization_variants", []).append({
                            "arguments_raw": c["function"]["arguments"],
                            "arguments_ref": child(ref, f"/tool_calls/{i}/function/arguments"),
                            "basis": "equal_decoded_arguments_different_serialization"})
                    self.accepted.add(cid)
            if e.get("role") == "tool":
                self.receipt(e["tool_call_id"], e, ref, "framework_tool_result")


ADAPTERS = (AnthropicRelayAdapter, CodexNativeAdapter, DirectResponsesAdapter)


def select(first, sources):
    matches = [a for a in ADAPTERS if a.matches(first)]
    if len(matches) != 1:
        raise ValueError("Unsupported or ambiguous recording format")
    return matches[0](sources)
