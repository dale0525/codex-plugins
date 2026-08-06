# Creative Model Bridge

Creative Model Bridge exposes three stateless operations through a bundled,
one-shot stdin/stdout CLI backed by the OpenAI Chat Completions API shape:

- `creative_models` calls the configured provider's `/models` endpoint.
- `creative_preview` validates files and builds the exact outbound payload
  without network access.
- `creative_generate` makes one `/chat/completions` streaming request and
  returns generated text verbatim with usage, request ID, and a prompt report.

## Runtime and migration

The launcher (currently `v0.2.0`) downloads a versioned, self-contained PyInstaller executable,
verifies its SHA-256 entry, and atomically caches it at
`$CODEX_HOME/creative-model-bridge/runtime/v<version>/objects/<target>/<sha256>/<generation>/`.
Cached starts perform no network access and do not modify global Codex
configuration. Target machines need neither Git, Pixi, Python, nor PowerShell
7; native Windows PowerShell 5.1 is sufficient.

The non-interactive `cache` action only verifies or warms that current-version
cache and exits without reading stdin or starting the CLI. The normal plugin
provisioning hook is `install`: it performs the same cache step, then invokes
the cached executable's `migrate --codex-home <resolved CODEX_HOME>` command.
When no historical state exists, migration reports success. A local
`CREATIVE_MODEL_BRIDGE_BIN` override is direct for `run`/`migrate`, but for
`cache`/`install` it is copied into the same verified v4 object and active
pointer layout before use.

Invoke the one-shot route with `scripts/bootstrap.sh run` on POSIX or:

```text
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/provision.ps1 run
```

The process emits one protocol-v1 `ready` frame, a response metadata frame,
and bounded `data` frames. Each data frame has a contiguous `seq`,
`chunk_sha256`, overall `sha256`, and `done`; callers must validate all bytes
before parsing the reconstructed JSON. Keep the request JSON on stdin, never in
argv, shell history, temporary files, or stderr. A yielded Codex exec session
may be continued with `write_stdin` for cold starts over ten seconds or
operations over sixty seconds.

For a machine that previously used an owned global MCP entry, the `install`
hook invokes the one-time migration automatically; the explicit `migrate`
action remains available for a direct migration run. It validates the historical marker, install ID,
runtime command, and `CODEX_HOME` before atomically removing only that entry
and pre-v4 active pointer. A byte-for-byte backup is retained under
`$CODEX_HOME/creative-model-bridge/migration-backups/`; unrelated MCP tables,
credentials, and current v4 cache objects are preserved. Ambiguous ownership or
an external edit fails closed and leaves the original files untouched.

## Configuration

The bridge reads `config.toml` with `tomllib`. It first honors an explicit
configuration path (embedding/tests), then a non-empty `$CODEX_HOME`, and
otherwise uses the platform default `Path.home()/.codex` (on Windows,
`%USERPROFILE%\\.codex`). The provider name and default model are selected from:

```toml
[shell_environment_policy.set]
CREATIVE_MODEL_PROVIDER = "my-provider"
CREATIVE_MODEL_DEFAULT = "my-opaque-model"

[model_providers.my-provider]
base_url = "https://provider.example/v1"
wire_api = "responses" # "responses" or "chat_completions"; bridge uses /chat/completions
env_key = "MY_PROVIDER_API_KEY"
# experimental_bearer_token = "development-only-value"
```

`wire_api` may be `"responses"` or `"chat_completions"`; the bridge always uses
the provider's `/chat/completions` endpoint. An explicitly supplied request
model overrides `CREATIVE_MODEL_DEFAULT` exactly; no model auto-selection or
adapter is performed. Credential precedence is: the configured `env_key`, then
`CREATIVE_MODEL_API_KEY`, and finally `experimental_bearer_token` only when no
`env_key` is configured. Credentials never appear in results, errors, or
protocol frames.

## Materials and preview

`context_text` accepts ordered labeled blocks (`label` and `text`).
`context_files` accepts ordered absolute paths to regular text files only. Each
file is limited to 2 MiB and total decoded file context to 180,000 characters.
UTF-8, BOM UTF-16, and supported East Asian legacy encodings are detected
strictly; binary signatures and ambiguous byte streams are rejected. No file is
truncated or summarized. The assembled user prompt is also limited to 180,000
characters. `prompt_report` records each resolved path, decoded character
count, encoding, and raw-byte SHA-256 digest.

## Outbound boundary

For the same arguments, `creative_preview.payload` is byte-for-byte equivalent
after JSON serialization to the body sent by `creative_generate`:

```json
{
  "model": "the-requested-or-configured-opaque-name",
  "messages": [
    {"role": "system", "content": "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"},
    {"role": "user", "content": "the deterministic user prompt"}
  ],
  "max_tokens": 60000,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

The system message is omitted for `system_mode: "none"`; `temperature` is
added only when supplied. The user prompt order is `task` → `constraints` →
`output_spec` → `context_text` → `context_files`. The bridge does not retry,
switch providers, add hidden prompts, or carry conversation state.

An explicit `SSL_CERT_FILE` (or `CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE`) is
validated as an absolute readable non-empty regular file before a provider
request. If absent, urllib's platform trust store is used. Release assets are
verified before the immutable cache pointer is published; integrity checking
is not an independent signing or provenance attestation.

## Install and test

```bash
codex plugin add creative-model-bridge@dale0525-codex-plugins
pixi run creative-model-bridge-test
pixi run test
pixi run validate
```

`CREATIVE_MODEL_BRIDGE_BIN` is an explicit executable override for tests and
development; it performs zero download. `CREATIVE_MODEL_BRIDGE_OFFLINE=1`
requires an already verified cache. Focused tests use an in-process mock HTTP
opener, never make a live provider request, and contain no credentials. The
Windows matrix is the cross-platform validation boundary for this checkout.
