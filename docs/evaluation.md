# Evaluation protocol

## What the released evaluator computes

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
