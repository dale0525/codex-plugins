---
name: creative-model-bridge
description: Use the bundled Creative Model Bridge one-shot Python script for fiction, scripts, poetry, story development, revision, and every other creative writing task while preserving supplied material and returning generated output verbatim.
---

# Creative Model Bridge

Use this skill for every creative writing task: drafts, scenes, scripts, poetry,
story development, critique-assisted revision, rewrites, and format changes.
The bridge is unconditional for these tasks; do not silently replace it with a
local draft or another provider client.

## Invocation

Locate the plugin root containing this skill and run the bundled script through
Pixi. Keep the request JSON on stdin, never in argv, shell history, temporary
files, or stderr:

```text
pixi run --manifest-path <plugin-root>/pixi.toml run
```

The command is a one-shot process: provide the complete UTF-8 JSON request and
close stdin (EOF) in the same invocation. Prefer a non-interactive pipe or
heredoc; do not type JSON into a live PTY and leave it open, because the script
uses `stdin.read()` and will correctly wait for EOF before making the network
request. For example:

```bash
pixi run --manifest-path <plugin-root>/pixi.toml run <<'JSON'
{"task":"Revise the supplied scene.","context_text":["..."]}
JSON
```

The script reads one JSON object and emits one JSON object. Successful output
always has `reasoning` and `output`; return `output` verbatim. If the process
exits non-zero, surface its safe `error` rather than retrying or drafting a
replacement. Do not send more than one request per process.

If a process produces no output, first verify that stdin reached EOF. A process
waiting before any provider request is usually an input/EOF problem; a process
that has reached the provider is bounded by the bridge's HTTP timeout. Do not
retry until the first process has exited.

## Request rules

Preserve the user's material, language, point of view, tense, format, and named
constraints. Use ordered `context_text` entries for inline material and ordered
absolute `context_files` paths for source files. The bridge places task,
constraints, output specification, inline context, and file context in that
order, with explicit begin/end markers around every file.

If the user names a model, pass that model string exactly. Otherwise allow the
built-in `gemini-3-pro` default. Any supplied `max_tokens` or
`max_output_tokens` value is ignored; the bridge always sends 60,000. Use
`system_mode: "none"` only when the user explicitly
wants no system instruction; the default `minimal` mode uses only the documented
minimal Chinese writing instruction. Keep every revision stateless by supplying
all required material again.

Do not add a preface, critique, translation, summary, or formatting wrapper to
the returned output unless the user explicitly requests one. Do not edit global
`AGENTS.md`, Codex configuration, or provider credentials as part of a writing
request.

## Operational boundary

The script makes exactly one `POST /chat/completions` request with `stream: true`
and parses SSE frames. Reasoning deltas and output deltas are kept separate;
usage is retained when present. It does not list models, preview payloads,
maintain a conversation, retry, switch providers/models, download binaries, or
run an MCP server.
