# Loop Guard

Loop Guard is a privacy-preserving Codex hook plugin for detecting exact
repeated tool failures. It starts in observe-only mode and does not impose hard
limits on long-running tasks.

The hook keeps keyed fingerprints rather than raw tool arguments or results. A
new user prompt, a successful call, a changed call, or an expired window resets
the sequence. In explicitly enabled enforce mode, the third identical failure
advises the model to change strategy and a fourth identical call is denied
before execution.

See the bundled `loop-guard` skill for inspection and enablement guidance.
