# Current diagnosis acceptance

The only current diagnosis acceptance policy is **`codetracer-detailed-compatible-v1`**, implemented in [the validator](../pipeline/stages/diagnosis/validation.py) and declared in [CURRENT_ACCEPTANCE.json](../configs/diagnosis/CURRENT_ACCEPTANCE.json).

It follows CodeTracer's pinned [_validate_detailed](https://github.com/NJU-LINK/CodeTracer/blob/2d302191dd07e7c0c2da6f7a5e9451c7cbb62d34/src/codetracer/services/validation.py):

1. The native output must parse as a JSON object.
2. These five top-level fields must exist: `root_cause_chain`, `critical_decision_points`, `correct_strategy`, `stage_labels`, `summary`.
3. A provided, non-null critical-decision `step_id` must exist in the trajectory.

Additional fields and content beyond those checks are accepted unchanged. Local string step IDs stay strings. Malformed decision-point containers that would crash the upstream loop return a failure rather than crashing.

Current `reproduction.replay`, `reproduction.evaluate` and `reproduction.diagnose` do not run the legacy strict diagnosis schema/read validator. Evidence coverage, read logs and author review may be reported, but do not change CodeTracer-compatible acceptance. Valid JSON is still required: wrapper extraction is not silently performed.

## Separate questions

| Question | What checks it |
| --- | --- |
| Is this diagnosis structurally accepted? | The CodeTracer-compatible checks above. |
| Are the released files and replay outputs the recorded material? | File hashes, source bindings and exact replay comparisons. These are material-integrity checks. |
| Is the diagnostic explanation correct? | Semantic review; independent gold is currently unavailable. Structural acceptance does not answer this question. |

The processor's existing fact extraction and evidence-preserving merge remain the released research method. Historical schemas, prompts, validators and saved results under the frozen sample/research directories are preserved for provenance; they are not alternative current diagnosis gates.

## The two real samples

| Sample | Current acceptance | Historical record and semantic scope |
| --- | --- | --- |
| Complete DABstep 1464 | Passes | Its saved legacy strict audit passed, but author review is `needs_revision` because a script-saving obligation was overgeneralized. |
| Agriculture 22 excerpt | Passes, including the original extra field | Its saved legacy declared-schema result failed on that field. This is a historical policy difference, not a current rejection or an accuracy label. |

Historical acceptance counts and formatting-only revalidation outcomes are not rewritten. Missing independent semantic accuracy remains `null`.
