# Methodology

The two main stages are Trace Processor and Trace Diagnosis. The complete available stage implementations and their helper dependencies are now included. The high-level workflow and quick commands are in the root README; operational details are in `reproduction.md`.

Trace Processor preserves native interactions, source references, static extraction and evidence-grounded task/operation annotations. Observed execution facts, assistant claims and LLM interpretations remain separate. Its merged bundle supports independent source/hash and exact-citation checks.

Trace Diagnosis uses original task evidence, operation navigation and read-only source retrieval. It examines critical decisions, later recovery and competing explanations. Causal chains are supported explanations rather than consequences of step order or an error message alone.

Mechanical validity, evidence support and semantic correctness are separate evaluation dimensions. The public real sample retains a strict-schema failure and a permissive structural acceptance, with no root-cause accuracy inferred from either.

The archived runners preserve the original batch/reuse/continuation methods. The new release adapters support fresh user-supplied recordings, with differences from historical orchestration recorded explicitly. The public excerpt supports offline inspection; it is not a fabricated complete native recording.
