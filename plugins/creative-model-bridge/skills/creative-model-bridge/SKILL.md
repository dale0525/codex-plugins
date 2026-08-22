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
is not directly available, use Codex's own cross-platform effective-configuration
resolver (for example, the app-server `config/read` endpoint) to resolve the
active user-level configuration and profile without changing them.

Do not parse `config.toml` with the system `python3`, Ruby, PowerShell,
`plutil`, `awk`, or another OS utility, and do not assume any of them provides a
portable TOML parser. TOML is not a universally OS-native format, and macOS
system Python may be older than 3.11. Do not install a parser or create a
helper. Configuration resolution is a single preflight before selecting the
first model. If Codex's effective-config resolver is unavailable or fails, stop
at that configuration boundary with a local diagnostic; this is not a model
attempt and must not be repeated as fallback across candidates.

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
a time. This order is fixed; do not skip, reorder, retry a candidate, or call a
model outside it. If the user explicitly requests a model outside this list,
report that it is unsupported instead of silently substituting another model.

Treat an attempt as successful only when all of these conditions hold:

- the HTTP response is 2xx and `curl` exits successfully;
- no error, refusal, safety, policy, or blocked signal appears anywhere in the
  response or stream;
- the stream has a normal text-completion `finish_reason` (normally `stop`, or
  an explicitly documented provider-equivalent; reject `length`,
  `content_filter`, `tool_calls`, `function_call`, safety/refusal values, and
  unknown values);
- the terminal `data: [DONE]` event arrives; and
- the concatenated `choices[].delta.content` is non-whitespace text that meets
  the user's explicit output format and is not merely an error, refusal, or
  status message.

Treat every other model-attempt outcome as an unsuccessful attempt and try the
next candidate in the exact order until the list is exhausted. This includes
model-specific rejections, 401/403,
429, policy denials, non-2xx responses, nonzero `curl` exits, timeouts,
connection failures, 5xx responses, malformed or interrupted streams, partial
output, unsupported or non-normal finish reasons, explicit error/refusal/safety
signals, whitespace-only text, and streams that reach `finish_reason` and
`data: [DONE]` but contain no usable `choices[].delta.content` text. A transport
or protocol error remains a failure even if buffered text and `[DONE]` are also
present.

For every fallback, keep the same provider origin, credentials, headers, prompt,
and API shape. Discard all visible text from an unsuccessful attempt; never
return it as a partial result. After every candidate is unsuccessful, report the
failure boundary without exposing credentials, raw reasoning, or internal
metadata. Do not fallback after an explicit user cancellation or interrupt;
that is a user stop, not a model failure.

## Extract the final visible text

Parse the Server-Sent Events line by line. For each JSON `data:` event, append
only a string value from `choices[].delta.content`, in event order. Skip null,
missing, or empty content values and ignore every other delta field, including
`reasoning_content`, `reasoning`, `thinking`, role, tool calls, usage, and
metadata. The models currently tested do not all emit the same thinking fields,
but their final visible text is consistently carried by `choices[].delta.content`.

Accept a completed generation only when the success conditions above are all
met. In particular, require non-whitespace visible text and a normal completion
reason; do not accept `length`, `content_filter`, `tool_calls`, `function_call`,
unknown finish reasons, refusal/error payloads, or a stream whose transport
failed. Do not treat a usage-only chunk as text. Treat an early end, missing
completion marker, malformed stream, or empty visible text as an unsuccessful
attempt and continue with the next candidate.

Return the concatenated visible text verbatim without a preface, critique,
translation, summary, or formatting wrapper unless the user explicitly requests
one. Never expose raw reasoning, reasoning deltas, internal metadata, or
credentials.
