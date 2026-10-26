# Methodology

The research has two stages: **Trace Processor** reconstructs an analytical workflow from native recordings; **Trace Diagnosis** examines consequential decisions using source evidence. See the [real walkthrough](case-study.md) for one complete recorded example and the [run guide](reproduction.md) for execution modes.

## Trace Processor

The fact layer preserves native interactions and source references. Semantic annotation identifies task requirements and data operations. The merge connects those views without collapsing observed execution facts, assistant claims and LLM interpretations into a single asserted truth. Source-bound outputs support retrieval and inspection.

## Trace Diagnosis

The diagnosis stage uses task evidence, operation navigation and read-only source retrieval. It examines critical decisions, later recovery and competing explanations. Temporal order or a tool error alone is not sufficient to establish an analytical failure chain.

## Current acceptance and sample scope

Diagnosis acceptance uses only the [CodeTracer-compatible structural policy](acceptance.md). Legacy strict diagnosis validators are not run by the current portable entrypoints. Material-integrity checks preserve the identity of source files and recorded replay outputs; evidence audits and semantic review are separate from acceptance.

The complete **DABstep 1464** sample passes current acceptance and retains an existing `needs_revision` author review. Its saved legacy strict audit passed. The earlier **Agriculture 22 excerpt** also passes current acceptance; its saved legacy strict rejection concerns an additional field and is historical only. Neither sample provides independent root-cause accuracy.

Archived runners preserve their original batch, reuse and continuation methods. Portable adapters support fresh user-supplied complete recordings; orchestration differences are disclosed. Recorded-output replay reuses saved model responses rather than generating new predictions.
