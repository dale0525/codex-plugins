---
name: creative-model-bridge
description: Route fiction, scripts, poetry, story development, rewrites, revision, and every other creative writing task through an external LLM using the current Codex model provider's existing base URL and provider API key. Use when creative work must preserve supplied material and return the model's output verbatim; honor an explicit wire API before safe protocol fallbacks.
---

# Creative Model Bridge

Use this skill for every creative writing task. Send the work to an external
model; do not silently draft or revise the requested creative text locally when
the external call fails. Treat this package as instructions only: do not look
for a bundled runtime, script, MCP server, or Pixi environment.

## Prepare the request

1. Preserve the user's language, point of view, tense, format, named constraints,
   and supplied material.
2. Use the user's model string exactly when one is named. Otherwise use
   `gemini-3-pro`.
3. Build one stateless prompt in this order: task, constraints, output
   specification, inline material, then file material. Read required files with
   available tools and wrap each one in explicit begin/end markers.
4. Use this system instruction unless the user explicitly requests no system
   instruction:

   ```text
   你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。
   ```

5. Request up to 60,000 output tokens where the selected protocol and model
   support it. Adapt only the protocol-specific field name; do not silently
   discard source material to satisfy a client default.

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

## Choose an available caller

Use a safe system-provided mechanism that is already available. Prefer Node
`fetch` for direct HTTP because it keeps the key in process memory and has broad
compatibility with OpenAI-compatible gateways. Next prefer a provider-aware
client or tool, an installed SDK or provider CLI, or `curl` with both body and
credential supplied through stdin. Avoid Python `urllib` when Node is available;
some compatible gateways reject its transport fingerprint with a misleading
403. Do not put a credential in a command argument, install dependencies, create
a persistent helper, or start a daemon.

Choose the wire format before sending anything. A failed or possibly accepted
generation must not be repeated merely to change transport.

## Try protocols safely

1. Honor an explicit provider wire format first. For `wire_api = "responses"`,
   call `POST /responses`; do not probe Chat Completions first. Send `model`,
   `instructions`, and `input` as an ordered message array whose text parts use
   `input_text`. Start with the Codex-compatible transport, `stream: true`, and
   omit optional token-limit fields unless this provider is known to support
   them. Parse SSE events, concatenate only `response.output_text.delta`, and
   accept the result only after `response.completed`. Treat `response.failed`,
   `response.incomplete`, an error event, or a stream ending without completion
   as failure with no retry.
2. For a provider that explicitly declares Chat Completions, call
   `POST /chat/completions` first with ordered `messages`. Prefer non-streaming
   JSON unless that provider is known to require SSE.
3. When `wire_api` is absent or unknown, try OpenAI-compatible Chat Completions
   first, then Responses. Switch only after a clear endpoint or request-schema
   incompatibility before generation, such as 404, 405, 415, or an explicit
   unsupported-field response.
4. If both OpenAI formats are clearly incompatible, try a provider-native format
   only when the provider is confidently identified and the call remains inside
   the same configured origin and authentication boundary. Eligible formats
   include Anthropic Messages and Gemini `generateContent`; an existing provider
   CLI or SDK may perform this adaptation.

Do not change formats after 401 or 403 authentication failures, 429 rate limits,
policy denials, transport timeouts, partial output, malformed or interrupted
streams, or a 5xx response that may have followed request acceptance. Do not
retry an ambiguous request. These stops prevent duplicate generations and
charges.

If no safe caller can access the current provider credential, or every eligible
format fails, report the exact safe failure boundary. Do not expose response
bodies that may contain secrets, and do not draft a replacement locally.

## Return the result

Extract only the model's final visible content. Do not expose raw reasoning,
reasoning deltas, internal metadata, or credentials. Return the generated output
verbatim without a preface, critique, translation, summary, or formatting wrapper
unless the user explicitly requests one.
