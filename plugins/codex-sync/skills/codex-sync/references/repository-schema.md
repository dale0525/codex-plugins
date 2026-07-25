# Configuration repository schema

## Contents

- Required layout
- Root manifest
- Agent profiles
- Portable and device configuration
- Providers
- Marketplaces
- Plugins
- Ownership behavior

## Required layout

```text
codex-sync.toml
AGENTS.md
agents/*.toml
config/common.toml
devices/<device-id>.toml
marketplaces.toml
plugins.toml
providers.toml
```

`codex-sync.toml`, the referenced `AGENTS.md`, and a non-empty agent profiles directory are required. Missing optional TOML files are treated as empty. All text must be UTF-8.

## Root manifest

```toml
schema_version = 2
agents = "AGENTS.md"
agent_profiles = "agents"
common_config = "config/common.toml"
devices = "devices"
marketplaces = "marketplaces.toml"
plugins = "plugins.toml"
providers = "providers.toml"
```

Every path must be relative and remain inside the repository. Parent traversal and absolute paths are rejected.

## Agent profiles

Put the shared native subagent profiles under the directory selected by `agent_profiles`:

```text
agents/default.toml
agents/creative_text.toml
agents/image.toml
```

All synchronized devices use the same profile files. Each file must be UTF-8 TOML, use a portable filename, and declare matching `name`, `description`, and `developer_instructions` strings. Model and reasoning settings may also be declared in the profile.

Agent profiles inherit session settings that they omit. Keep complete custom provider definitions in `providers.toml` and omit partial `[model_providers.<name>]` tables from profile files so they inherit the synchronized parent provider. This avoids a partial profile definition shadowing provider authentication fields.

Profile additions, replacements, and removals are high-risk changes. The engine owns only the filenames it previously synchronized and preserves unrelated files under `$CODEX_HOME/agents/`.

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

The engine remembers every managed leaf key. On a later apply it removes keys that were previously managed but have disappeared from the repository, then applies the new common and device values. Unmanaged Codex tables such as project trust, desktop state, and marketplace revision metadata remain intact.

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

Prefer `env_key` or command-backed authentication. When a private configuration repository must make a provider immediately usable on every device, the provider file may explicitly store Codex's dev-only plaintext field:

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

Plugin IDs must use `plugin@marketplace` syntax. For each non-OpenAI marketplace declared in `marketplaces.toml`, `plugins.toml` is the complete synchronized plugin set. Removing an installed plugin's entry schedules a high-risk uninstall through `codex plugin remove`, which removes its local configuration and cache. Plugins from undeclared marketplaces are preserved.

Use presence in this file to express the desired installed set; remove a plugin's entire entry when it should be absent. `enabled = false` remains accepted for backward compatibility and also requests removal, but capture does not generate or retain disabled entries.

## Ownership behavior

The repository declares desired portable state. It does not own or transport:

- `auth.json` or provider secrets other than an explicitly declared `experimental_bearer_token`
- sessions, history, memories, goals, logs, or SQLite databases
- project trust decisions
- hook trust decisions
- desktop runtime state unless explicitly placed in portable or device config
- downloaded marketplace or plugin caches

## Current-device capture

`codex-sync capture` transactionally updates the repository cache from the current device. It captures:

- current values for leaf keys already declared by common and current-device configuration, while preserving common values shadowed by a device override
- complete current `model_providers` tables into `providers.toml`, subject to the normal secret policy
- global `AGENTS.md` and the profile files already synchronized by the repository
- installed and enabled plugins outside marketplaces named `openai` or beginning with `openai-`

Capture removes OpenAI-managed plugin and marketplace declarations from the cache. It also removes entries for portable plugins that are absent or disabled on the current device. After reviewed publication, other devices interpret the missing entry as a high-risk uninstall because `plugins.toml` is the complete desired set for declared marketplaces.

When an installed plugin uses an undeclared marketplace, capture can add that marketplace only when current Codex configuration records a portable HTTPS Git source and ref. Local marketplaces, including the implicit personal marketplace, are skipped with a warning because the configuration repository does not transport plugin source code.

Capture does not discover arbitrary new common or device configuration keys. Add a new key to the appropriate repository file once to establish its ownership and portability; later captures update its current value automatically.
