# Data scope and provenance

## Included real data

`samples/real/agriculture_22_claude/` is a projection of one already-recorded AgenticDataBench run. The original task, selected original action arguments and observations, recorded processor annotations, final response, diagnosis and existing author review are included.

Evidence text is copied unchanged. Source object hashes, the normalized-trace hash and the original diagnosis hash are retained. The public sample manifest binds the actual released files. The original full source objects are not claimed to be included or resolvable from this excerpt alone.

The excerpt covers S0004-S0006. Earlier local-path/directory-listing evidence and a raw input-row preview are excluded explicitly. No missing native records are reconstructed. The example is not a complete A-stage input and must not be submitted as one.

The real sample does not redistribute the original CSV dataset, reference answers, benchmark evaluation files, post-run snapshots, credentials or hidden model reasoning. Dataset access remains with the upstream benchmark. The task/code repository uses Apache-2.0; its license is included. No claim is made that this automatically relicenses separate upstream datasets.

## Prompt examples and export changes

The complete available method prompts include their original methodological examples. In public copies, local filesystem spellings are replaced consistently with `${PROJECT_ROOT}` and `${LOCAL_MIRROR}` placeholders. Original research-source hashes and public-copy hashes differ where these edits occur and are both recorded. Public prompt copies should not be described as byte-identical historical prompts.

Runtime limits, schema and algorithm logic remain traceable to the original files. `docs/research-source-manifest.json` discloses original locations relative to the research project, source hashes, release hashes and export edits. Original research files and frozen experiments were not modified.

## Not redistributed

The full multi-benchmark corpus, all native model I/O, every earlier attempt and historical request prefix are not included. The local corpus-selection pointers are recorded for provenance, not as claims that all pointed-to data are shipped. A complete historical replay still requires those matching archives.

Independent gold annotations and completed paper-level comparisons do not presently exist in this release. They are not replaced by synthetic labels or the earlier tutorial demo.
