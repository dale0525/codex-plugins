---
name: loop-guard
description: Inspect or explain the Loop Guard Codex hook, its observe/enforce mode, privacy behavior, recent repeated-failure candidates, or local diagnostic state. Use only when the user explicitly asks about Loop Guard or repeated tool-call protection.
---

# Loop Guard

Loop Guard watches exact repeated tool failures without imposing generic call,
time, or cost limits on legitimate long-running work.

## Behavior

- The shipped default is `observe` mode. No tool call is blocked.
- State is scoped by session, turn, and transcript when all three identifiers are
  available.
- Only explicit failures count: non-zero exit codes, structured error states, or
  structured tool errors.
- The tool name, canonical arguments, and failure evidence are stored only as
  keyed HMAC digests. Raw prompts, arguments, tool results, paths, and commands
  are not written to the plugin database or event log.
- A successful tool call, a different tool signature, a new user prompt, or an
  expired time window resets the consecutive-failure sequence.
- Hook errors fail open and never stop the Codex task.

## Local files

Codex supplies the plugin's writable `PLUGIN_DATA` directory to hooks. Loop
Guard stores:

- `state.sqlite3`: bounded per-scope counters.
- `events.jsonl`: privacy-safe candidate events, rotated at the configured size.
- `secret.key`: local HMAC key with owner-only permissions where supported.
- `config.json`: optional local override.

The versioned defaults are in `config/defaults.json`. Do not switch a user from
`observe` to `enforce` without reviewing candidate events and obtaining explicit
approval because enforcement can deny tool calls.

## Inspection workflow

1. Locate this plugin's installed data directory from Codex plugin diagnostics.
2. Read `config.json` if present, otherwise report the versioned defaults.
3. Summarize `events.jsonl` by event type, tool name, and time. Never expose or
   attempt to reverse HMAC values.
4. Treat `repeat_failure_candidate` as an observation, not proof that the task
   made no semantic progress.
5. Recommend enforcement only when candidates show exact repeated explicit
   failures and no legitimate polling workflow is being caught.

## Enforce mode

When explicitly approved, create `PLUGIN_DATA/config.json` with:

```json
{
  "mode": "enforce"
}
```

In enforce mode, the third identical explicit failure adds private model-facing
context. A fourth identical attempt is denied before execution, and Codex is
asked to diagnose the existing error and choose a different strategy. Other
tool calls continue normally.
