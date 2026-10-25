# Real recorded sample: agriculture_22 / Claude Code

This is an excerpt of an existing research run, not a synthetic trace or a new model execution. Evidence text is copied unchanged from the frozen normalized trace. SHA-256 anchors are recorded in `provenance.json`.

The public excerpt includes the task and steps S0004-S0006: dataset-level inspection, the recorded analysis program and aggregate observations, the output readback, and the recorded final answer. Steps S0001-S0003 are excluded because they contain local workspace metadata or raw input-row previews. The original dataset, native system prompts, hidden reasoning, scores/reference answers and post-run snapshots are not redistributed.

`processor_annotations.json` contains recorded task and operation annotations for the published steps. `diagnosis.json` is the actual saved diagnosis, byte-for-byte unchanged. It includes an extra `root_cause_chain_note` field. Accordingly the strict declared schema fails while the current CodeTracer-compatible structural policy passes. Neither result certifies semantic correctness.

`author_review.json` is an existing author review, explicitly not independent gold. It records that the F1 aggregation interpretation is unresolved by the task and should not be treated as a confirmed error. The sample is not a benchmark accuracy dataset.

Source task/code repository: https://github.com/AgenticDataBench/AgenticDataBench (Apache-2.0; license text included). The original agricultural dataset is not included. Aggregate model outputs and researcher-generated trace annotations are released here as research artifacts.

Run `python -m reproduction.evaluate --sample samples/real/agriculture_22_claude --output /path/to/new-evaluation-directory` from the repository root. This recomputes mechanical checks without executing recorded code or calling a model.
