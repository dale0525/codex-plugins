---
name: codex-sync
description: Synchronize global Codex configuration, agent profiles, Git marketplaces, and plugins only when the user explicitly names Codex Sync or explicitly requests its setup, status, pull, or push workflow. Do not trigger for ordinary local configuration edits or generic Git operations.
---

# Codex Sync

Use the bundled engine for every operation. The engine owns a Git cache under
`CODEX_SYNC_HOME`; do not edit that cache manually. Every `pull` and `push`
fetches the configured branch and uses it as the baseline.

Resolve the plugin root as the parent of the `skills/` directory containing this
file. On macOS/Linux invoke:

```sh
<plugin-root>/scripts/bootstrap.sh <command> [arguments]
```

On Windows invoke:

```powershell
pwsh -NoProfile -File <plugin-root>\scripts\bootstrap.ps1 <command> [arguments]
```

For local development, `CODEX_SYNC_BIN` may point to a reviewed build. The
bootstrap downloads and verifies the 0.6.7 release binary otherwise. On Windows,
it resolves Git independently of the system `PATH`: it uses a reviewed
`CODEX_SYNC_GIT_BIN` override, a usable installed Git, or FastCtx's portable
Git when available, otherwise downloads a locked, SHA-256-verified portable Git
runtime into Codex Sync's private data directory. No system-wide Git installation
is required. `CODEX_SYNC_OFFLINE=1` prevents this download and reports the
missing runtime explicitly.

## Commands

First setup requires a repository and device:

```text
<bootstrap> setup --repository OWNER/REPOSITORY --device DEVICE [--branch main]
```

`OWNER/REPOSITORY` is converted to a GitHub HTTPS URL. A complete HTTPS or SSH
Git URL is also accepted. If a v0.4 state file is present, setup can be run
without these values to reuse its binding and migrate it. Setup never reads or
saves Git credentials; normal Git credential helpers and process environment
inheritance remain under the user's control.

Pull and apply the remote branch directly:

```text
<bootstrap> pull
<bootstrap> pull --dry-run
```

Pull first builds a strict non-protected convergence plan, then overlays
`config/common.toml` with `devices/<device>.toml` (device values win), replaces
`AGENTS.md`, mirrors only agent profiles referenced by backtick names in that
file (removing profiles that are no longer referenced), and registers or
refreshes desired Git marketplaces before running an unconditional `plugin add`
for every desired plugin. A final marketplace/plugin listing must exactly match the
remote non-protected Git sets and report each desired plugin as `installed =
true` and `enabled = true`. After that verification, pull bootstraps only
`provider-chat-completions` and `provider-imagegen`: it reads the synchronized
active provider directly from local `config.toml` and atomically writes an
owner-only credential cache under each installed plugin version. It never sends
that cache to app-server, Git, or the marketplace source. Source identity is
`(url, ref, sparse)`; source
mismatches detach plugins before replacing a marketplace. Personal, `openai`,
`openai-*`, and non-Git resources remain outside the sync domain. Core files are
written atomically with one rolling backup. A plugin/marketplace failure leaves
already-applied operations in place, keeps the previous commit, marks state not
converged, and makes the next pull reconcile from the actual local listing.
When Codex's plugin listing omits a stale non-protected plugin that is still
declared under `[plugins]` in `config.toml`, pull also treats that declaration as
local state, removes the declaration atomically, and verifies it is gone. Known
non-Git marketplace declarations and protected resources remain untouched.

Capture and publish the current device:

```text
<bootstrap> push
<bootstrap> push --dry-run
<bootstrap> push --message "Describe this change"
```

Push updates only leaves already declared in `config/common.toml` and the
current device file, plus the narrowly allowlisted
`model_providers.*.http_headers.x-openai-actor-authorization` capability header
and `features.code_mode.direct_only_tool_namespaces` capability list.
Other newly discovered local keys are reported and not captured.
It captures the complete current `AGENTS.md`, only local agent TOML profiles
referenced by backtick names in that file, and reports profiles that are no
longer needed. It does not read or capture local automations. It also captures
every installed non-protected plugin (including disabled entries), and only
marketplaces referenced by at least one such installed plugin. Personal,
OpenAI and `openai-*` resources, available-but-uninstalled plugins, non-Git
local marketplaces, and orphan marketplaces are excluded. A local marketplace
is exported as Git only when its canonical source is the top of a Git worktree,
has one credential-free HTTPS/SSH/scp `origin`, is on a branch, and its manifest
and captured plugin definitions are tracked by `HEAD`; workspace cleanliness is
not required. `push --dry-run` never commits, pushes, writes local state, or
mutates Codex state. A real no-change push still records the fetched base as
`last_applied_commit` and marks state converged.
Normal pushes use a non-force fast-forward update with author `Logic Tan
<logictan89@gmail.com>`; a remote race fails and the next operation starts
again from the latest remote branch.

Inspect the binding and convergence state with:

```text
<bootstrap> status
```

## Repository schema

The repository manifest is fixed:

```toml
schema_version = 3
```

The only managed paths are `AGENTS.md`, `agents/*.toml`,
`config/common.toml`, `devices/<device>.toml`, `marketplaces.toml`, and
`plugins.toml`. There is no `providers.toml`, custom path declaration, or
external AGENTS section. `plugins.toml` is a string array:

```toml
plugins = ["plugin@market"]
```

The local state stores only binding, managed configuration paths/profiles,
commit, migration, and convergence data. Unknown fields from older state files
(including former `managed_plugins` and `managed_markets`) are ignored on read
and disappear on the next save; the schema version is unchanged. Pull has no
ownership or conflict-preserve history: the remote non-protected Git sets are
authoritative. A desired name colliding with a protected or non-portable local
marketplace fails preflight before any device mutation.

Marketplaces are `[[marketplaces]]` entries with `source = "git"`, a portable
Git URL, and an optional ref/sparse list. A v2 manifest is migrated in the
engine-owned worktree. `github-snapshot` marketplaces fail explicitly rather
than remaining backward-compatible.

Model provider data lives under `[model_providers.<name>]` in common/device
configuration. Only `model_providers.*.experimental_bearer_token` may contain
a literal bearer token; other probable secrets and URLs with embedded
credentials are rejected. `model_reasoning_effort` is synchronized verbatim.

Do not synchronize auth/session/history, SQLite state, caches, automations, or
plugin provision artifacts. The two provider credential caches are device-local
post-pull runtime artifacts, not repository members or synchronized state.
Automations are entirely device-local: pull ignores
legacy repository automation declarations, and the next push removes a legacy
`automations/` directory from the repository without reading or mutating the
local automation store. Codex Sync is explicit and does not add lifecycle timers
or hooks.
