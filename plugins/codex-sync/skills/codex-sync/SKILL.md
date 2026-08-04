---
name: codex-sync
description: Bind Codex Sync to a Git repository and pull or push global Codex configuration, agent profiles, Git marketplaces, and plugins.
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
bootstrap downloads and verifies the 0.5.0 release binary otherwise.

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

Pull overlays `config/common.toml` with `devices/<device>.toml` (device values
win), replaces `AGENTS.md`, mirrors synchronized `agents/*.toml`, then registers
or refreshes Git marketplaces before installing plugins. Previously managed
markets and plugins removed from the repository are removed, except `openai`
and `openai-*` resources and resources that were never managed. Core files are
written atomically with one rolling backup. Plugin failure leaves core files in
place and state marked not converged; rerun pull to retry.

Capture and publish the current device:

```text
<bootstrap> push
<bootstrap> push --dry-run
<bootstrap> push --message "Describe this change"
```

Push updates only leaves already declared in `config/common.toml` and the
current device file; newly discovered local keys are reported and not captured.
It captures the complete current `AGENTS.md`, all local agent TOML profiles,
enabled non-OpenAI plugins, and portable Git marketplaces. `push --dry-run`
never commits or pushes. Normal pushes use a non-force fast-forward update with
author `Logic Tan <logictan89@gmail.com>`; a remote race fails and the next
operation starts again from the latest remote branch.

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

Marketplaces are `[[marketplaces]]` entries with `source = "git"`, a portable
Git URL, and an optional ref/sparse list. A v2 manifest is migrated in the
engine-owned worktree. `github-snapshot` marketplaces fail explicitly rather
than remaining backward-compatible.

Model provider data lives under `[model_providers.<name>]` in common/device
configuration. Only `model_providers.*.experimental_bearer_token` may contain
a literal bearer token; other probable secrets and URLs with embedded
credentials are rejected. `model_reasoning_effort` is synchronized verbatim.

Do not synchronize auth/session/history, SQLite state, caches, or plugin
provision artifacts. Codex Sync is explicit and does not add lifecycle timers or
hooks.
