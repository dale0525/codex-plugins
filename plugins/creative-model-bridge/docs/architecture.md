# Architecture

The plugin has one runtime: `scripts/creative_model_bridge.py`. A process reads
one UTF-8 JSON object from stdin, validates it, builds one deterministic Chat
Completions body, performs one HTTP POST, parses the SSE stream, and writes one
JSON object to stdout.

```text
stdin JSON
   │
   ├─ config.toml → selected provider, opaque default, credential
   ├─ ordered context text/files → bounded prompt with file markers
   ▼
one POST /chat/completions (stream=true)
   ▼
SSE choices[0].delta.reasoning_content / reasoning → reasoning
SSE choices[0].delta.content                         → output
   ▼
stdout JSON {reasoning, output, usage, request_id, model, provider}
```

There is no protocol envelope, readiness frame, bounded NDJSON stream, MCP
server, daemon, profile, cache, launcher, model listing, preview route, retry,
provider switch, or hidden conversation state. The process exits non-zero for
validation, HTTP, malformed-stream, or provider errors and emits a single safe
JSON error object with empty `reasoning` and `output` fields.

## Prompt boundary

Sections are ordered `task`, `constraints`, `output_spec`, `context_text`, then
`context_files`. Inline blocks accept strings or `{label, text}` objects. Files
must be absolute regular non-symlink paths and are decoded deterministically;
each file is at most 2 MiB, total decoded files and the assembled prompt are at
most 180,000 characters. File content is enclosed by explicit begin/end
markers, preserving request order and exact decoded text.

## Provider boundary

`base_url` is validated as an absolute HTTP(S) URL with no credentials, query,
or fragment; `/chat/completions` is appended once. Bearer selection is the
configured `env_key`, then `CREATIVE_MODEL_API_KEY`, then the development-only
`experimental_bearer_token` when no `env_key` is configured. No credential is
written into the body or result. Redirects, retries, and response-body error
echoes are refused.
