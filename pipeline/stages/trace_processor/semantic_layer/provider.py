"""One audited request at a time. No retries, protocol fallback or secret logging."""
import importlib
import json
import os
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .evidence import encode, text_hash


class ProviderFailure(RuntimeError):
    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__(metadata.get("reason", "provider_failure"))


def helpers():
    scripts = str(Path(__file__).resolve().parents[4]/"scripts")
    if scripts not in sys.path:
        sys.path.insert(0,scripts)
    return importlib.import_module("trace_processor_v2"), importlib.import_module("probe_trace_processor_provider")


class Client:
    def __init__(self, model, env_file, output_tokens=18000, timeout=600, wire_protocol="responses",
                 thinking_token_budget=None):
        if thinking_token_budget is not None:
            if wire_protocol != "chat":
                raise ValueError("Independent thinking budget is supported only on the verified chat path")
            if model != "x-test-2":
                raise ValueError("Independent thinking budget has only been verified for x-test-2")
            if (isinstance(thinking_token_budget, bool) or not isinstance(thinking_token_budget, int)
                    or thinking_token_budget < 0 or thinking_token_budget >= output_tokens):
                raise ValueError("Thinking budget must be a nonnegative integer smaller than total output tokens")
        core, _ = helpers()
        # Existing credential loader consumes the file in memory; never emit its contents.
        self.base, self.key = core.provider_config(Path(env_file))
        p = urlsplit(self.base)
        if p.scheme != "https" or p.hostname != "soclaas-api.comp.nus.edu.sg" or p.path.rstrip("/") != "/v1" or p.username or p.password or p.query or p.fragment:
            raise ValueError("Expected the existing verified SoCLaaS HTTPS /v1 provider")
        if not self.key:
            raise ValueError("Provider credential unavailable; no request sent")
        self.model,self.output_tokens,self.timeout = model,output_tokens,timeout
        if wire_protocol not in ("responses","chat"):
            raise ValueError("Unsupported wire protocol")
        self.wire_protocol=wire_protocol
        self.thinking_token_budget=thinking_token_budget

    def ask(self, prompt, data, schema, directory):
        core, probe = helpers()
        directory = Path(directory)
        directory.mkdir(parents=True,exist_ok=False)
        payload = {"model":self.model,"input":[{"role":"system","content":prompt},
                   {"role":"user","content":"UNTRUSTED ANNOTATION INPUT DATA:\n"+encode(data)+"\nApply the system contract and return JSON only."}],
                   "max_output_tokens":self.output_tokens,"temperature":0,
                   "text":{"format":{"type":"json_schema","name":"data_agent_semantic_annotation","schema":schema,"strict":False}}}
        protocol=getattr(self,"wire_protocol","responses")
        if protocol=="chat":
            # The documented native path avoids the Responses shim's retained-conversation cache.
            # JSON mode constrains syntax; the unchanged schema is supplied explicitly and checked locally.
            payload={"model":self.model,"messages":[
                {"role":"system","content":prompt+"\nOUTPUT JSON SCHEMA (structural contract):\n"+encode(schema)},
                {"role":"user","content":payload["input"][1]["content"]}],
                "max_tokens":self.output_tokens,"temperature":0,"response_format":{"type":"json_object"}}
            if self.thinking_token_budget is not None:
                payload["thinking_token_budget"] = self.thinking_token_budget
        # Guard against credential echo in retained request artifacts.
        payload = core.redact_credentials(payload,self.key)
        request_text = encode(payload)
        (directory/"request.json").write_text(request_text+"\n",encoding="utf-8")
        endpoint="/chat/completions" if protocol=="chat" else "/responses"
        request = urllib.request.Request(self.base.rstrip("/")+endpoint,data=request_text.encode("utf-8"),method="POST",
                                         headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json"})
        ca_file = os.environ.get("SSL_CERT_FILE")
        if not ca_file:
            try:
                import certifi
                ca_file=certifi.where()
            except ImportError:
                pass
        metadata={"request_sha256":text_hash(request_text),"requested_model":self.model,"automatic_retries":0,"http_attempts":1,"wire_protocol":protocol}
        if self.thinking_token_budget is not None:
            metadata.update(max_tokens_requested=self.output_tokens,
                            thinking_token_budget_requested=self.thinking_token_budget)
        started=time.monotonic()
        try:
            with urllib.request.urlopen(request,timeout=self.timeout,context=ssl.create_default_context(cafile=ca_file)) as response:
                body=json.load(response)
                metadata["http_status"]=response.status
        except urllib.error.HTTPError as exc:
            metadata.update(reason="provider_http_failure",http_status=exc.code,**probe.safe_error_metadata(exc))
            self.fail(directory,metadata,started)
        except (OSError,ValueError) as exc:
            metadata.update(reason="provider_transport_or_decode_failure",error_type=type(exc).__name__)
            self.fail(directory,metadata,started)
        body=core.redact_credentials(body,self.key)
        response_text=encode(body)
        (directory/"response.json").write_text(response_text+"\n",encoding="utf-8")
        metadata.update(returned_model=body.get("model"),response_id=body.get("id"),usage=body.get("usage"),
                        response_sha256=text_hash(response_text),elapsed_seconds=round(time.monotonic()-started,3))
        try:
            parsed=core.parse_annotation_response(core.normalize_native_chat(body) if protocol=="chat" else body,self.output_tokens)
        except (ValueError,RuntimeError) as exc:
            metadata.update(reason="provider_annotation_output_unusable",error_type=type(exc).__name__)
            self.fail(directory,metadata,started)
        (directory/"receipt.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n")
        return parsed,metadata

    @staticmethod
    def fail(directory,metadata,started):
        metadata["elapsed_seconds"]=round(time.monotonic()-started,3)
        (directory/"receipt.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n")
        raise ProviderFailure(metadata)
