# Creative Model Bridge

Creative Model Bridge exposes three stdio MCP tools backed by the OpenAI
Responses API shape:

- `creative_models` calls the configured provider's `/models` endpoint.
- `creative_preview` validates files and builds the exact outbound payload
  without network access.
- `creative_generate` makes one `/responses` request and returns generated text
  verbatim with usage, request ID, and a prompt report.

The plugin manifest includes a standard bundled MCP declaration in
`.mcp.json`. After the plugin is enabled, Codex can discover all three tools by
starting `scripts/bootstrap.sh serve` (the current bundled fixture is verified
on macOS). Its server ID is `creative-model-bridge-bundled`, deliberately
distinct from the legacy global `creative-model-bridge` provision ID so an
existing 0.1.5–0.1.10 entry cannot shadow the bundled 0.1.11 runtime. No global
MCP provisioning is required for that discovery path. The
platform provisioners remain available for users who want a global owner-marked
entry: run `scripts/bootstrap.sh setup --yes` on POSIX, or
`powershell.exe -ExecutionPolicy Bypass -File scripts/provision.ps1 setup --yes`
on Windows PowerShell 5.1. The launcher downloads a versioned, self-contained
PyInstaller executable, verifies its SHA-256 entry, and atomically caches it at
`$CODEX_HOME/creative-model-bridge/runtime/v<version>/objects/<target>/<sha256>/<generation>/`.
The executable transactionally writes the global `$CODEX_HOME/config.toml` MCP
entry only for that optional provisioning path. `provision status`, `repair`,
and `uninstall` are available from the same binary. Objects are immutable and
never garbage-collected, so a Windows binary in use is never overwritten or
deleted. Target machines need neither Git, Pixi, Python, nor PowerShell 7; only
native Windows PowerShell 5.1 is required.

Provision state schema 2 reports `absent`, `installed`, `uninstalled`,
`drift`, `foreign`, or `pending_manual_recovery`. A healthy setup or repair is
an exact no-op. Repair replaces only an owned MCP block and preserves outside
configuration edits; uninstall removes only that block and retains the
`uninstalled` state even when the rest of `config.toml` changes. A WAL with
before/after images is retained when an unknown external edit prevents safe
rollback.

Provisioning resolves TLS trust once, before writing the managed block. An
explicit `SSL_CERT_FILE` (or `CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE`) must be an
absolute, readable, non-empty regular file. macOS uses `/etc/ssl/cert.pem`;
Linux checks its fixed ordered system bundle candidates; Windows omits the
variable by default and keeps the native trust store. The selected path is
recorded in schema-2 state and in the owned MCP environment. If that bundle is
later removed, status reports drift while uninstall remains available.

An internally consistent 0.1.5 through 0.1.10 owned block/state is upgraded
transactionally to 0.1.11. The migration recognizes the one known
begin-only marker repair shape only when the legacy state, install ID, and both
canonical CMB table values match exactly. Complete markers may surround
unrelated tables; migration and uninstall remove only the two CMB table spans
and ownership marker lines, preserving every other table, comment, order, and
value byte-for-byte. Any external edit or malformed/ambiguous ownership marker
fails closed and leaves the WAL for recovery.

## Configuration

The bridge reads `config.toml` with `tomllib`. It first honors an explicit
configuration path, then a non-empty `$CODEX_HOME`, and otherwise uses the
platform default `Path.home()/.codex` (on Windows, `%USERPROFILE%\.codex`).
The provider name and
default model are selected from:

```toml
[shell_environment_policy.set]
CREATIVE_MODEL_PROVIDER = "my-provider"
CREATIVE_MODEL_DEFAULT = "my-opaque-model"

[model_providers.my-provider]
base_url = "https://provider.example/v1"
wire_api = "responses"
env_key = "MY_PROVIDER_API_KEY"
# experimental_bearer_token = "development-only-value"
```

`wire_api` must be exactly `"responses"`. An explicitly supplied request model
overrides `CREATIVE_MODEL_DEFAULT` exactly; no model auto-selection or adapter
is performed. A bundled stdio MCP cannot dynamically forward arbitrary
provider-specific environment names from its host, so the provisioned entry
forwards `CREATIVE_MODEL_API_KEY` and the selected provider's `env_key` when configured.
Credential precedence is: the configured `env_key`, then
`CREATIVE_MODEL_API_KEY`, and finally `experimental_bearer_token` only when no
`env_key` is configured. Credentials never appear in tool results or errors.

## Materials and preview

`context_text` accepts ordered labeled blocks (`label` and `text`).
`context_files` accepts ordered absolute paths to regular text files only. Each
file is limited to 2 MiB and the total decoded file context to 180,000
characters. UTF-8, BOM UTF-16, and supported East Asian legacy encodings are
detected strictly; binary signatures and ambiguous byte streams are rejected.
No file is truncated or summarized. The final assembled user prompt, including
all task, constraints, output specification, and context sections, is also
limited to 180,000 characters. The prompt report records each resolved path,
decoded character count, encoding, and raw-byte SHA-256 digest.

## Audit boundary

The preview shows what this plugin would send, but it cannot audit provider-side
CPA routing, logging, retention, moderation, or model internals. Review the
provider's policy separately before sending sensitive material. The bridge does
not retry, switch providers, or hide additional prompts.
Provider requests identify themselves honestly as
`User-Agent: creative-model-bridge/0.1.11` for transport compatibility; no
Codex-specific identity or session headers are sent.

## Install and test

```bash
codex plugin add creative-model-bridge@dale0525-codex-plugins
pixi run creative-model-bridge-test
pixi run test
pixi run validate
```

`CREATIVE_MODEL_BRIDGE_BIN` is an explicit executable override for tests and
development; a valid override performs zero network access. Set
`CREATIVE_MODEL_BRIDGE_OFFLINE=1` to require a cached executable (an uncached
offline start fails clearly). Downloaded assets and `checksums.txt` come from
the same GitHub release and therefore provide integrity checking, not an
independent supply-chain attestation. No `creative-model-bridge-v0.1.11`
release is claimed to exist until the workflow is run; before that tag, use the
override for local smoke tests.

The tag workflow is retry-safe: an absent tag creates a draft, a draft can be
completed or clobbered only after rechecking that it is still draft, unknown
extra assets fail, and an exact published release is a read-only no-op.

Focused tests use an in-process mock HTTP opener; they never make a live CPA
request and contain no credentials. The repository workflow builds and smoke
tests the direct binary on Linux, macOS, and Windows. This checkout has not run
a real Windows host locally; the Windows matrix job is the validation boundary.
