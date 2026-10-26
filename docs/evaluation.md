# Evaluation protocol

## Current diagnosis acceptance

All current portable diagnosis/evaluation entrypoints use **CodeTracer-compatible acceptance only**: a parseable JSON object, five top-level fields and valid provided critical-decision step IDs. Additional fields are accepted unchanged. Legacy strict diagnosis schema/read audits are not run. See [the exact policy](acceptance.md).

## Complete recorded-output replay

`python -m reproduction.replay --output reproduction_output/complete-001` rebuilds the released fact/annotation/merge outputs, reruns the actual native evidence tools and checks the saved diagnosis with the current policy. It compares original step bodies, task/operation evidence, saved annotation responses, complete diagnostic message histories, tool results and diagnosis bytes against the recording. Frozen corpus search statistics preserve the sample's original ranking and scores. Sockets are blocked; no model inference, captured code or benchmark scorer is executed.

A passing replay certifies consistency with the published recording. **DABstep 1464** passes current structural acceptance. Its existing author review remains **`needs_revision`**: the diagnosis overstates an obligation to save standalone scripts when only inline code was created. This review is not independent gold. The saved legacy strict audit and expected-outcome field are preserved on disk, but the current replay does not use that field as an acceptance gate.

## Earlier real excerpt

`python -m reproduction.evaluate --sample samples/real/agriculture_22_claude --output reproduction_output/check-001` verifies the published sample/diagnosis identity, applies the current acceptance policy and separately reports evidence-ID coverage, literal quotation qualification and recomputed static extraction. It does not replay the complete original model-read audit.

**Agriculture 22** passes current acceptance with its original extra `root_cause_chain_note` field. The old declared-schema rejection remains a historical record; the current evaluator does not recompute or enforce it. Its author review also retains an unresolved interpretation of cross-validation F1 aggregation. Presence of an evidence ID or a matching quotation does not prove the diagnostic conclusion.

## Semantic evaluation

Independent root-cause and causal-chain gold is unavailable. Corresponding accuracy metrics remain `null`. Author review, structural acceptance and material-integrity checks must not be reported as independent semantic accuracy. A future semantic study needs its own annotation protocol, labels and evaluation split; the current release does not fabricate those assets.

## Existing experiment records

`python -m reproduction.verify_research --artifacts research_artifacts --output reproduction_output/research-audit-001` checks the published inventory, saved native responses, indexed message/read continuity, recorded formatting extraction, author-review spot checks and the 164-case selection partition. It blocks network access and executes no captured code.

Historical acceptance policies/counts remain as recorded. The ten-case run retains native acceptance 6/10 and separately recorded formatting-extracted acceptance 10/10. These are structural outcomes, not diagnostic accuracy. The full private-corpus source audit is not rerun by this public command.
