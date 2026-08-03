# Configuration reference

The plugin reads `config.toml` using Python `tomllib`. Path precedence is:
explicit `config_path` (embedding/tests), then a non-empty `CODEX_HOME`, then
the platform default `Path.home()/.codex` (Windows: `%USERPROFILE%\.codex`).

| Key | Required | Meaning |
| --- | --- | --- |
| `shell_environment_policy.set.CREATIVE_MODEL_PROVIDER` | yes | Exact key under `model_providers` to use |
| `shell_environment_policy.set.CREATIVE_MODEL_DEFAULT` | for omitted request model | Opaque default model identifier |
| `model_providers.<provider>.base_url` | yes | OpenAI-compatible API root; bridge posts to `/chat/completions` |
| `model_providers.<provider>.wire_api` | yes | `responses` or `chat_completions`; bridge uses the Chat Completions endpoint for both |
| `model_providers.<provider>.env_key` | no | Preferred provider environment variable; if unavailable, the fixed `CREATIVE_MODEL_API_KEY` channel is used |
| `CREATIVE_MODEL_API_KEY` | host channel | Fixed environment variable forwarded by the provisioned MCP entry for provider credentials |
| `model_providers.<provider>.experimental_bearer_token` | no | Development-only credential, used only without `env_key` and the fixed channel |

An explicit `model` in `creative_preview` or `creative_generate` wins over the
configured default byte-for-byte. The bridge never guesses a model from
`creative_models`; that tool reports only the provider's current `/models`
response.

The bundled declaration forwards `CODEX_HOME`, `CREATIVE_MODEL_API_KEY`, the
explicit CA channels, and the offline/runtime override channels. The
development-only `CREATIVE_MODEL_BRIDGE_BIN` override bypasses downloads, and
`CREATIVE_MODEL_BRIDGE_OFFLINE=1` permits a cached version/target asset only.
The default release version is 0.1.14. The binary's `provision setup`,
`status`, `repair`, and `uninstall` commands own the global MCP entry
transactionally.

The target-machine baseline is `curl` plus either `sha256sum` or `shasum` on
POSIX, and native Windows PowerShell 5.1 (`Invoke-WebRequest` and
`Get-FileHash`) on Windows. Git, Python, Pixi, and PowerShell 7 are not
required. The launchers re-verify the release checksum and asset on every
cached start; this is integrity evidence rather than independent signing or
provenance.

Provision lifecycle state is schema 2. `status` reports `absent`, `installed`,
`uninstalled`, `drift`, `foreign`, or `pending_manual_recovery`; the latter
means a retained WAL could not safely reconcile an external edit.

At setup time `SSL_CERT_FILE` (or `CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE`) may
select an absolute, readable, non-empty regular CA bundle. macOS deterministically
uses `/etc/ssl/cert.pem`; Linux checks the ordered candidates
`/etc/ssl/certs/ca-certificates.crt`, `/etc/pki/tls/certs/ca-bundle.crt`,
`/etc/ssl/ca-bundle.pem`, `/etc/pki/tls/cacert.pem`,
`/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem`, then `/etc/ssl/cert.pem`.
Windows leaves `SSL_CERT_FILE` out unless an explicit override is supplied.
The selected value is appended to `env_vars` after the credential entries and
stored as optional `ssl_cert_file` state. A missing configured file is reported
as drift, but does not prevent owned-block uninstall. A consistent 0.1.5
through 0.1.13 state is upgraded to 0.1.14 under the same byte-exact
WAL transaction. The ownership parser removes only canonical CMB table and
marker line spans, and accepts begin-only markers only when matching legacy
state proves the command, home, provider environment, and CA values.

The bundled stdio server runs this same CA resolver before constructing the
bridge or making any provider request. If `SSL_CERT_FILE` is absent on POSIX,
the selected system bundle is assigned to it; Windows keeps native trust by
default. An explicit `SSL_CERT_FILE` is validated and preserved unless the
plugin-specific alias is also set, in which case the alias has documented
precedence and becomes the effective `SSL_CERT_FILE` used by urllib.

Release retries reconcile state before mutating: absent creates a draft,
existing drafts may be completed or clobbered only while still draft, unknown
extra assets are hard failures, and an exact published release is verified
read-only. A published mismatch fails without mutation.
