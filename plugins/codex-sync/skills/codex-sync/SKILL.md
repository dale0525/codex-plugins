---
name: codex-sync
description: Set up, inspect, synchronize, publish, or roll back global Codex configuration through a selected private GitHub repository. Use when a user is onboarding a new device, syncing AGENTS.md, native agent profiles, or portable config.toml settings, reconciling providers, marketplaces, or plugins, checking configuration drift, or restoring a previous synchronized state.
---

# Codex Sync

Use the bundled deterministic engine for every operation. Do not reproduce its TOML merge, GitHub authentication, marketplace installation, or rollback logic with ad hoc shell edits.

## Resolve the engine

Resolve the plugin root as the parent of the `skills/` directory containing this skill. For the canonical installed path `$PLUGIN_ROOT/skills/codex-sync/SKILL.md`, move up from `codex-sync/` to `skills/`, then once more to `$PLUGIN_ROOT`. On POSIX run `test -f "$PLUGIN_ROOT/.codex-plugin/plugin.json"`; on Windows run `if (-not (Test-Path -LiteralPath "$pluginRoot\.codex-plugin\plugin.json")) { throw 'Invalid Codex Sync plugin root' }`. Stop if validation fails. Never look for `scripts/` inside the skill directory.

On macOS or Linux, invoke every command through:

```sh
<plugin-root>/scripts/bootstrap.sh <command> [arguments]
```

On Windows, invoke with PowerShell 7:

```powershell
pwsh -NoProfile -File <plugin-root>\scripts\bootstrap.ps1 <command> [arguments]
```

In the workflows below, `<bootstrap>` means the complete platform-specific invocation above, not a literal executable name.

The bootstrap verifies a release checksum before caching the platform binary under `$CODEX_HOME/codex-sync/bin/`. During local plugin development, honor `CODEX_SYNC_BIN` when it points to a reviewed development build. For an offline preflight, set `CODEX_SYNC_OFFLINE=1`; bootstrap then uses an existing reviewed binary or fails without network access.

## Choose the workflow

- New device or first connection: run the setup workflow.
- Pull remote changes: run the synchronization workflow.
- Inspect without modifying anything: run `status` and `doctor`.
- Publish deliberate edits from the local repository cache: run the publication workflow.
- Recover from a bad apply: run the rollback workflow.

Read [repository-schema.md](references/repository-schema.md) when creating or editing a configuration repository. Read [security.md](references/security.md) before approving hooks, MCP servers, providers, marketplace sources, plugins, publication, or rollback. Read [github-app.md](references/github-app.md) when GitHub device login is not configured.

## Set up a device

1. Confirm the private repository in `owner/name` form, a portable device ID, and the branch or tag. The bundled `dale0525-codex-sync` GitHub App client ID is the default. For a reviewed self-hosted App, override it with `--github-client-id` or `CODEX_SYNC_GITHUB_CLIENT_ID`; verify an environment override without printing it. Never request a client secret, private key, personal access token, API key, or provider token in chat.
   If the repository is not initialized, stop. Codex Sync intentionally does not create GitHub repositories because that would require broader account permissions. Ask the user to create a private repository with an initial commit from `<plugin-root>/templates/config-repository/`, rename `devices/example-device.toml`, and review every value before continuing.
2. Run:

```text
<bootstrap> setup --repository OWNER/REPOSITORY --device DEVICE --git-ref BRANCH
```

3. Run `<bootstrap> login`. Present the GitHub verification URL and one-time user code exactly as emitted by the engine. Wait for the user to complete browser authorization. If credential storage fails, stop and report the OS credential-store error; never fall back to a plaintext token file. The received token exists only in process memory until keyring storage succeeds; if storage fails after authorization, ask the user to revoke the App authorization in GitHub settings before retrying.
4. Run `<bootstrap> sync`. Summarize the plan by low-risk and high-risk changes.
5. Ask for confirmation before every apply. For high-risk plans, enumerate the high-risk changes and obtain explicit confirmation. Apply only the exact plan ID printed by `sync`:

```text
<bootstrap> apply PLAN_ID --approve-high-risk
```

6. Report the applied commit and instruct the user to start a new Codex task. Plugin skills, tools, and config changes are not guaranteed to hot-load into the current task.

Do not silently replace an existing setup. Run `<bootstrap> status` first. Use `setup ... --replace-existing` only after the user explicitly approves replacing the repository/device binding; the engine backs up the previous local state.

## Synchronize

1. Run `<bootstrap> doctor` if the previous synchronization failed or the device has changed materially.
2. Run `<bootstrap> sync`. This fetches an immutable commit snapshot and creates a pending plan; it does not apply changes.
   If the engine reports unpublished cache edits, publish them or obtain explicit permission before running `<bootstrap> sync --discard-local`; never discard them automatically. Discarding invalidates any older pending plan, so run sync again and use only the newly printed plan ID.
3. Show the plan ID, commit, affected configuration paths, agent profile operations, marketplace operations, and plugin operations.
4. For a low-risk plan, show all changes and ask “Apply this plan now?”; wait for an affirmative answer. For a high-risk plan, enumerate those changes and obtain explicit confirmation.
5. If the engine reports that `config.toml`, `AGENTS.md`, or a managed agent profile changed after planning, do not use the old plan ID. Run `sync` again, which replaces the pending plan, and review the newly printed ID.

Never copy hook trust hashes, project trust, authentication sessions, SQLite state, or plugin caches between devices.

## Publish

Treat GitHub as the shared source of truth. Do not infer that arbitrary live `config.toml` state should be uploaded.

1. Run `status` and identify the local repository cache.
2. Edit only the repository cache and validate it with `doctor`.
3. Show the repository diff or exact changed files to the user.
4. After explicit confirmation, run:

```text
<bootstrap> publish --message "MESSAGE" --approve
```

The engine publishes one commit only when the remote branch still matches the fetched base commit. If the branch advanced, synchronize first; never force-push.

## Roll back

1. Explain that rollback restores the synchronized `config.toml`, global `AGENTS.md`, managed agent profiles, and local sync state from a pre-apply backup. Downloaded caches may remain but are not authoritative.
2. After confirmation, run `<bootstrap> rollback --approve` for the latest backup, or `<bootstrap> rollback BACKUP_NAME --approve` for a named backup.
3. Ask the user to start a new Codex task.

## Guardrails

- Prefer provider credentials in environment variables, command-backed authentication, or the OS credential store. If the user explicitly chooses plaintext cross-device storage, allow only `providers.<name>.experimental_bearer_token`, warn that it persists in Git history and global `config.toml`, and treat it as a high-risk provider change. Never ask the user to paste the token into chat.
- Do not bypass a secret-scan failure.
- Do not apply a plan whose ID, base hashes, or commit no longer match.
- Do not manually edit Codex marketplace snapshot metadata or plugin cache directories.
- Do not add lifecycle hooks for synchronization or drift repair. All checks and applies are explicitly invoked.
