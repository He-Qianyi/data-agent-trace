# Fact layer v5 — constant positional connection arguments

Version: fact-layer-connection-positional-v5-20261020. Recording adapters, schema, semantic overlays, and v1/v2/v3/v4 implementations remain unchanged.

V5 reuses the existing bounded source-order string resolver for the first positional argument of an already recognized database connection. It preserves the entire URI/DSN string, including file: prefix, escaping, query parameters and Unicode. It never connects, executes code, interprets URI contents, or certifies runtime content.

A supplied positional argument takes precedence; if unknown, the value stays null rather than borrowing a keyword. With no positional argument, previous keyword-only database/dsn behavior is preserved. PostgreSQL dbname is not converted to a file path or fabricated DSN. Dynamic expressions and unsupported control flow remain unknown. Existing connection metadata, raw argument expressions, engine_verified=false, and statement_execution_verified=false are retained.

The resource directory receives the recovered value through the unchanged generic connection operation mapping. No task IDs, database filenames, sample values, prompts, annotations, scores, or evaluation rules occur in the new rule.
