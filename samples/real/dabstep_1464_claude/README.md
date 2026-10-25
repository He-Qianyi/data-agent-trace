# Complete real recorded sample: DABstep 1464 / Claude Code

This sample contains **all nine recorded model exchanges, all eight observed tool steps, all recorded B/C annotations and all four diagnosis rounds with five native tool calls** from one existing research run. It is not a synthetic fixture or an extended version of the agriculture excerpt.

Run from the repository root after `python -m pip install -e .`:

```bash
python -m reproduction.replay \
  --sample samples/real/dabstep_1464_claude \
  --output reproduction_output/complete-001
```

Use a new output directory. This command blocks network connections, needs no API key/GPU, and does not execute the recorded analysis code. It rebuilds facts using the released v5 extractor with fresh recording/source checks, verifies the recorded annotation requests and responses, reruns the original merge and native evidence tools, and writes `evaluation.json` and a readable `report.md`. It reuses saved model outputs; it does not regenerate model predictions.

## Included records

| Path | Content |
| --- | --- |
| `raw/attempt-001/` | Complete four-surface request/response ledger, native CLI transcript, prompt, task, provider metadata, completion result, recorded answer and historical benchmark evaluation |
| `raw/result.json` | Recorded parent-level completion receipt |
| `raw/manifest.json` | Portable bindings to these original source copies; replay resolves paths at runtime |
| `recorded_processor/shared/` | Original task/operation views, requirements input, native and accepted requirements, and provider request/response/receipt |
| `recorded_processor/cases/` | All three original operation packets and their native outputs, provider requests/responses and receipts |
| `recorded_diagnosis/` | Four original model request/response/receipt sets, five original tool-read records, read ranges, native final text, JSON diagnosis and original pilot status |
| `recorded_diagnosis/search_statistics.json` | Newly exported deterministic aggregate statistics of the historical retrieval corpus; only tokens already present in this sample |
| `reference/historical_fact.json` | Original v1 fact snapshot for source/step/evidence comparison; historical path/hash fields are provenance, not portable runtime bindings |
| `author_review.json` | Existing author review, explicitly not independent gold |
| `provenance.json`, `manifest.json` | Original and published source hashes, disclosed transformations, and the complete public file inventory |

The original optional `input_data_manifest.json` was absent. No replacement is created. The full benchmark input dataset is not needed for trace replay and is not included. The raw transcript preserves its original read-size error and recovery; no unsuccessful step is removed.

## What exact replay means here

Every original action/observation body, exchange ID, final response, task-evidence block, operation-evidence block and unit boundary is checked against the recorded snapshot. Every B/C response is decoded from its saved provider response and compared with the accepted native output. Complete diagnostic message histories and every native tool return must match the saved records, including pagination, search ranking and scores. The saved diagnosis is copied byte-for-byte.

The historical search index covered 220 units; this trace has nine. Rebuilding search over one trace alone changes BM25 scores. The supplied aggregate term frequencies, average lengths and document count reproduce the actual historical search without distributing other traces. A small release adapter applies the original scoring formula to real local units using those frozen statistics. It is not a fresh search experiment.

Only disclosed local filesystem prefixes, including their shell/JSON escaped spellings, are pseudonymized with same-length replacements. Source and published hashes are recorded separately. The ledger/transcript, action/observation evidence and diagnosis are unchanged in this sample. Public prompt examples have path substitutions; replay normalizes only those disclosed path spellings when comparing prompts. No missing model output, tool result, gold label or score is generated.

## Expected outcome and known limitation

`expected_outcome.json` records the verified expected checks, including the unresolved semantic limitation. The processor bundle, complete message replay, all five native tool results, strict schema/read audit and current structural acceptance pass. The report also preserves the existing author review status **`needs_revision`**: the diagnosis overgeneralizes “Save every script you create” into requiring an extra script file for inline commands. This recorded semantic issue is not repaired to improve the demonstration.

The historical benchmark agent score is retained as context but is not recomputed. It is excluded from the diagnostic input, search and evidence tools. Root-cause accuracy remains `null` because there is no independent diagnosis gold. A successful replay certifies software/data consistency, not the truth of the diagnosis.

## Attribution

DABstep: Data Agent Benchmark for Multi-step Reasoning, © 2025 Alexander David Egg, Martin Iglesias Goyanes, Andreu Mora, Friso H. Kingma, Thomas Wolf and Leandro Von Werra. Task/context material is from [adyen/DABstep](https://huggingface.co/datasets/adyen/DABstep), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The original license notice is included. Local path substitutions are disclosed above and in provenance. Research recording and annotations: He Qianyi. No upstream endorsement is implied.
