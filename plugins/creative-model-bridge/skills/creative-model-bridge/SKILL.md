---
name: creative-model-bridge
description: Route fiction, scripts, poetry, story development, rewrites, revision, and every other creative writing task through the active provider's OpenAI-compatible Chat Completions streaming API. Use the fixed model fallback order and return only final visible text.
---

# Creative Model Bridge

Use this skill for every creative writing task. Send the work to an external
model; do not silently draft or revise the requested creative text locally when
the external call fails. Treat this package as instructions only: do not look
for a bundled runtime, script, MCP server, or Pixi environment.

## Prepare the request

1. Preserve the user's language, point of view, tense, format, named constraints,
   and supplied material.
2. Build one stateless prompt in this order: task, constraints, output
   specification, inline material, then file material. Read required files with
   available tools and wrap each one in explicit begin/end markers.
3. Use this system instruction unless the user explicitly requests no system
   instruction:

   ```text
   你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。
   ```

4. Request up to 60,000 output tokens only when the current compatible provider
   documents the relevant Chat Completions field. Do not silently discard source
   material to satisfy a client default.

## Reuse the current provider

Reuse the current Codex model provider's effective `base_url`, provider API key,
and headers. Prefer provider information already exposed by the runtime. If it
is not directly available, resolve the active user-level configuration and
profile without changing them.

Resolve credentials only from the selected provider. Use its command-backed
`auth`, `env_key`, or `experimental_bearer_token`, plus `http_headers` and
`env_http_headers`. Treat a configured provider bearer token as an API key and
send it as `Authorization: Bearer <key>` unless the provider headers explicitly
define another scheme. Do not use a key belonging to another provider.

Do not read `auth.json`, extract a Codex or ChatGPT login token, or synthesize a
`ChatGPT-Account-Id` header. `requires_openai_auth` describes Codex runtime
authentication; it is not a provider API-key source for this skill. If the
selected provider has no API-key source, stop at that boundary instead of using
the Codex login session.

Keep every credential in process memory. Never print it, return it, place its
literal value in a tool call or command argument, write it to a file, create a
new `.env`, or include it in diagnostics. Reference an existing environment
variable by name where possible. Never ask the user to paste a key into chat.

Send credentials only to the exact origin of the configured provider. Do not
infer a model vendor from the model name and forward the credential to another
host. Disable automatic redirects for credential-bearing requests; reject a
redirect unless the caller can prove that the resolved target has the same
scheme, host, and effective port as the configured provider. Never forward an
authorization or provider header across origins. Do not edit `AGENTS.md`, Codex
configuration, credential storage, or the marketplace as part of a writing
request.

## Call Chat Completions with curl

Use only the OpenAI-compatible Chat Completions streaming API. Ignore
`wire_api` and do not call Responses, Anthropic Messages, Gemini native APIs,
SDKs, provider CLIs, or a locally installed runtime.

Use the system `curl` command on macOS/Linux and `curl.exe` on Windows. It is
available without adding a package dependency. Send one `POST /chat/completions`
request with this JSON shape:

```json
{
  "model": "<candidate>",
  "messages": [
    {"role": "system", "content": "<system instruction>"},
    {"role": "user", "content": "<prepared prompt>"}
  ],
  "stream": true
}
```

Pass the JSON body and credential/header to `curl` through the current shell's
anonymous pipe or file descriptor, not a command argument or persistent file.
Set `Content-Type: application/json`, disable redirects, keep SSE buffering
disabled, and send the request only to `<base_url>/chat/completions`. Do not
install a dependency, create a helper, or start a daemon.

## Select and fall back between models

The supported candidates, in exact fallback order, are:

1. `gemini-3-pro`
2. `gemini-3-flash`
3. `deepseek-flash`
4. `deepseek-pro`
5. `gpt-5.6-terra`
6. `gpt-5.6-sol`
7. `gpt-5.6-luna`

Always begin with `gemini-3-pro` and advance through this list one candidate at
a time. This order is fixed; do not skip, reorder, or call a model outside it.
If the user explicitly requests a model outside this list, report that it is
unsupported instead of silently substituting another model.

Try the next candidate only when the previous request is conclusively rejected
before any SSE output, such as a structured invalid-model or unsupported-model
response or a model-specific 400/404/422 response. Keep the same provider
origin, credentials, headers, prompt, and API shape for every candidate. A
local caller, configuration, or credential failure is not model-specific; stop
instead of cycling through the list.

Do not fall back after 401 or 403 authentication failures, 429 rate limits,
policy denials, timeouts, connection failures after sending the request, 5xx
responses, partial output, malformed or interrupted streams, or any SSE delta.
Those outcomes may represent an accepted generation and a retry could duplicate
content or charges. Report the exact safe failure boundary if no eligible
candidate remains.

## Extract the final visible text

Parse the Server-Sent Events line by line. For each JSON `data:` event, append
only a string value from `choices[].delta.content`, in event order. Skip null,
missing, or empty content values and ignore every other delta field, including
`reasoning_content`, `reasoning`, `thinking`, role, tool calls, usage, and
metadata. The models currently tested do not all emit the same thinking fields,
but their final visible text is consistently carried by `choices[].delta.content`.

Accept a completed generation only after a non-null `finish_reason` and the
terminal `data: [DONE]` event. Do not treat a usage-only chunk as text. If the
stream ends early or has no final completion marker, do not return partial text
or retry it with another model.

Return the concatenated visible text verbatim without a preface, critique,
translation, summary, or formatting wrapper unless the user explicitly requests
one. Never expose raw reasoning, reasoning deltas, internal metadata, or
credentials.
