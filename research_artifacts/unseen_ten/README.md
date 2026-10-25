# New ten-case diagnosis experiment: recorded 20 October 2026

The original experiment selected ten official non-full-score traces using a frozen rule, excluding traces already used in earlier diagnostic trials or examples. Selection, exclusions, source trace hashes, agent/framework identities and step/evidence IDs are preserved in `recorded_run/selection.json`, `recorded_run/plan.json` and `recorded_run/case_index.json`.

This experiment used **`v1-no-repeat-and-unlocalized-candidate`**, the last actually tested candidate, with a 24-round/60-tool-call budget and no forced final round or automatic retries. The public default remains original-v1. The experimental runtime, actual prompt and original runner are preserved under `recorded_run/current_runtime.json` and `recorded_run/method/`.

## Read the outcomes in this order

1. [Original experiment report](recorded_run/report.md): ten completed runs, 44 model responses and 57 read-only tool calls. Six native outputs pass the structural contract. Four return surrounding prose and a JSON fence and retain their original `invalid_json` status.
2. [Separate formatting revalidation](format_revalidation/report.md): the already-recorded postprocessing keeps the exact JSON substring from those four outputs. Ten pass after extraction. `extraction.json` records original ranges/hashes; payload fields, numbers, conclusions and evidence IDs were not rewritten. The native results above are unchanged.
3. [Content review](author_review/report.txt): all ten are checked with explicit supporting evidence, errors and limits. `review.json`, `evidence_ledger.json` and `checks.json` preserve the detailed findings. The review is author-style, unblinded and not independent gold.

The publication verifier recomputes native and extracted acceptance separately, verifies the exact retained substrings, and recomputes four content spot checks from published evidence. One retained example is the actual eight immediate rules versus the diagnosis's reported seven. It does not silently fix that diagnosis. The separate current-input sensitivity calculations remain recorded outcomes; they require the original benchmark files to rerun.

## Records and reproduction scope

`recorded_run/results/<record_id>/` includes all saved native provider responses/receipts, available native final text, existing diagnosis JSON where originally produced, and recorded status/read/acceptance assessments. Missing native diagnosis JSON is not manufactured. `format_revalidation/results/` is a separate historical output set.

`call_index.json` binds responses to original request hashes, published-path message signatures, tool calls and recorded context roles. `read_event_index.json` binds read events to actual source-page ranges and content hashes. `withheld_context.json` inventories complete request/tool contexts not bundled here. This is a public research-results capsule, not a full ten-trace source-data release.

Run the unified command in [the artifact guide](../README.md). No API key, dataset download or model call is needed for its published-record checks. The frozen historical runners and supplemental review checks require their matching original corpus; they are not invoked by this verifier.
