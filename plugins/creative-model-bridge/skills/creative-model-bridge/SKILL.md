---
name: creative-model-bridge
description: Use the Creative Model Bridge MCP tools for fiction, scripts, poetry, story development, revision, and every other creative writing task while preserving supplied material and returning generated text verbatim.
---

# Creative Model Bridge

Use this skill for every creative writing task: drafts, scenes, scripts, poetry,
story development, critique-assisted revision, rewrites, and format changes.
MCP use is unconditional for these tasks; do not decide that a task is too
small or too ordinary to use the bridge.

## Operating rules

1. Use only the provisioned global MCP server `creative-model-bridge`.
   Verify that all three tools (`creative_models`, `creative_preview`, and
   `creative_generate`) are visible before starting. Never use another server,
   a direct HTTP/API client, a `creative_text` profile, or a second provider
   adapter. If any of the three tools is missing, fail closed: do not draft,
   call another server, or silently continue. Ask the user to provision the
   global server and then restart Codex or start a new task. On POSIX run
   `scripts/bootstrap.sh setup --yes`; on Windows run
   `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/provision.ps1 setup --yes`.
2. Preserve the user's material, language, point of view, tense, format, and
   named constraints. Put source text in labeled `context_text` blocks and
   source files in ordered absolute `context_files` paths.
3. If the user names a model, pass that model string exactly. Model names are
   opaque identifiers: do not normalize, validate against a private list, or
   silently select another model. If no model is named, allow the bridge to use
   the configured default.
4. Preview with `creative_preview` when the user asks to inspect a prompt, when
   the material is sensitive, or before a consequential outbound request. A
   preview never makes a network call and reports file hashes and decoded
   character counts.
5. Generate with `creative_generate` only after the request is complete. The
   bridge is stateless: every revision must include the material and
   instructions needed for that revision. It does not carry hidden session
   context, retry on failure, or switch providers/models.
6. Return the `text` field verbatim by default. Do not add a preface, critique,
   translation, summary, or formatting wrapper unless the user explicitly
   requests one.
7. Compare models or drafts only when the user explicitly asks for a
   comparison. Otherwise make one generation with the requested model.
8. Do not edit global `AGENTS.md`, global Codex configuration, or provider
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
