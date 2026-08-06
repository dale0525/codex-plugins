# Configuration reference

The bundled CLI reads `config.toml` with Python `tomllib`. Path precedence is:
explicit `config_path` (embedding/tests), then a non-empty `CODEX_HOME`, then
the platform default `Path.home()/.codex` (Windows: `%USERPROFILE%\\.codex`).

| Key | Required | Meaning |
| --- | --- | --- |
| `shell_environment_policy.set.CREATIVE_MODEL_PROVIDER` | yes | Exact key under `model_providers` to use |
| `shell_environment_policy.set.CREATIVE_MODEL_DEFAULT` | for omitted request model | Opaque default model identifier |
| `model_providers.<provider>.base_url` | yes | OpenAI-compatible API root; bridge posts to `/chat/completions` |
| `model_providers.<provider>.wire_api` | yes | `responses` or `chat_completions`; both use Chat Completions |
| `model_providers.<provider>.env_key` | no | Preferred provider environment variable |
| `CREATIVE_MODEL_API_KEY` | host channel | Fixed credential environment variable |
| `model_providers.<provider>.experimental_bearer_token` | no | Development-only fallback when no `env_key` is configured |

An explicit `model` wins over the configured default byte-for-byte. The bridge
never guesses a model from `creative_models`; that operation reports only the
provider's current `/models` response.

Invoke the normal route with `scripts/bootstrap.sh run` on POSIX or
`powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/provision.ps1 run`
on Windows. The development-only `CREATIVE_MODEL_BRIDGE_BIN` override bypasses
downloads, and `CREATIVE_MODEL_BRIDGE_OFFLINE=1` permits a verified cache only.

The launcher also exposes non-interactive `cache` and `install` actions. `cache`
only verifies or warms the current-version v4 cache and never reads stdin or
starts the CLI. `install` is the `.codex-sync/provision.json` hook: after
ensuring the cache it invokes `migrate --codex-home <resolved CODEX_HOME>`;
missing historical state is a successful no-op. For these two actions a local
`CREATIVE_MODEL_BRIDGE_BIN` is hashed and published into the normal immutable
cache layout before migration; `run` and explicit `migrate` retain direct
override execution.

The request envelope and result frames are protocol v1 NDJSON. Keep material
and credentials on stdin/environment, never argv or temporary plaintext files.
The caller validates all sequence, size, and SHA-256 fields before parsing.

An explicit `SSL_CERT_FILE` (or `CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE`) must be
an absolute, readable, non-empty regular file; absent overrides use urllib's
platform trust store.

## Historical migration

The `install` hook automatically runs the migration when this machine has the
old managed global MCP entry; an explicit `migrate` action is also available.
The migration requires matching marker/install ID/state,
backs up the original bytes under
`$CODEX_HOME/creative-model-bridge/migration-backups/`, and atomically removes
only the CMB table, marker lines, and pre-v4 active pointer. It preserves all
other MCP entries, credentials, and current v4 runtime objects. Any ambiguity,
foreign same-name entry, or external edit fails closed with the backup intact.
