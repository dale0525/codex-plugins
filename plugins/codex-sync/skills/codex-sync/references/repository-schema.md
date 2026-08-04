# Codex Sync repository schema v3

The root manifest is intentionally exact:

```toml
schema_version = 3
```

Managed files are fixed to `AGENTS.md`, `agents/*.toml`,
`config/common.toml`, `devices/<device>.toml`, `marketplaces.toml`, and
`plugins.toml`. Do not add path mappings, `providers.toml`, or external AGENTS
sections.

Common configuration is overlaid by the current device file. Existing local
keys outside the set previously managed by Codex Sync are preserved on pull.
Push only samples already declared leaves and reports new local keys.

```toml
# plugins.toml
plugins = ["my-plugin@my-market"]

# marketplaces.toml
[[marketplaces]]
source = "git"
name = "my-market"
url = "https://example.test/my-market.git"
git_ref = "main"
sparse = []
```

Only Git marketplace sources are portable. The `openai` and `openai-*`
namespaces are application-managed and are never changed by Codex Sync.

Repositories using schema v2 are converted in the engine-owned Git cache. V2
provider tables are merged into `[model_providers.*]`; disabled and
auto-provisioned plugin metadata is discarded. A `github-snapshot` marketplace
must be replaced with a Git source before migration can complete.
