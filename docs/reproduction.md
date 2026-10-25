# Reproduction guide

## Three distinct activities

1. **Offline real-sample checks:** inspect the released recorded excerpt, re-extract code facts, check saved annotations and diagnosis structure, and bind exact quotations. This needs no model and does not rerun the original analysis.
2. **Fresh method execution:** run A/B/C/D and native-tool diagnosis on eligible complete recordings supplied by the user. This makes explicit live model calls only with `--allow-network`.
3. **Historical experiment replay:** the archived original batch and continuation code relies on its historical selections, reuse ledgers, request histories and full source objects. Those records are not all publicly available. This release does not claim full historical replay or identical stochastic model outputs.

## Environment

Use Python 3.10+ on Linux or macOS. POSIX `fcntl` is used by original runners; native Windows execution is not verified. Install with `python -m pip install -e .`. `requirements-tested.txt` records the exact versions used for offline release validation on Python 3.12/macOS ARM64; it is an environment record, not a cross-platform binary lock.

No GPU is required for the released offline evaluator. Full analysis-task execution, GPU model hosting, and benchmark databases are not part of this command: the method analyzes recordings rather than executing their code. Full API cost/time for the portable adapters has not been measured and is not estimated as a research result.

## Offline evaluation

```bash
python -m reproduction.evaluate --sample samples/real/agriculture_22_claude \
  --output reproduction_output/check-001
```

This writes `evaluation.json`, `recomputed_static_facts.json`, and `report.md`. Output directories must be new. `evaluation.json` records coverage exclusions and unavailable independent gold. A reported strict-schema failure for the sample is expected from its original extra field; do not delete the field to improve the result.

## Complete native input for A

The fact-layer CLI consumes a JSONL manifest. Each eligible row identifies the recorded task and agent and points to the actual original files. Relevant fields are:

- `record_id`, `benchmark`, `agent_id`, `task_id`.
- `attempt_directory`, `model_io_path`, `recording_level`.
- `sources`: source-file `path` and `role` pairs.
- `declared_source_hashes`: original path to SHA-256 mappings.
- `eligible_strict_completed`, `strict_recording_complete`, and a normal completion receipt.

The input must actually satisfy the recording verifier; setting flags is not sufficient. Original Anthropic relay, Codex native and direct-response recording adapters are included. See `fact_layer_v5/adapters.py`, `build.py`, and `scripts/curate_complete_traces.py` for precise contracts. The included unit tests exercise small synthetic recorder fixtures and are labelled as such; these are not research samples.

```bash
python -m pipeline.stages.trace_processor.fact_layer_v5 \
  --manifest /path/to/eligible_recordings.jsonl --output reproduction_output/facts
```

A never calls a model or executes recorded SQL/Python/shell code. It retains source references and explicit unknowns.

## Fresh B/C/D execution

Create a local credential file following `.env.example`. Do not commit it. The released native clients preserve the historical verified SoCLaaS endpoint and model constraints; no general-provider compatibility is claimed.

```bash
python -m reproduction.processor --facts reproduction_output/facts \
  --output reproduction_output/processor --env-file /path/to/local-provider.env \
  --max-requests 10 --allow-network
```

The adapter uses the frozen B/C prompts and schemas, the original requirement validator, operation-evidence packet builder, provider client, merge implementation, normalized schema and source verifier. It records actual native responses and receipts, does not manufacture missing annotations, and stops on failed requests, validation failures or budget exhaustion. Model identity is checked against the saved expected returned-model value.

The fresh release adapter is deliberately sequential and has no historical reuse ledger. The historical scheduler used concurrency 2; the incremental scheduler releases B results in groups of 30 and is included under `research_runners/processor/increment289/`. These orchestration differences are disclosed; the fresh adapter is not presented as a byte-identical replay of a historical batch.

The output `final/` is a source-bound normalized bundle. Verify it with:

```bash
python -m pipeline.stages.trace_processor.merge_layer.verify reproduction_output/processor/final
```

For the original DuckDB/Parquet downstream export, run:

```bash
python -c "from pipeline.stages.trace_processor.merge_layer.downstream import export; export('reproduction_output/processor/final')"
```

This is exercised by the offline integration test on explicitly mocked annotations and synthetic recorder fixtures.

## Fresh Trace Diagnosis

```bash
python -m reproduction.diagnose --bundle reproduction_output/processor/final \
  --output reproduction_output/diagnosis --env-file /path/to/local-provider.env --allow-network
```

The adapter uses the original EvidenceIndex, SafeEvidence, five native read-only tools and native client. It preserves model tool calls, model/response identity checks, read ranges, final output and receipts. It allows up to 24 model rounds and 60 tool calls, does not force a final response, and does not retry failures automatically. A nonterminal run remains budget-exhausted or stopped; it is not converted into a diagnosis.

Current CodeTracer-compatible structural acceptance is saved separately from the strict declared schema/read audit. Neither is semantic correctness evaluation. The historical round-24 code continues a saved 12-round prefix; a fresh 24-round call sequence is a different experimental procedure and is labelled accordingly.

## Original archived runners

- `research_runners/processor/batch164/`: original selection/preparation, sealing, missing-work execution and inventory logic.
- `research_runners/processor/increment289/`: current incremental fact preparation, annotation runner and pipelined scheduler.
- `research_runners/diagnosis/native_tools/`: original pilot, reports and recovery code.
- `research_runners/diagnosis/round24/`: paired continuation, source/read-efficiency and message-chain audits.

These preserve historical assumptions and some `${PROJECT_ROOT}`/`${LOCAL_MIRROR}` placeholders. They require the matching historical archive and are included for method inspection. Use the portable adapters for fresh user-supplied recordings. Do not claim an absent historical archive was recreated.

## Validation during this release

Offline tests run with network-free mock annotation/diagnosis clients. The real public sample evaluator reads actual saved records. The release does not call a model or rerun benchmark tasks. Both validation modes are recorded separately in `docs/release-verification.json`.
