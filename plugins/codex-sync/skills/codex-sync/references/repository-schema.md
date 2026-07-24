# Configuration repository schema

## Contents

- Required layout
- Root manifest
- Portable and device configuration
- Providers
- Marketplaces
- Plugins
- Ownership behavior

## Required layout

```text
codex-sync.toml
AGENTS.md
config/common.toml
devices/<device-id>.toml
marketplaces.toml
plugins.toml
providers.toml
```

Only `codex-sync.toml` and the referenced `AGENTS.md` are strictly required. Missing optional TOML files are treated as empty. All text must be UTF-8.

## Root manifest

```toml
schema_version = 1
agents = "AGENTS.md"
common_config = "config/common.toml"
devices = "devices"
marketplaces = "marketplaces.toml"
plugins = "plugins.toml"
providers = "providers.toml"
```

Every path must be relative and remain inside the repository. Parent traversal and absolute paths are rejected.

## Portable and device configuration

Put portable Codex keys in `config/common.toml`:

```toml
model = "gpt-5.6"
model_reasoning_effort = "high"
web_search = "cached"

[features]
apps = true
multi_agent = true
```

Put host-specific paths, hooks, notification commands, and local MCP commands in `devices/<device-id>.toml`. Device values override common values at the same key path.

The engine remembers every managed leaf key. On a later apply it removes keys that were previously managed but have disappeared from the repository, then applies the new common and device values. Unmanaged Codex tables such as project trust, hook trust state, desktop state, and marketplace revision metadata remain intact.

## Providers

Define providers under a `providers` table. Each child table becomes `model_providers.<name>` in Codex config:

```toml
[providers.company]
name = "Company API"
base_url = "https://api.example.com/v1"
wire_api = "responses"
env_key = "COMPANY_OPENAI_API_KEY"
requires_openai_auth = false
```

Prefer `env_key` or command-backed authentication. When a provider must carry a static token across devices, the provider file may explicitly store Codex's dev-only plaintext field:

```toml
[providers.company]
name = "Company API"
base_url = "https://api.example.com/v1"
wire_api = "responses"
experimental_bearer_token = "replace-with-the-real-token"
```

Only `providers.<name>.experimental_bearer_token` receives this exception. The engine still rejects `bearer_token`, access tokens, API keys, passwords, client secrets, refresh tokens, private keys, and secret-looking fields elsewhere. The plaintext value is copied into global `config.toml` and remains in Git history, clones, backups, and GitHub audit surfaces even after replacement. Use only a private repository with narrowly selected access and rotate the token after any suspected exposure.

## Marketplaces

Public Git source:

```toml
[[marketplaces]]
source = "git"
name = "dale0525-codex-plugins"
url = "https://github.com/dale0525/codex-plugins.git"
git_ref = "main"
sparse = []
```

Private GitHub snapshot source:

```toml
[[marketplaces]]
source = "github-snapshot"
name = "personal-private"
repository = "owner/private-codex-plugins"
git_ref = "3f8d27c..."
```

Private sources are resolved to a commit SHA, downloaded through the authenticated GitHub API, validated, and registered as versioned local marketplace snapshots. Pin releases or commit SHAs for reproducibility.

## Plugins

```toml
[[plugins]]
id = "codex-sync@dale0525-codex-plugins"
enabled = true

[[plugins]]
id = "private-tool@personal-private"
enabled = true
```

Plugin IDs must use `plugin@marketplace` syntax. `enabled = false` means the plugin should be absent on synchronized devices.

## Ownership behavior

The repository declares desired portable state. It does not own or transport:

- `auth.json` or provider secrets other than an explicitly declared `experimental_bearer_token`
- sessions, history, memories, goals, logs, or SQLite databases
- project trust decisions
- hook trusted hashes
- desktop runtime state unless explicitly placed in portable or device config
- downloaded marketplace or plugin caches
