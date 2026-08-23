# Provider Chat Completions

`provider-chat-completions` is a small, one-shot utility for calling the
effective Codex model provider's OpenAI-compatible `POST /chat/completions`
endpoint.

The caller supplies a model and an ordered `messages` array. The utility resolves
the effective provider configuration for the current working directory, uses
only that provider's configured credential and headers, makes one non-streaming
request, and returns a normalized JSON result.

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

On Windows, use `scripts/run.ps1` with the same JSON input. The process writes
one JSON object to stdout and exits non-zero on failure. Credentials and
provider response bodies never appear in stdout or diagnostics. The launchers
require Python 3.8 or newer and return `python_unavailable` when it is absent.

When reading the effective Codex provider on Windows, the utility first uses
`PROVIDER_CHAT_CODEX_BIN` when it is set. Otherwise it checks the directly
launchable helper at
`%CODEX_HOME%\plugins\.plugin-appserver\codex.exe`, or
`%USERPROFILE%\.codex\plugins\.plugin-appserver\codex.exe` when `CODEX_HOME`
is not set. It never falls back to a PATH-resolved `codex` on Windows, avoiding
the App Execution Alias and working-directory executable search. If the helper
is absent, the result is `codex_unavailable`. Set the variable to an absolute
helper path when the desktop installation uses a different location; relative
overrides are rejected as `codex_bin_not_absolute` on every platform.

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
is currently rejected so the result stays deterministic and portable.

## Result

Success returns `ok`, `model`, `content`, `finish_reason`, and optional `usage`.
Failure returns only a safe `stage`, `code`, optional HTTP status, and whether a
retry could be meaningful. The utility never retries automatically.

Configuration-launch failures may also include a bounded, redacted `diagnostic`
object with the executable path, Win32/OS error number, process exit code, and
stderr presence/byte-count metadata. Raw stderr text is never returned. The
diagnostic never includes credentials, authorization headers, tokens, or the
provider response body. A denied Windows launch is reported as
`stage=config`, `code=codex_launch_denied`, `retryable=false`; no provider
request is attempted in that case.

The provider is resolved through Codex's `config/read` app-server method. Only
the effective provider's `base_url`, `env_key`,
`experimental_bearer_token`, `http_headers`, `env_http_headers`, and query
parameters are considered. Codex login/session credentials are never used.
