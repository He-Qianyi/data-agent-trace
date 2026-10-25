# Evaluation protocol

## Complete recorded-output replay

`python -m reproduction.replay --output reproduction_output/complete-001` runs the released v5 fact extractor with capture/source checks, all original B/C packet validators, the original evidence-preserving merge, normalized-schema/source verification, the actual native evidence tools and separate diagnosis acceptance/read audits.

It asserts all historical step bodies and all task/operation evidence match, parses and checks all four saved annotation responses, and compares the full four-round diagnostic message histories and all five tool returns byte-for-byte as serialized strings or as complete JSON values where originally structured. Frozen corpus search statistics preserve original ranking/scores. The final diagnosis bytes must match the recorded file. All input files are hash-bound, and sockets are blocked. No model inference or recorded shell/Python/SQL code is executed.

A passing `evaluation.json` means this consistency replay passed. Strict schema/read auditing and current structural acceptance both pass for DABstep 1464. Its existing author review remains **`needs_revision`**: the diagnosis overstates an obligation to save standalone scripts when only inline code was created. This semantic issue is deliberately retained. The review is not independent gold and no accuracy metric is produced. Historical benchmark score files are preserved separately as context; their scorer is not rerun.

## What the earlier excerpt evaluator computes

- File hashes against the released sample manifest.
- Byte equality of the saved real diagnosis to its original recorded hash.
- The strict declared five-section JSON schema.
- The current CodeTracer-compatible structural policy: parseability, core fields and known critical-decision step IDs; additional fields remain untouched.
- Whether cited diagnostic evidence IDs are present in the public excerpt.
- Exact-quotation qualification for recorded processor annotations, retaining multiple matches and unavailable excerpt evidence separately.
- Static code facts recomputed from the actual published action arguments without executing code.

Each result is reported with its scope. Existence of an evidence ID is not entailment, and this excerpt does not replay the complete original model-read audit. The original audit implementation is included for runs that have complete retrieval logs.

## Real sample outcome

The real saved diagnosis contains `root_cause_chain_note: null`. The strict schema rejects the additional field; the current permissive structural policy accepts it. All original diagnostic evidence references are included in the public excerpt. These are mechanical observations, not a claim that the diagnostic conclusions are correct.

The original author review says the cross-validation F1 aggregation convention was not uniquely specified by the task. It should remain an unresolved interpretation rather than a confirmed failure label. The existing review is disclosed as author review and not relabelled as independent gold.

## Unavailable semantic metrics

There is no released independent gold set for root causes or causal chains. Accordingly root-cause accuracy and causal-chain accuracy are `null`, not zero, perfect, or inferred from LLM agreement. Baseline comparisons and ablation results are not populated with simulated values.

To add semantic evaluation later, first establish an annotation protocol, independent labels, unit of analysis and a held-out split. The current evaluator does not invent these assets. Existing structural validators and source audits must remain separate from that future evaluation.
