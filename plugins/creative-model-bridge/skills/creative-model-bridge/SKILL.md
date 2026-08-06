---
name: creative-model-bridge
description: Use the bundled Creative Model Bridge CLI for fiction, scripts, poetry, story development, revision, and every other creative writing task while preserving supplied material and returning generated text verbatim.
---

# Creative Model Bridge

Use this skill for every creative writing task: drafts, scenes, scripts, poetry,
story development, critique-assisted revision, rewrites, and format changes.
The bridge is unconditional for these tasks; do not decide that a task is too
small or too ordinary to use it.

## Operating rules

1. Use the plugin-bundled one-shot CLI. Do not look for MCP tool visibility,
   Codex profiles, `creative_text`, a direct HTTP/API client, or another
   provider adapter. The CLI is a versioned cached executable; it never edits
   global Codex configuration on the normal path. Locate the plugin cache root
   containing this skill and use its `scripts/bootstrap.sh` on POSIX, or
   `scripts/provision.ps1` with native PowerShell on Windows. An explicit
   `CREATIVE_MODEL_BRIDGE_BIN` executable override is allowed for local tests.
   Invoke the launcher with the `run` argument and keep the request JSON on
   stdin (never in argv, shell history, temporary files, or stderr). For an
   interactive session, `exec_command` must use `tty: true`; wait for a
   `ready` frame with `input_echo: false` while the session remains alive,
   then send the exact request envelope plus one newline with `write_stdin`.
   Do not type the request before readiness. A pipe invocation may send the
   same line through stdin directly; the runtime pre-reads it before emitting
   `ready` and labels that frame `input_mode: "pipe"`.

   ```text
   POSIX:   <plugin-root>/scripts/bootstrap.sh run
   Windows: powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
            -File <plugin-root>/scripts/provision.ps1 run
   ```

   The plugin metadata uses the non-interactive `install` action during normal
   provisioning. `install` warms/verifies the current v0.2.0 cache, then calls
   the cached executable's `migrate --codex-home <resolved CODEX_HOME>`; absent
   legacy state is success. `cache` only warms/verifies and exits without
   reading stdin or emitting a `ready` frame. Do not send creative requests to
   either action. For `cache`/`install`, a local executable override is first
   published into the immutable v4 object/active-pointer cache; `run` keeps its
   direct override path.

   The launcher may cold-start for more than ten seconds. Start it with
   `exec_command` in a session, wait for the `ready` NDJSON frame, then send the
   request envelope through `write_stdin`; continue polling the yielded session
   until the complete response arrives, including operations that take more
   than sixty seconds. The exec transcript may contain launcher diagnostics and
   protocol frames, but the request material itself must not be copied into
   argv, environment variables, temporary files, or stderr. Never replace a
   missing/invalid response with a local draft or a retry.
2. The stdin envelope is protocol v1:

   ```json
   {"protocol":1,"type":"request","id":"1","operation":"creative_preview","arguments":{"task":"..."}}
   ```

   The process emits one `ready` frame, a `response` metadata frame, and
   bounded `data` frames. Validate `protocol`/`v`, matching `id`, contiguous
   zero-based `seq`, the declared `chunks` and `bytes`, `done` only on the final
   chunk, each `chunk_sha256`, and the overall `sha256` before JSON parsing.
   Concatenate `data` exactly as received and parse it as one JSON value. A
   response with `ok: false` is a safe bridge error; surface it without adding
   provider details. Truncation, a missing `done`, a hash mismatch, or a
   malformed frame is a hard bridge failure and must not be retried.
3. Preserve the user's material, language, point of view, tense, format, and
   named constraints. Put source text in labeled `context_text` blocks and
   source files in ordered absolute `context_files` paths.
4. If the user names a model, pass that model string exactly. Model names are
   opaque identifiers: do not normalize, validate against a private list, or
   silently select another model. If no model is named, allow the bridge to use
   the configured default.
5. Preview with `creative_preview` when the user asks to inspect a prompt, when
   the material is sensitive, or before a consequential outbound request. A
   preview never makes a network call and reports file hashes and decoded
   character counts.
6. Generate with `creative_generate` only after the request is complete. The
   bridge is stateless: every revision must include the material and
   instructions needed for that revision. It does not carry hidden session
   context, retry on failure, or switch providers/models.
7. Return the `text` field verbatim by default. Do not add a preface, critique,
   translation, summary, or formatting wrapper unless the user explicitly
   requests one.
8. Compare models or drafts only when the user explicitly asks for a
   comparison. Otherwise make one generation with the requested model.
9. Do not edit global `AGENTS.md`, global Codex configuration, or provider
   credentials as part of a writing request.

## Tool selection

- `creative_models`: discover the provider's current `/models` response. It
  does not invent or cache a model list.
- `creative_preview`: validate materials and return the exact prompt payload;
  it must be used for a no-network audit.
- `creative_generate`: send one Chat Completions streaming request and return generated
  text, provider/model identity, usage, request ID, and the prompt report.

The bridge's system mode is `minimal` by default and uses only its documented
minimal Chinese writing instruction. Set `system_mode: "none"` only when the
user explicitly wants no system instruction. Do not add hidden instructions.
