# Run status and safe resume

These features apply to **new portable Trace Processor and Trace Diagnosis runs**. They do not adopt historical experiment directories or modify frozen model outputs. Diagnosis still uses only the [CodeTracer-compatible acceptance policy](acceptance.md).

## Understand the result

Both commands print a JSON summary and return a meaningful process exit code. The same status is saved in `run_summary.json`; trace details and counters are in `progress.json` and `results/<record_id>/result.json` for diagnosis.

| Exit code | Meaning | Typical next action |
| --- | --- | --- |
| 0 | Processor completed, or all diagnosis traces passed CodeTracer-compatible structural acceptance | Inspect the outputs; this is not a semantic-accuracy claim. |
| 2 | Invalid inputs, changed run identity or unreadable/changed checkpoint | Correct the input; use a new output directory for changed data/model settings. |
| 3 | Request, model-round or tool budget exhausted | Explicitly resume with an appropriate budget. |
| 4 | Provider failure, incomplete prior request or local execution failure | Read the saved reason and attempt records; do not assume a diagnosis exists. |
| 5 | Invalid JSON, structural rejection, truncated output or unusable annotation response | Inspect the native output. A completed but unusable response is retained, not silently regenerated. |
| 6 | Pause marker or keyboard interruption | Remove `pause.requested` when ready, then resume. |
| 7 | Another process owns the run | Wait for that process; no concurrent request is sent. |

The summary distinguishes `provider_failed`, `incomplete_call` and `execution_failed` even though they share exit code 4. A missing credential, invalid configuration or other setup issue is reported without printing secret contents.

## Resume a processor run

Use the same input facts, model settings and output directory:

```bash
python -m reproduction.processor \
  --facts /path/to/facts \
  --output reproduction_output/processor \
  --env-file /path/to/local-provider.env \
  --max-requests 10 --allow-network --resume
```

`--max-requests` caps **new request attempts in this invocation**. Successful cached calls do not consume it. Each invocation records its budget separately. Existing successful annotation calls are reused, including shared task requirements. If merging was interrupted, partial derived output is retained under `.resume/derived/` and rebuilt from saved responses. Resuming a completed, intact final bundle sends no model request.

## Resume a diagnosis run

```bash
python -m reproduction.diagnose \
  --bundle reproduction_output/processor/final \
  --output reproduction_output/diagnosis \
  --env-file /path/to/local-provider.env --allow-network --resume
```

The adapter reconstructs the message/tool prefix from saved responses and reexecutes read-only evidence retrieval to rebuild context. It requests only the missing tail. Previously returned responses, native request/response/receipt files and failed attempts are retained. Resuming an already completed diagnosis reuses its calls without initializing a provider client.

After round/tool budget exhaustion, you may supply a config with larger `max_model_rounds_per_trace` or `max_tool_calls_per_trace` using `--config`. These limits apply to the **whole reconstructed trace**, including its saved prefix. The original plan is preserved; the new invocation's plan is recorded separately. Model, prompt, tool contract, input bytes or implementation changes require a new output directory.

This explicit portable continuation is not relabelled as any historical paired experiment.

## Incomplete requests are not automatically resent

If a call was interrupted after a complete native request, response and successful receipt were saved, the adapter can recover that result and finish its checkpoint. If those records are incomplete or unusable, normal `--resume` stops with `incomplete_call` before resending it.

After inspecting the retained attempt, an explicit new attempt can be requested:

```bash
python -m reproduction.diagnose \
  --bundle reproduction_output/processor/final \
  --output reproduction_output/diagnosis \
  --env-file /path/to/local-provider.env \
  --allow-network --resume --retry-incomplete-calls
```

The same option is available on the processor. It preserves the earlier attempt and creates a new attempt directory. A provider may already have processed a request whose response was not fully saved locally; an explicit reattempt may therefore incur another charge. There is no automatic retry, provider fallback, output repair or claim of exactly-once remote execution.

A completed invalid/truncated response remains a completed call record. `--resume` does not regenerate it; changing model settings or intentionally rerunning it requires a new run.

## What is stored

| Path within a new run | Purpose |
| --- | --- |
| `.resume/run.json` | Identity of input bytes, model settings, prompts and implementation. No credential file or API key is stored. |
| `.resume/run.lock` | Exclusive run ownership on POSIX platforms. |
| `.resume/calls/…/attempt-…/` | Retained native records, returned values and atomic completion checks. Failed attempts remain available. |
| `.resume/sessions/` and `.resume/session_plans/` | Per-invocation reuse counters, retry choice and resource limits. |
| `results/<record_id>/calls/` or the processor's per-packet call directories | Copies of the successful native request/response records used by downstream tools. |
| `run_summary.json` and `progress.json` | Current operational status, separate from the retained call history. |

`new_request_attempts`, `reused_model_responses` and `recovered_saved_responses` are operational counters, not billing measurements or research performance metrics. Checkpoint/file identity checks prevent accidental reuse of changed material; they do not add stricter diagnosis acceptance rules.

## Verification scope

Fault-injection tests cover interruption after a saved response, request failures, processor merge interruption, round/tool budget continuation, changed inputs/checkpoints, completed-run reuse, concurrent ownership and CLI exit codes. Their provider responses are explicitly labelled test mocks. Existing real-sample replay and historical artifact checks remain separate. No fresh paid model run is claimed by these tests.
