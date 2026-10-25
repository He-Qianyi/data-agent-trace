# Recorded experiments and validation evidence

This publication adds three groups of **existing** research records. Original outcomes, incomplete runs, content-review issues and historical acceptance policies are retained. No new inference, benchmark execution, independent labels or accuracy claims were created for publication.

| Group | Included evidence | Main boundary |
| --- | --- | --- |
| [New ten-case diagnosis experiment](unseen_ten/README.md) | Frozen experimental candidate prompt/runner, selection, all 44 saved model responses, final outputs, native and separately postprocessed assessments, author review and evidence checks | Native structural acceptance is 6/10; the existing wrapper-only extraction gives 10/10. Neither is diagnosis accuracy. |
| [Full processor validation and corpus selection](processor_validation/README.md) | Existing all-164 validation reports, trace/hash inventory, field-recovery checks, original audit scripts, and complete 145-retained/19-excluded decision lists | Full normalized trace objects and datasets are not redistributed. Their source checks are archived outcomes, not rerun here. |
| [Diagnosis controls and negative outcomes](diagnosis_controls/README.md) | Six recorded experiments, original plans/method snapshots, provider responses, final/nonterminal outcomes, message/read signatures and prior audits | Different designs and acceptance policies remain separate. Unfinished diagnoses remain unfinished. |

## Verify without a model

From the repository root, after `python -m pip install -e .`:

```bash
python -m reproduction.verify_research \
  --artifacts research_artifacts \
  --output reproduction_output/research-audit-001
```

Use a new output directory. The command blocks network connections and writes `verification.json` and `report.md`. It checks the closed public file inventory and original/public source-hash bindings; actual saved responses and tool-call identities; indexed message-prefix continuity and tool-result content hashes; native versus wrapper-extracted JSON; four deterministic author-review checks from published evidence; the all-164 validation inventory; and the complete, disjoint 145/19 selection partition.

The experiment archives live in the repository. When using an installed wheel, pass `--artifacts /path/to/checkout/research_artifacts` explicitly.

## What is and is not reproduced

Saved native responses and final outputs are included, with same-length substitutions of local path prefixes where needed. `publication_provenance.json` records each original source hash, public hash and publication change. Additional indexes are explicitly marked as deterministic exports, not newly discovered model results. Every source file exported or hashed as withheld context was checked against its original before publication.

The complete multi-benchmark model requests, native source/tool contexts, normalized trace corpus and benchmark datasets are not all redistributed. Their original hashes and the released message/read indexes make that boundary explicit; `withheld_context.json` exists for each run. Message signatures and recorded audit results are not substitutes for rereading source text or proving semantic entailment.

The separate [complete DABstep 1464 sample](../samples/real/dabstep_1464_claude/README.md) continues to support full raw-recording and native-tool replay. This publication audit does not claim to extend that full-context guarantee to the whole corpus.

Historical `method/`, manifests and reports are frozen source snapshots. Their original paths and references may require the matching research archive. They are included for inspection and provenance; use the portable verification command above for this public package. Do not run a historical runner expecting missing datasets or contexts to be generated.

Root-cause accuracy remains **unavailable (`null`)**. The existing author-style review is not independent, blinded human gold. Supplemental dataset calculations and recorded official answers used in that review are post hoc checks, not evidence supplied to the diagnostic model.

See [source-material notes](NOTICE.md). The publication does not promote the experimental candidate prompt to the default runtime.
