"""Explicit Stage C settings for new calls, separate from frozen historical attempts."""
from dataclasses import dataclass
import json
from pathlib import Path

from .provider import Client


PROJECT = Path(__file__).resolve().parents[4]
DEFAULT_PROFILE = PROJECT / "configs/03_trace_processor/stage-c-runtime.json"


@dataclass(frozen=True)
class StageCRuntime:
    values: dict
    profile_path: Path

    @classmethod
    def load(cls, profile_path=DEFAULT_PROFILE):
        path = Path(profile_path).resolve()
        values = json.loads(path.read_text())
        if values.get("schema_version") != "stage-c-runtime-v1" or values.get("stage") != "C":
            raise ValueError("Expected a Stage C runtime profile")
        if values.get("model") != "x-test-2" or values.get("wire_protocol") != "chat":
            raise ValueError("Thinking-budget profile is verified only for x-test-2 chat")
        total, thinking = values["max_tokens"], values["thinking_token_budget"]
        if (type(total) is not int or type(thinking) is not int or not 0 <= thinking < total):
            raise ValueError("Invalid independent thinking/total output budgets")
        if values.get("temperature") != 0 or values.get("response_format") != {"type": "json_object"}:
            raise ValueError("Unexpected Stage C sampling or format change")
        if any(values.get(k) != 0 for k in ("automatic_retries", "repair_calls", "review_calls")):
            raise ValueError("Stage C profile does not enable retries, repairs or model review")
        if any(type(values.get(k)) is not int or values[k] <= 0 for k in (
                "timeout_seconds", "concurrency", "max_units_per_packet", "max_packet_bytes")):
            raise ValueError("Invalid Stage C execution or packet limits")
        for name in ("prompt_path", "schema_path"):
            if not (PROJECT / values[name]).is_file():
                raise ValueError("Missing Stage C contract")
        return cls(values, path)

    @property
    def final_output_headroom(self):
        return self.values["max_tokens"] - self.values["thinking_token_budget"]

    def client(self, env_file=PROJECT / ".env.soclaas"):
        """New Stage C runners use this instead of a historical hardcoded Client call."""
        return Client(self.values["model"], env_file,
                      output_tokens=self.values["max_tokens"],
                      timeout=self.values["timeout_seconds"],
                      wire_protocol=self.values["wire_protocol"],
                      thinking_token_budget=self.values["thinking_token_budget"])


if __name__ == "__main__":
    runtime = StageCRuntime.load()
    print(json.dumps({"profile": str(runtime.profile_path),
                      "settings": runtime.values,
                      "final_output_headroom_tokens": runtime.final_output_headroom},
                     ensure_ascii=False, indent=2))
