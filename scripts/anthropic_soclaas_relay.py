#!/usr/bin/env python3
"""Local Anthropic Messages relay for Claude Code -> SoCLaaS Responses API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "input_text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


def _anthropic_to_responses(payload: dict[str, Any], model: str) -> dict[str, Any]:
    input_items: list[dict[str, Any]] = []
    system = payload.get("system")
    if system:
        input_items.append({"role": "system", "content": _text_from_content(system)})

    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        text_parts: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_result":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": str(block.get("tool_use_id", "")),
                    "output": _text_from_content(block.get("content", "")),
                })
            elif kind == "tool_use":
                arguments = block.get("input", {})
                input_items.append({
                    "type": "function_call",
                    "call_id": str(block.get("id", "")),
                    "name": str(block.get("name", "")),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                })
            elif kind in {"thinking", "redacted_thinking"}:
                continue
            elif kind == "image":
                source = block.get("source")
                if isinstance(source, dict) and source.get("type") == "base64":
                    media_type = str(source.get("media_type", "image/png"))
                    data = str(source.get("data", ""))
                    text_parts.append({
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{data}",
                    })
                else:
                    text_parts.append({"type": "input_text", "text": "[image]"})
            else:
                text = block.get("text")
                if text is not None:
                    text_parts.append({"type": "input_text", "text": str(text)})
        if text_parts:
            content_value: Any = (
                text_parts[0]["text"]
                if len(text_parts) == 1 and text_parts[0].get("type") == "input_text"
                else text_parts
            )
            input_items.append({"role": role, "content": content_value})

    request: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "max_output_tokens": min(int(payload.get("max_tokens", 25000)), 25000),
        "stream": False,
    }
    for key in ("temperature", "top_p", "parallel_tool_calls"):
        if key in payload:
            request[key] = payload[key]

    raw_tools = payload.get("tools")
    if isinstance(raw_tools, list) and raw_tools:
        tools: list[dict[str, Any]] = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            tools.append({
                "type": "function",
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "")),
                "parameters": tool.get("input_schema", {"type": "object"}),
            })
        if tools:
            request["tools"] = tools

    choice = payload.get("tool_choice")
    if isinstance(choice, str):
        request["tool_choice"] = {"any": "required"}.get(choice, choice)
    elif isinstance(choice, dict):
        if choice.get("type") == "tool":
            request["tool_choice"] = {
                "type": "function",
                "name": str(choice.get("name", "")),
            }
        elif choice.get("type") in {"auto", "any", "none"}:
            request["tool_choice"] = {
                "any": "required"
            }.get(choice["type"], choice["type"])
    return request


def _responses_to_anthropic(value: dict[str, Any], requested_model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    has_tool = False
    output = value.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                blocks = item.get("content", [])
                if isinstance(blocks, list):
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        text = block.get("text")
                        if isinstance(text, str) and text:
                            content.append({"type": "text", "text": text})
            elif item_type == "function_call":
                has_tool = True
                raw_args = item.get("arguments", "{}")
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    arguments = {}
                content.append({
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex[:20]}"),
                    "name": str(item.get("name", "")),
                    "input": arguments,
                })
    if not content and isinstance(value.get("output_text"), str):
        content.append({"type": "text", "text": value["output_text"]})
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    status = str(value.get("status", "completed"))
    stop_reason = "tool_use" if has_tool else ("max_tokens" if status == "incomplete" else "end_turn")
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n".encode("utf-8")


class _RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        upstream_base: str,
        model: str,
        api_key: str,
        event_log: Path,
        bind_host: str = "127.0.0.1",
        trace_payloads: bool = False,
    ):
        super().__init__((bind_host, 0), _RelayHandler)
        self.upstream_base = upstream_base.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.event_log = event_log
        self.trace_payloads = trace_payloads
        self.lock = threading.Lock()
        self.activity = threading.Condition(self.lock)
        self.active_requests = 0

    def process_request(self, request, client_address):
        # Reserve before starting a worker, avoiding an idle/drain race.
        with self.activity:
            self.active_requests += 1
        try:
            return super().process_request(request, client_address)
        except BaseException:
            with self.activity:
                self.active_requests -= 1
                self.activity.notify_all()
            raise

    def process_request_thread(self, request, client_address):
        try:
            return super().process_request_thread(request, client_address)
        finally:
            with self.activity:
                self.active_requests -= 1
                self.activity.notify_all()

    def wait_for_idle(self, timeout):
        """Wait for already admitted requests; never send or retry a request."""
        with self.activity:
            return self.activity.wait_for(lambda: self.active_requests == 0, timeout)

    def record(self, event: dict[str, Any]) -> None:
        mount = Path(__file__).resolve().parents[1]
        if mount in self.event_log.parents and not mount.is_dir():
            raise OSError('External recording disk unavailable; no replacement path created')
        self.event_log.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _reply_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        server = self.server
        if isinstance(server, _RelayServer) and self.path.rstrip("/").endswith("/models"):
            self._reply_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "claude-sonnet-4-6",
                            "object": "model",
                            "owned_by": "local-soclaas-relay",
                        }
                    ],
                },
            )
            return
        self._reply_json(404, {"error": {"message": "unsupported relay path"}})

    def do_POST(self) -> None:  # noqa: N802
        server = self.server
        if not isinstance(server, _RelayServer):
            self._reply_json(502, {"error": {"message": "relay unavailable"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        started = time.time()
        try:
            request_path = urlsplit(self.path).path
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            if request_path.endswith("/count_tokens"):
                text = json.dumps(payload, ensure_ascii=False)
                self._reply_json(200, {"input_tokens": max(1, len(text) // 4)})
                return
            if not request_path.endswith("/messages"):
                self._reply_json(404, {"error": {"message": "unsupported relay path"}})
                return
            requested_model = str(payload.get("model", "claude-sonnet-4-6"))
            trace_id = uuid.uuid4().hex
            if server.trace_payloads:
                server.record({"event": "incoming_anthropic_request", "trace_id": trace_id,
                               "task_id": getattr(server, "task_id", None),
                               "path": self.path, "payload": payload})
            upstream_payload = _anthropic_to_responses(payload, server.model)
            if server.trace_payloads:
                server.record({"event": "outgoing_soclaas_responses_request", "trace_id": trace_id,
                               "task_id": getattr(server, "task_id", None),
                               "upstream_url": f"{server.upstream_base}/responses",
                               "payload": upstream_payload})
            request = Request(
                f"{server.upstream_base}/responses",
                data=_json_bytes(upstream_payload),
                headers={
                    "Authorization": f"Bearer {server.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            context = _ssl_context()
            open_kwargs = {"timeout": 900}
            if context is not None:
                open_kwargs["context"] = context
            with urlopen(request, **open_kwargs) as response:
                response_body = response.read()
                status = response.status
            upstream_value = json.loads(response_body)
            if not isinstance(upstream_value, dict):
                raise ValueError("SoCLaaS response was not a JSON object")
            if server.trace_payloads:
                server.record({"event": "incoming_soclaas_responses_response", "trace_id": trace_id,
                               "task_id": getattr(server, "task_id", None),
                               "http_status": status, "payload": upstream_value})
            anthropic_value = _responses_to_anthropic(upstream_value, requested_model)
            if server.trace_payloads:
                server.record({"event": "outgoing_anthropic_response", "trace_id": trace_id,
                               "task_id": getattr(server, "task_id", None),
                               "payload": anthropic_value})
            server.record({
                "event": "request_complete",
                "trace_id": trace_id,
                "task_id": getattr(server, "task_id", None),
                "path": self.path,
                "requested_model": requested_model,
                "upstream_model": server.model,
                "http_status": status,
                "elapsed_seconds": round(time.time() - started, 3),
                "input_tokens": anthropic_value["usage"].get("input_tokens", 0),
                "output_tokens": anthropic_value["usage"].get("output_tokens", 0),
                "stop_reason": anthropic_value.get("stop_reason"),
            })
            if payload.get("stream"):
                self._send_stream(anthropic_value)
            else:
                self._reply_json(200, anthropic_value)
        except HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace")
            server.record({
                "event": "upstream_error",
                "path": self.path,
                "http_status": exc.code,
                "elapsed_seconds": round(time.time() - started, 3),
                "error_prefix": body[:400],
            })
            self._reply_json(exc.code, {"error": {"type": "api_error", "message": body[:1000]}})
        except (URLError, TimeoutError) as exc:
            server.record({
                "event": "upstream_transport_error",
                "path": self.path,
                "elapsed_seconds": round(time.time() - started, 3),
                "error": type(exc).__name__,
            })
            self._reply_json(502, {"error": {"type": "api_error", "message": "SoCLaaS transport error"}})
        except Exception as exc:  # pragma: no cover - defensive gateway boundary
            server.record({
                "event": "relay_error",
                "path": self.path,
                "elapsed_seconds": round(time.time() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            })
            self._reply_json(500, {"error": {"type": "api_error", "message": "local relay error"}})

    def _send_stream(self, message: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        message_start = {
            "type": "message_start",
            "message": {
                **message,
                "content": [],
                "stop_reason": None,
                "usage": {
                    "input_tokens": message["usage"]["input_tokens"],
                    "output_tokens": 0,
                },
            },
        }
        self.wfile.write(_sse_event("message_start", message_start))
        self.wfile.flush()
        for index, block in enumerate(message.get("content", [])):
            content_block = dict(block)
            if content_block.get("type") == "text":
                content_block["text"] = ""
            elif content_block.get("type") == "tool_use":
                content_block["input"] = {}
            self.wfile.write(_sse_event("content_block_start", {
                "type": "content_block_start",
                "index": index,
                "content_block": content_block,
            }))
            if block.get("type") == "text":
                delta = {"type": "text_delta", "text": block.get("text", "")}
            else:
                delta = {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False),
                }
            self.wfile.write(_sse_event("content_block_delta", {
                "type": "content_block_delta",
                "index": index,
                "delta": delta,
            }))
            self.wfile.write(_sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": index,
            }))
            self.wfile.flush()
        self.wfile.write(_sse_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": message.get("stop_reason"), "stop_sequence": None},
            "usage": {"output_tokens": message["usage"]["output_tokens"]},
        }))
        self.wfile.write(_sse_event("message_stop", {"type": "message_stop"}))
        self.wfile.flush()
        self.close_connection = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-base", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("a Claude Code command is required after --")
    api_key = os.environ.get("SOCLAAS_API_KEY", "")
    if not api_key:
        parser.error("SOCLAAS_API_KEY is not set")
    server = _RelayServer(args.upstream_base, args.model, api_key, args.event_log)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    child_environment = os.environ.copy()
    child_environment.update({
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_AUTH_TOKEN": "local-relay-token",
        "ANTHROPIC_API_KEY": "local-relay-token",
    })
    try:
        return subprocess.run(command, check=False, env=child_environment).returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
