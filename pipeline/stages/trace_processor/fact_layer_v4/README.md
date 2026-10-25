# Fact layer v4 — bounded constants and database keyword arguments

Version: fact-layer-constant-connection-v4-20261020. Same normalized schema and recording adapters as v3; v1/v2/v3 remain frozen.

- New values are recovered only for SQL strings and read/write paths from straight-line module bindings, immutable string concatenation, constant string f-interpolation without conversion/format specs, and imported os.path.join/posixpath.join with all-string arguments. Variable reassignment is source ordered; unknown reassignment, branches, loops, local/deferred scopes, comprehensions, shadowed imports and reflective mutation do not provide guessed values.
- Existing non-null fields and unrelated literal extraction remain unchanged. Original code/expressions and statement_execution_verified=false remain present.
- Keyword-only calls to imported database drivers or unresolved canonical driver spellings are recorded as lexical connection operations. Literal option values and exact keyword expressions are preserved, including unknown runtime port/dbname values. Database names are not fabricated file paths or DSNs; absent DSN path remains null. File-backed sqlite3/duckdb database keyword may supply a path. engine_verified remains false.
- The resource directory propagates recovered read paths and exact connection options. It does not infer access_observed, successful imports/connections, or runtime execution.
- No captured code is executed; no LLM annotation or prompt is changed.

Canonical names without a recorded import are source syntax only; missing imports/NameError are not repaired. Explicitly shadowed names are not promoted to database operations.
