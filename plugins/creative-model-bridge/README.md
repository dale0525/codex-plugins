# Creative Model Bridge

Creative Model Bridge is a small, auditable one-shot bridge for creative
writing. It reads one JSON request from stdin, makes exactly one streaming
OpenAI-compatible `POST /chat/completions`, and writes one JSON result to
stdout. There is no MCP server, daemon, launcher, model discovery, preview
operation, retry, model switching, or Codex profile.

## Run

From this plugin directory:

```bash
pixi run run < request.json
```

`stdout` contains one compact JSON object. Successful results always include
`reasoning` and `output`; the output string is the provider text verbatim.
Failures include those same two empty fields plus a safe `error` string and exit
with status 1. Diagnostics never contain credentials or provider response
bodies.

## Request

The request is a JSON object with the required `task` string and these optional
fields:

```json
{
  "task": "Write the next scene.",
  "model": "provider/opaque-model-name",
  "system_mode": "minimal",
  "context_text": ["ordered source text", {"label": "notes", "text": "..."}],
  "context_files": ["/absolute/path/one.txt", "/absolute/path/two.txt"],
  "constraints": ["preserve tense"],
  "output_spec": {"format": "prose"},
  "temperature": 0.7,
  "max_tokens": 60000
}
```

`system_mode` is `minimal` by default and adds only the documented Chinese
writing instruction. `none` omits the system message. A supplied model is
passed byte-for-byte; otherwise `gemini-3-pro` is used. `max_tokens` and the
legacy `max_output_tokens` spelling are accepted for caller compatibility, but
their values are ignored and the outbound request always uses 60,000.

Prompt sections are deterministic and ordered as `task`, `constraints`,
`output_spec`, `context_text`, and `context_files`. Every file is wrapped in
`--- BEGIN FILE: path ---` and `--- END FILE: path ---` markers. File order is
preserved exactly.

Context files must be absolute, regular, non-symlink files. Each file is capped
at 2 MiB and decoded context at 180,000 characters. UTF-8 (including BOM),
UTF-16 with a BOM, and a small deterministic East-Asian legacy set
(`gb18030`, `big5`, `shift_jis`, `euc_jp`, `euc_kr`) are accepted. Legacy codecs
are used only when the decoded text has a strong printable linguistic signal;
binary signatures, control-heavy data, ambiguous bytes, directories, and
symlinks are rejected. The assembled prompt has the same 180,000-character cap.

## Configuration

The script reads `config.toml` from an explicit path when embedded, then
`$CODEX_HOME/config.toml`, otherwise `~/.codex/config.toml`:

```toml
[shell_environment_policy.set]
CREATIVE_MODEL_PROVIDER = "my-provider"

[model_providers.my-provider]
base_url = "https://provider.example/v1"
wire_api = "chat_completions" # "responses" is also accepted for compatibility
env_key = "MY_PROVIDER_API_KEY"
# experimental_bearer_token = "development-only-value"
```

Credentials are resolved in this order: the configured `env_key`, fixed
`CREATIVE_MODEL_API_KEY`, then `experimental_bearer_token` only when no
`env_key` is configured. A bearer token is sent only in the HTTP header.

## HTTP and streaming

The request body uses the Chat Completions shape with `stream: true`, the
requested model or the built-in `gemini-3-pro` default, deterministic messages,
`max_tokens: 60000`, and `stream_options.include_usage: true`. The response is
parsed as UTF-8 SSE. `data:` frames are accumulated independently from
`choices[0].delta.reasoning_content` (with `reasoning` as a compatible alias)
and `choices[0].delta.content`. A usage object is retained when present;
`[DONE]`, malformed JSON/UTF-8, error events, missing completion, and HTTP
errors fail once with no retry.

## Development checks

```bash
pixi run test
pixi run validate
pixi lock --check
```

All runtime code is in `scripts/creative_model_bridge.py` and uses only the
Python standard library.
