"""Explicit compatibility launcher; never silently changes frozen semantics."""
import subprocess
from pathlib import Path
from .common import digest, write, require_unpaused, fresh_output
from .config import STAGES


def plan(config):
    method = config["method"]
    script = Path(method["entrypoint"])
    if not script.is_file() or len(method["sha256"]) != 64 or digest(script) != method["sha256"]:
        raise ValueError("Frozen method entrypoint requires an existing path and its matching sha256")
    if not method["approved_new_attempt"]:
        raise ValueError("Frozen method profile must explicitly declare an independently approved new attempt")
    if method["model_calls"] and method["max_model_requests"] < 1:
        raise ValueError("Model method requires an explicit positive request budget")
    arguments = method["arguments"]
    if not any("{output}" in value for value in arguments):
        raise ValueError("Method must accept a new {output} argument; historical output roots cannot be reused")
    if method["model_calls"] and not any("{max_model_requests}" in value for value in arguments):
        raise ValueError("Pinned model method must expose and enforce its request budget argument")
    # These parameters are not magically injected into arbitrary historical
    # scripts. If selected, they must be explicitly bound in the profile.
    return {"operation": "pinned_external_method", "entrypoint": str(script), "sha256": method["sha256"],
            "arguments": arguments, "model_calls": method["model_calls"],
            "request_budget_declaration": method["max_model_requests"],
            "request_budget_enforced_by": "pinned method, not this generic launcher",
            "semantics_rewritten": False}


def execute(config, selection):
    output = fresh_output(config, STAGES[config["stage"]])
    require_unpaused(config["safety"]["pause_markers"] + config["method"]["pause_markers"])
    if digest(selection["entrypoint"]) != selection["sha256"]:
        raise RuntimeError("Frozen entrypoint changed")
    output.mkdir(parents=True)
    values = {"output": str(output / "method-output"), "model": config["provider"]["model"],
              "context_tokens": str(config["limits"]["context_tokens"]),
              "output_tokens": str(config["limits"]["output_tokens"]),
              "max_model_requests": str(config["method"]["max_model_requests"])}
    args = [value.format_map(values) for value in selection["arguments"]]
    command = [config["paths"]["python"], "-B", selection["entrypoint"], *args]
    write(output / "invocation.json", {"command": command, "selection": selection,
                                      "profile": config, "quality_certified": False})
    with (output / "stdout.log").open("w") as out, (output / "stderr.log").open("w") as err:
        try:
            result = subprocess.run(command, stdout=out, stderr=err, check=False,
                                    timeout=config["limits"]["task_timeout_seconds"],
                                    cwd=Path(selection["entrypoint"]).parent)
        except subprocess.TimeoutExpired:
            write(output / "result.json", {"status": "needs_review", "reason": "method_timeout", "automatic_retries": 0})
            return {"output": str(output), "status": "needs_review", "quality_certified": False}
    write(output / "result.json", {"exit_code": result.returncode, "semantic_quality_certified": False,
                                   "automatic_retries": 0})
    return {"output": str(output), "exit_code": result.returncode, "quality_certified": False}
