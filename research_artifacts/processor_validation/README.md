# Full processor validation and corpus-selection evidence

This package preserves the completed v5 validation and selection audit, rather than replacing it with results from the small public sample.

## Processor v5: all 164 recorded traces

[v5/README.md](v5/README.md) is the original historical report. `v5/summary.json`, `postmerge_validation.json`, `annotation_compatibility.json` and `final/validation.json` record all-trace checks. `trace_inventory.json` is a new content-free export of every normalized trace's hash, identity, step/unit counts, citation qualification counts and source hashes.

The original final corpus contains 4,208 tool steps and 4,259 semantic units. The v5 update recovered 26 fields across two traces; original B/C evidence, native annotations and citations were retained. The existing per-trace verification, static downstream readback, null-preservation/recovery records and original reproduction scripts are included. Current extraction implementations and regression tests already live in the repository's `pipeline/` and `tests/` directories.

The public verifier checks that the published inventory matches all 164 recorded validation rows and unit totals. It does **not** rerun source resolution against the full corpus, whose complete normalized objects and source files are not included. The separately released complete real sample demonstrates portable source-bound replay for one trace. Stored all-corpus validation and current software tests must not be conflated.

## Corpus selection: 164 → 145 retained + 19 excluded

`curation/plan.json` preserves the rules. `decisions.json`, `retained_manifest.jsonl` and `excluded_manifest.jsonl` preserve every decision and its trace-hash anchor. Exclusions comprise 14 confirmed external-environment cases and five officially full-score traces with fewer than ten tool steps. Agent mistakes remain eligible; a keyword hit or absent present-day temporary path is not sufficient for exclusion.

`input_file_checks.json`, `input_check_summary.json`, `eligible_verification.json`, `subset_checks.json` and `final_verification.json` preserve the actual audit outcomes. The original selection scripts and SQL queries are included under `curation/method/` and `curation/queries/`. Current-file checks do not prove historical input equivalence or exhaustive environment correctness.

The portable publication verifier reconstructs the disjoint partition directly from the published lists and decisions and checks it covers the exact all-164 inventory. Original trace bytes were not modified by selection. The original `eligible/` directory was a local view; it is not supplied here as a falsely portable corpus.

Historical READMEs and configs retain references to the full research archive. Only the files listed in the public package manifest are shipped. Original benchmark datasets, databases, full normalized trace objects and private runtime contexts are not included. Read the [artifact scope](../README.md) before attempting a historical full-corpus rerun.
