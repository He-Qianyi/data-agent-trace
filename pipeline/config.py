"""Strict shared configuration. Secrets and silent parameter no-ops rejected."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

STAGES = {
    "collection": "01_collection", "recording": "02_recording_quality",
    "trace_processor": "03_trace_processor", "quality": "04_annotation_quality",
    "diagnosis": "05_diagnosis", "presentation": "06_presentation",
}
WORKSPACE = Path("${PROJECT_ROOT}")
DEFAULT_PROJECT = Path("${PROJECT_ROOT}")
DEFAULTS = {
    "schema": "data-agent-trace-pipeline-v1",
    "stage": "collection",
    "paths": {"project_root": str(DEFAULT_PROJECT), "legacy_scripts": str(WORKSPACE / "scripts"),
              "python": str(WORKSPACE / ".venv-dataagentbench/bin/python"),
              "dataset_root": "${BENCHMARK_ROOT}/DABstep", "output": "",
              "control_root": str(WORKSPACE / ".research-pipeline")},
    "provider": {"base_url": "https://soclaas-api.comp.nus.edu.sg/v1", "model": "qwen3.6:35b",
                 "api_key_env": "SOCLAAS_API_KEY", "env_file": str(WORKSPACE / ".env.soclaas")},
    "limits": {"context_tokens": 110000, "output_tokens": 25000, "task_timeout_seconds": 3600,
               "max_tasks": 10, "concurrency": 1, "wait_seconds": 0, "minimum_free_disk_gib": 8},
    "collection": {"benchmark": "dabstep", "scope": "public", "agents": ["codex"], "task_ids": []},
    "agent_options": {"codex": {"output_tokens": None}, "claude": {}, "kimi": {}, "deepseek": {}},
    "binaries": {"codex": "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                 "claude": "claude",
                 "kimi": str(DEFAULT_PROJECT / "tools/kimi-code-node-2.0.0/node_modules/.bin/kimi"),
                 "deepseek": str(DEFAULT_PROJECT / "tools/deepseek-harness/node_modules/.bin/dsh")},
    "safety": {"pause_markers": [], "automatic_retries": 0, "stop_on_http": [429, 500, 502, 503]},
    "input": {"manifest": "", "package": ""},
    "method": {"entrypoint": "", "sha256": "", "arguments": [], "model_calls": False,
               "approved_new_attempt": False, "max_model_requests": 0, "pause_markers": []},
}


def merge(base, update, prefix=""):
    for key, value in update.items():
        if key not in base:
            raise ValueError("Unknown configuration field: " + prefix + key)
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ValueError("Expected object: " + prefix + key)
            if prefix == "agent_options.":
                allowed = {"output_tokens"}
                if set(value) - allowed:
                    raise ValueError("Unknown per-agent limit")
                base[key].update(value)
            else:
                merge(base[key], value, prefix + key + ".")
        else:
            base[key] = value
    return base


def validate(c):
    if c["schema"] != DEFAULTS["schema"] or c["stage"] not in STAGES:
        raise ValueError("Unknown pipeline schema or stage")
    p = urlsplit(c["provider"]["base_url"])
    if (p.scheme != "https" or p.hostname != "soclaas-api.comp.nus.edu.sg" or p.port not in (None, 443)
            or p.path.rstrip("/") != "/v1" or p.username or p.password or p.query or p.fragment):
        raise ValueError("This provider profile only permits the verified SoCLaaS HTTPS /v1 endpoint")
    if not isinstance(c["provider"]["model"], str) or not re.fullmatch(r"[A-Za-z0-9._:/-]+", c["provider"]["model"]):
        raise ValueError("Nonempty model required")
    if c["provider"]["api_key_env"] != "SOCLAAS_API_KEY":
        raise ValueError("The compatibility backend requires SOCLAAS_API_KEY as the credential name")
    for field in ("context_tokens", "task_timeout_seconds", "max_tasks", "concurrency", "minimum_free_disk_gib"):
        if type(c["limits"][field]) is not int or c["limits"][field] < 1:
            raise ValueError("Positive integer required: limits." + field)
    limit = c["limits"]["output_tokens"]
    if limit is not None and (type(limit) is not int or not 0 < limit < c["limits"]["context_tokens"]):
        raise ValueError("output_tokens must be null or positive and smaller than context_tokens")
    if c["limits"]["concurrency"] != 1:
        raise ValueError("The compatibility collection backend is serial; concurrency must be 1")
    if type(c["limits"]["wait_seconds"]) not in (int, float) or c["limits"]["wait_seconds"] < 0:
        raise ValueError("wait_seconds must be nonnegative")
    if c["safety"]["automatic_retries"] != 0:
        raise ValueError("Automatic retries are not supported; use an independently authorized attempt")
    if c["safety"]["stop_on_http"] != [429, 500, 502, 503]:
        raise ValueError("Required provider failure stop policy cannot be disabled")
    agents = c["collection"]["agents"]
    if not isinstance(agents, list) or not agents or len(agents) != len(set(agents)) or any(a not in c["binaries"] for a in agents):
        raise ValueError("agents must be a unique nonempty list of codex/claude/kimi/deepseek")
    if not isinstance(c["collection"]["task_ids"], list):
        raise ValueError("task_ids must be a list")
    for agent in agents:
        budget = c["agent_options"][agent].get("output_tokens", limit)
        if agent == "codex" and budget is not None:
            raise ValueError("Stock Codex output cap is not verified; codex.output_tokens must remain null")
        if agent != "codex" and (type(budget) is not int or not 0 < budget < c["limits"]["context_tokens"]):
            raise ValueError("A valid output budget is required for " + agent)
        if agent == "claude" and budget > 25000:
            raise ValueError("Frozen Claude relay caps at 25000; larger limits cannot silently take effect")
    if not isinstance(c["method"]["arguments"], list) or any(not isinstance(x, str) for x in c["method"]["arguments"]):
        raise ValueError("method.arguments must be a list of strings, never a shell command")
    for value in c["method"]["arguments"]:
        if re.search(r"api[_-]?key|authorization|bearer\s|--(?:auth-)?token", value, re.I):
            raise ValueError("Credentials must not be passed or stored in method.arguments")
    for key in ("model_calls", "approved_new_attempt"):
        if type(c["method"][key]) is not bool:
            raise ValueError("method." + key + " must be boolean")
    if type(c["method"]["max_model_requests"]) is not int or c["method"]["max_model_requests"] < 0:
        raise ValueError("method.max_model_requests must be a nonnegative integer")
    for name in ("pause_markers",):
        if not isinstance(c["safety"][name], list) or any(not isinstance(x, str) for x in c["safety"][name]):
            raise ValueError("pause_markers must be path strings")
    return c


def load_profile(path, seen=None):
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError("Circular profile inheritance")
    seen.add(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    parent = raw.pop("extends", None)
    base = load_profile(path.parent / parent, seen) if parent else copy.deepcopy(DEFAULTS)
    return merge(base, raw)


def load(path, overrides=()):
    c = load_profile(path)
    for expression in overrides:
        if "=" not in expression:
            raise ValueError("Override requires dotted.field=value")
        name, text = expression.split("=", 1)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            value = text
        patch = value
        for component in reversed(name.split(".")):
            patch = {component: patch}
        merge(c, patch)
    project = Path(c["paths"]["project_root"]).expanduser().resolve()
    c["paths"]["project_root"] = str(project)
    for key in ("legacy_scripts", "dataset_root", "python", "control_root"):
        p = Path(c["paths"][key]).expanduser()
        c["paths"][key] = str(p if p.is_absolute() else project / p)
    if c["paths"]["output"]:
        p = Path(c["paths"]["output"]).expanduser()
        c["paths"]["output"] = str(p if p.is_absolute() else project / p)
    return validate(c)
