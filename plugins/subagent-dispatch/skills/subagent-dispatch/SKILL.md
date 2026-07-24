---
name: subagent-dispatch
description: Route delegated Codex work to the native default, creative_text, or image profile and verify that the plugin has initialized all three profiles. Use whenever a task may spawn, direct, wait for, review, or close a subagent, when profile selection matters, or when native subagent profiles are missing or stale.
---

# Subagent Dispatch

Use native Codex subagents. Do not replace them with independent `codex exec` processes.

## Route the task

Choose exactly one profile:

| Profile | Use for | Model | Effort | Image generation |
| --- | --- | --- | --- | --- |
| `image` | Raster image generation, editing, inspection, comparison, and QC | `gpt-5.6-luna` | `max` | enabled |
| `creative_text` | Fiction, scripts, poetry, story development, and creative revision | `gpt-5.6-luna` | `max` | disabled |
| `default` | Exploration, verification, search, triage, tests, and ordinary read-heavy work | `gpt-5.6-luna` | `medium` | disabled |

Use `default` as the ordinary fallback. Never route image work to `default`, or text-only creative work to `image`.

## Spawn natively

1. Select the exact native profile through the spawn tool's agent type or role field.
2. Set `fork_turns = "none"` and provide a self-contained assignment.
3. State scope, concrete question, expected output, and constraints in every assignment.
4. Ask for compact internal results with exact paths, symbols, values, and sanitized errors where useful.
5. Wait for all required children before synthesis. Close completed children when the client supports it.

If the requested profile is unavailable, run the synchronization check below. Repair drift before retrying. If the client still rejects the profile or model, report the incompatibility without silently selecting another role.

## Respect profile contracts

- `default`: act as a read-heavy scout; do not make ordinary code changes, final decisions, or images; do not spawn children.
- `creative_text`: write manually and humanly; do not generate images or spawn children.
- `image`: load `imagegen` before image work; use visible reference inputs; may generate, edit, inspect, compare, and quality-check raster images; do not spawn children.

Keep secrets in the main thread. Treat child results as evidence to verify, not final user-facing conclusions.

## Verify initialization

The plugin hook synchronizes its bundled profiles to `${CODEX_HOME:-$HOME/.codex}/agents/` at session start and immediately before native spawn. It uses the runtime-provided `PLUGIN_ROOT`, so it remains valid when Codex installs the plugin into a versioned cache on another device.

Resolve the active plugin root from this skill's installed path, then run:

```bash
python3 <plugin-root>/scripts/install_profiles.py --check
```

Run without `--check` to repair missing or stale files. The installer is idempotent, validates all three TOML sources before writing, updates atomically, and never deletes unrelated agent files.

Plugin hooks require device-local trust. If automatic synchronization is skipped, review and trust the plugin's current hook definition, then start a new task. Do not copy hook trust hashes between devices.
