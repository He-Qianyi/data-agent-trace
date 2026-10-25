# Scope and methodology

## Research workflow

Raw traces are collected across combinations of data-analysis benchmarks, agent frameworks, and model configurations. Recording integrity and source availability are checked before downstream interpretation. These preparation steps support the two main research stages below.

## Trace Processor

The working research system retains native interactions and tool receipts, extracts conservative code and resource facts, organizes original task requirements, and annotates analytical operations with exact source evidence. Its merged representation keeps facts, interpretations, source locations, and quality limitations separate.

The selected package contains the fact-layer v5 extractor, bounded constant resolver, source-reference utilities, evidence-view builders, semantic contracts, and literal citation validators. The full recording adapters, merge/runtime orchestration, and experiment registry are outside this release. Copied modules retain their research logic; import-path changes for the selected test are recorded in the provenance manifest.

## Trace Diagnosis

The diagnostic stage compares the original task with recorded actions and outcomes. It follows relevant evidence, considers alternative explanations and later corrections, and distinguishes observed discrepancies from causal claims. Root-cause chains are retained only when their links can be supported.

The published prompt excerpt contains the original core instructions and the declared output schema, with real benchmark examples removed. Its five-section organization is adapted from CodeTracer. The complete model/retrieval runtime is not part of this selected release.

The strict published JSON schema is an output contract. It should not be confused with the working project's separate CodeTracer-compatible structural acceptance policy, evidence audit, or semantic review. Passing a schema check does not certify a diagnosis.

## Demonstration

The synthetic example requests a monthly revenue total excluding cancelled orders. The supplied recorded program filters the month but omits the exclusion; a supplied tool observation reports a total that includes the cancelled row. A supplied final answer repeats that value. The entire toy trace is short enough to inspect, and includes an explicit statement that all steps and the final answer are present.

The example diagnosis is authored for demonstration and labelled accordingly. It is not produced by a model or used to claim benchmark performance. The demo does not run the recorded program, connect to a database, or infer labels for arbitrary traces.

## Evidence and uncertainty

- Preserve source offsets and exact quotations.
- Keep alternative matches when a quote occurs repeatedly.
- Do not treat definitions as execution or tool exit status as task correctness.
- Do not turn incomplete evidence into an asserted causal chain.
- Separate agent errors from input, environment, evaluator, and recording issues.

## Release scope

Only selected source modules, original methodological instructions, and synthetic material are included. Credentials, local configuration, personal documents, raw benchmark recordings, hidden/reference answers, and experimental request/response logs are excluded. The active research workspace and frozen experimental versions remain separate from this repository.
