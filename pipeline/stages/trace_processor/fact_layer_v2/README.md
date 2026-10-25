# Fact layer v2: source-aware shell boundaries

This version fixes consecutive-newline command boundaries without changing the
fact schema, tool-step granularity, task/operation prompts or native annotations.
It splits real unquoted shell separators before decoding arguments, keeps
heredoc bodies as definitions, and never executes a captured command.

The v1 package and its frozen hashes remain untouched. The v2 package is an
explicit versioned copy for reproducibility; `extract.py` contains the functional
change. Existing conservative candidate-version binding remains unchanged and
does not certify successful execution or exact runtime file contents.

Use `python3 -m pipeline.stages.trace_processor.fact_layer_v2 --manifest ...
--output <new-directory>` for future offline builds. Current schema version
stays `data-agent-fact-layer-v1` because the field contract has not changed;
run configuration and the current-validation pointer identify this implementation.
