---
name: fastctx
description: Set up, inspect, repair, reconfigure, or remove the native FastCtx MCP runtime without installing an npm package globally. Use when FastCtx provisioning did not complete during Codex Sync, when its MCP tools or Bash integration drift, when the user wants status, or when FastCtx must be uninstalled cleanly.
---

# FastCtx

Use the bundled provision scripts instead of installing `fastctx` with npm, npx, or Cargo. Codex Sync normally runs setup automatically after installing or upgrading this plugin. Run the workflow here only for explicit setup, repair, status, or removal.

## Resolve the plugin root

Resolve the plugin root as the parent of the `skills/` directory that contains this skill. Confirm that both `.codex-plugin/plugin.json` and `upstream-release.json` exist there. Stop if either file is missing.

On macOS or Linux, use:

```sh
<plugin-root>/scripts/provision.sh <action>
```

On Windows, use PowerShell 7:

```powershell
pwsh -NoProfile -File <plugin-root>\scripts\provision.ps1 <action>
```

Supported actions are `setup`, `status`, and `unapply`.

## Set up or repair

1. Explain that setup downloads the exact reviewed GitHub Release recorded in `upstream-release.json`, verifies its SHA-256 digest, enables the FastCtx Bash tools, and changes global Codex configuration plus `~/.fastctx/`.
2. Obtain explicit confirmation unless this invocation is the already-approved Codex Sync provisioning step.
3. Run the platform provision command with `setup --yes`.
4. Report the FastCtx version and status result.
5. Ask the user to start a new Codex task so the MCP tools and global instructions are reloaded.

Do not fall back to a global npm installation. Do not enable FastCtx by manually duplicating its Codex TOML or AGENTS marker logic.

## Inspect status

Run the provision command with `status`. This is read-only. Report missing binaries, configuration drift, AGENTS drift, or MCP handshake failures exactly as FastCtx reports them.

## Remove FastCtx

1. Explain that Unapply stops FastCtx background jobs, removes its Codex MCP configuration and AGENTS marker, and deletes `~/.fastctx/`.
2. Obtain explicit confirmation.
3. Run the provision command with `unapply --yes` while the plugin is still installed.
4. If Codex Sync manages this plugin, remove its entry from the private configuration repository only after Unapply succeeds, then use the normal reviewed Codex Sync workflow.
5. Ask the user to start a new Codex task.
