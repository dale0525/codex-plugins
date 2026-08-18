# Codex Sync repository schema v3

The root manifest is intentionally exact:

```toml
schema_version = 3
```

Managed files are fixed to `AGENTS.md`, `agents/*.toml`,
`config/common.toml`, `devices/<device>.toml`, `automations/<id>/automation.toml`,
`marketplaces.toml`, and `plugins.toml`. Do not add path mappings,
`providers.toml`, or external AGENTS sections.

`automations/<id>/automation.toml` is the declaration-only part of a scheduled
automation. IDs use 1-128 ASCII letters, numbers, `.`, `-`, or `_`; leading or
trailing dots, case-insensitive duplicates, and Windows reserved names are
rejected for cross-device portability. The engine validates the Codex desktop
fields (`version`, `id`, `kind`, `name`, `prompt`, `status`, `rrule`,
model/reasoning, execution
environment, target/cwds, and timestamps). It also accepts the optional
`approval_policy` (`untrusted`, `on-request`, `never`, or `on-failure`) and
`sandbox_mode` (`read-only`, `workspace-write`, or `danger-full-access`) as
explicit sync metadata. Current Codex desktop automation files do not emit
these two fields and the current desktop runner derives the effective run
permissions from the selected/saved configuration and installation
requirements; therefore syncing these metadata fields does not by itself
override a read-only outer policy. Verify the effective mode in the automation
settings UI on each device.

When a desktop rewrite omits `approval_policy` or `sandbox_mode`, push preserves
the corresponding value already present in the repository. An explicit local
value still replaces the repository value.

Only `automation.toml` is synchronized. `$CODEX_HOME/automations/*/memory.md`,
`.run-jitter-salt`, run history, logs, SQLite state, and other lifecycle files
remain local and are never copied or deleted by pull/push. Removing a remote
definition removes only the local `automation.toml`; local memory is retained.

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

Only Git marketplace sources are portable. During capture, a local source is
exported as Git only when its canonical path is the Git worktree top, `origin`
is unique and credential-free HTTPS/SSH/scp, `HEAD` names a branch, and the
manifest plus every captured plugin definition is tracked by `HEAD`. Dirty
worktrees and local commits are allowed; synchronization follows `origin` and
the current branch. Personal/non-Git local and orphan marketplaces are
skipped. The `personal`, `openai`, and `openai-*` namespaces are application-managed and
are never changed by Codex Sync.

Capture includes every installed non-protected plugin, including disabled
ones; available-but-uninstalled entries are excluded. Local state no longer
stores marketplace/plugin ownership sets. Unknown legacy fields such as
`managed_plugins` and `managed_markets` are ignored when read and disappear on
the next save without a schema bump. Pull treats remote valid non-protected Git
markets and their plugins as the exact desired sets; source identity is
`(url, ref, sparse)` and a desired name colliding with a local non-portable
market fails preflight.

Repositories using schema v2 are converted in the engine-owned Git cache. V2
provider tables are merged into `[model_providers.*]`; disabled and
auto-provisioned plugin metadata is discarded. A `github-snapshot` marketplace
must be replaced with a Git source before migration can complete.
