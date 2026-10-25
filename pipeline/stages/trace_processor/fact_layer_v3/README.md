# Fact layer v3: source-backed null recovery

Versioned extension of v2; v1/v2 are not modified. Read explicit initial-provider
working-directory declarations with character-level source references; retain
constant f-strings without interpolation; decode shell-redirection paths with
shell lexical rules; honor a preceding cd guarded by && for script matching;
extract top-level constant Python writes of .py/.sql definitions. No captured
code is executed. Failed-tool definitions do not become valid candidates.

Dynamic expressions, unobserved resource access, missing protocol fields and
submission-variant redundant references retain their prior unknown/null values.
Candidate source versions are not certified runtime file hashes. General rules
are regression-tested with synthetic identifiers rather than task names.
