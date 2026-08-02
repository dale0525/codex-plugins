# Global Codex instructions

Keep personal, cross-repository instructions here. Put repository-specific guidance in each repository's own `AGENTS.md`.

## Subagent orchestration

The user grants standing authorization for proactive native Codex subagent delegation under these instructions; no per-spawn confirmation is needed. Delegate when broad reading, independent verification, or parallel work provides more value than dispatch overhead. Keep small known-file reads, final decisions, code changes, and user-facing conclusions in the main thread.

Choose exactly one profile for every delegated task:

- `default`: exploration, verification, search, triage, and tests.
- `creative_text`: fiction, scripts, poetry, story development, and creative revision.
- `image`: raster image generation, editing, inspection, comparison, and quality control.

Never route image work to `default` or text-only creative work to `image`. Set `fork_turns = "none"` and make every assignment self-contained with the scope, concrete question, expected output, and constraints. Keep secrets in the main thread. Wait for all required children before synthesis and treat their results as evidence to verify rather than final user-facing conclusions.

If a profile is missing or stale, invoke `$codex-sync` and use its normal `doctor`, `sync`, and reviewed `apply` workflow. Do not repair profiles automatically or through hooks.
