# Recorded diagnosis controls, including negative results

These are distinct historical experiments. They are not a completed factorial ablation study or an independent accuracy benchmark. Original strict assessments, later current-policy revalidation, reused early completions, service failures and nonterminal runs remain separately labelled.

| Directory | Actual design | Interpretation boundary |
| --- | --- | --- |
| `baseline_v1/` | Original eight-case native-tool pilot with a 12-round budget; saved transport/recovery records retained | The original strict policy and service-censored cases remain visible. |
| `forced_final_round12/` | Paired continuation of one affected case with `tool_choice=none` at round 12; five earlier completions reused | One new response, not eight independent reruns. A final JSON is not proof of a correct diagnosis. |
| `confirmation_gate/` | A separate recorded candidate experiment on the eight cases | Its prompt/procedure and semantic issues are distinct from the default and other controls. |
| `round24_continuation/` | Original prompt unchanged; one 12-round nonterminal prefix continued to round 24 | The affected case still lacks a final diagnosis; earlier completions and two service-censored cases are reused. |
| `service_rerun_two/` | Fresh original-v1 reruns of the two previously service-censored cases | Service recovery must not be attributed to a prompt improvement. |
| `no_repeat_and_unlocalized/` | Two fresh reruns with both a repeat-reading restriction and permission to state that the cause cannot be localized | Both reach the budget without a final JSON. Two prompt changes were made together; neither has an isolated causal effect estimate. |

Each directory contains the actual plan and available prompt/method snapshots, original summaries/reviews/audits, status records and all saved native provider responses/receipts. Final diagnosis files appear only where they existed. Context indexes preserve original hashes, published message signatures, read ranges and tool-result content hashes. Missing responses and final outputs remain missing.

`round24_continuation/read_efficiency.json` preserves the repeated-reading audit, and `message_chain_audit.json` preserves the original continuation-history check. `no_repeat_and_unlocalized/comparison.json`, `message_and_read_audit.json` and `request_field_read_audit.json` retain the actual comparison and evidence-reading analysis, including negative outcomes.

`acceptance_policy_revalidation/` contains a separate, already-completed re-evaluation under the CodeTracer-compatible structural policy. It made no new model requests and does not rewrite earlier strict results. Do not compare strict/permissive pass counts as if only the diagnostic method changed.

The unified [publication verifier](../README.md) cross-checks actual saved responses, indexed message continuity, native tool identities/content hashes and missing-final outcomes. It reports current-policy acceptance separately without changing original files. Full native context text and underlying benchmark datasets are not all bundled, so this is not a complete source-context replay of every trial.

No model calls were made to prepare this release. These recorded limitations are part of the evidence, not failed runs to be dropped from a success-rate denominator. There is no independent semantic gold and no root-cause accuracy estimate.
