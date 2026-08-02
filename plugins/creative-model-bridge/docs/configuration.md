# Configuration reference

The plugin reads `config.toml` using Python `tomllib`. Path precedence is:
explicit `config_path` (embedding/tests), then a non-empty `CODEX_HOME`, then
the platform default `Path.home()/.codex` (Windows: `%USERPROFILE%\.codex`).

| Key | Required | Meaning |
| --- | --- | --- |
| `shell_environment_policy.set.CREATIVE_MODEL_PROVIDER` | yes | Exact key under `model_providers` to use |
| `shell_environment_policy.set.CREATIVE_MODEL_DEFAULT` | for omitted request model | Opaque default model identifier |
| `model_providers.<provider>.base_url` | yes | Responses-compatible API root |
| `model_providers.<provider>.wire_api` | yes | Must be exactly `responses` |
| `model_providers.<provider>.env_key` | no | Preferred provider environment variable; if unavailable, the fixed `CREATIVE_MODEL_API_KEY` channel is used |
| `CREATIVE_MODEL_API_KEY` | host channel | Fixed environment variable forwarded by the provisioned MCP entry for provider credentials |
| `model_providers.<provider>.experimental_bearer_token` | no | Development-only credential, used only without `env_key` and the fixed channel |

An explicit `model` in `creative_preview` or `creative_generate` wins over the
configured default byte-for-byte. The bridge never guesses a model from
`creative_models`; that tool reports only the provider's current `/models`
response.

The launcher forwards `CODEX_HOME` and `CREATIVE_MODEL_API_KEY`; the
development-only `CREATIVE_MODEL_BRIDGE_BIN` override bypasses downloads, and
`CREATIVE_MODEL_BRIDGE_OFFLINE=1` permits a cached version/target asset only.
The default release version is 0.1.3; the repository deliberately does not
claim that tag has been published. The binary's `provision setup`, `status`,
`repair`, and `uninstall` commands own the global MCP entry transactionally.

The target-machine baseline is `curl` plus either `sha256sum` or `shasum` on
POSIX, and native Windows PowerShell 5.1 (`Invoke-WebRequest` and
`Get-FileHash`) on Windows. Git, Python, Pixi, and PowerShell 7 are not
required. The launchers re-verify the release checksum and asset on every
cached start; this is integrity evidence rather than independent signing or
provenance.

Provision lifecycle state is schema 2. `status` reports `absent`, `installed`,
`uninstalled`, `drift`, `foreign`, or `pending_manual_recovery`; the latter
means a retained WAL could not safely reconcile an external edit.

Release retries reconcile state before mutating: absent creates a draft,
existing drafts may be completed or clobbered only while still draft, unknown
extra assets are hard failures, and an exact published release is verified
read-only. A published mismatch fails without mutation.
