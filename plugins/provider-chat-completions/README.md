# Provider Chat Completions

`provider-chat-completions` is a small, one-shot utility for calling the
provider credential cache prepared by Codex Sync and the corresponding
OpenAI-compatible `POST /chat/completions` endpoint.

The caller supplies a model and an ordered `messages` array. After a successful
Codex Sync pull, the utility reads the versioned local cache (or its stable
marketplace sibling when a plugin reinstall replaced the version directory) for
the provider endpoint, credential headers, and query parameters, makes one
non-streaming request, and returns a normalized JSON result.

It does not add a system prompt, assemble files, choose a model, retry, switch
providers, stream SSE, or judge the quality of the returned content. A caller
that needs creative-writing rules or model fallback owns those decisions.

## Invocation

Run the bundled one-shot program with a JSON request on stdin:

```sh
plugins/provider-chat-completions/scripts/run.sh <<'JSON'
{"model":"gemini-3-pro","messages":[{"role":"user","content":"Hello"}]}
JSON
```

For long responses, use capture mode so the tool display never receives the
full completion:

```sh
result_file="$(mktemp "${TMPDIR:-/tmp}/provider-chat-completions.XXXXXX.json")"
plugins/provider-chat-completions/scripts/run.sh \
  --output-file "$result_file" <<'JSON'
{"model":"gemini-3-pro","messages":[{"role":"user","content":"Hello"}]}
JSON
```

Capture mode writes the complete normalized result atomically with owner-only
permissions and prints only a small manifest containing `result_file`, status,
and size metadata. On Windows it removes inherited ACLs with `icacls` and fails
closed if that restriction cannot be applied. Read the file locally and do not
print its full contents to the tool output. Keep it until the caller has
finished validating the result.

On Windows, use `scripts/run.ps1` with the same JSON input and the same
`--output-file <absolute-path>` option. Without capture mode, the process writes
one normalized JSON object to stdout; with capture mode it writes a bounded
manifest and stores the full result at the requested path. Credentials and
provider response bodies never appear in diagnostics. The launchers
require Python 3.8 or newer and return `python_unavailable` when it is absent.

## Request

```json
{
  "model": "provider-model-name",
  "messages": [
    {"role": "system", "content": "Optional system message"},
    {"role": "user", "content": "The request"}
  ],
  "parameters": {
    "temperature": 0.7,
    "max_tokens": 2000
  },
  "timeout_seconds": 120
}
```

`model` and `messages` are required. `parameters` is copied into the request
body, except that the utility owns `model`, `messages`, and `stream`; streaming
is currently rejected so the result stays deterministic and portable. The
optional `--output-file` launcher argument must be an absolute path; it does
not change the request body or cause another provider call.

## Result

Without capture mode, success returns `ok`, `model`, `content`, and
`finish_reason`, with optional `usage` and `tool_calls` when the provider
returns them. Capture mode returns a bounded manifest with `ok`, `result_file`,
`bytes`, and small status metadata; the complete normalized result (including
`content`, `usage`, and `tool_calls` when present) is in `result_file`.
Failure always returns `ok: false`, `stage`, `code`, and `retryable`, with an
optional `http_status`. The utility never retries automatically.

The provider cache is written atomically by Codex Sync at both
`<CODEX_HOME>/plugins/cache/<marketplace>/provider-chat-completions/<version>/.codex-provider/credential.json`
and the stable sibling
`<CODEX_HOME>/plugins/cache/<marketplace>/.codex-provider/provider-chat-completions/credential.json`.
The CLI reads the versioned file first, then the stable sibling, without checking
POSIX modes or Windows ACLs. The cache is never part of the synchronized Git
repository and contains no raw `experimental_bearer_token` field. `env_key` and
`env_http_headers` remain environment references and are resolved only in the
plugin process. A missing, malformed, symlinked, or non-regular cache returns a
structured credential failure; the utility does not launch Codex app-server,
read `config.toml`, ask for a pasted credential, or fall back to a different provider. It also rejects
credential-bearing remote HTTP endpoints and credential-like query parameters
before networking; loopback HTTP remains available for an explicitly configured
local gateway.
