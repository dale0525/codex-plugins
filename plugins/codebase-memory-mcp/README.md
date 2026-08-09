# Codebase Memory MCP

This plugin starts a pinned, verified [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) runtime without running the upstream installer or changing agent configuration. Its MCP entry downloads the locked archive on first startup, verifies its byte size and SHA-256 digest, then launches it.

## Install and first startup

Install this plugin through the Codex plugin marketplace. The first MCP request on a supported platform downloads the release recorded in `runtime-release.json` to a versioned user cache and may take longer than ordinary startup. All launcher diagnostics go to stderr; MCP stdout remains reserved for JSON-RPC.

Supported platforms are macOS on Apple Silicon and Git Bash/MSYS2 on 64-bit Windows. The cache is `~/.local/share/codebase-memory-mcp/versions/<version>` on macOS and `%LOCALAPPDATA%/codebase-memory-mcp/versions/<version>` on Windows.

## Manual upgrade and rollback policy

The launcher never checks for, installs, or switches to a newer upstream release automatically. It also gives the MCP process a private `curl` shim that disables the upstream runtime's built-in update check without affecting the launcher's verified download. GitHub Actions reviews and publishes the daily `runtime-release.json` and plugin-version update. On a device, manually run `codex-sync pull`, then begin a new task so its MCP process reads the newly pinned release. The next startup downloads only that explicitly pinned version. Do not run the upstream `install` or `update` commands; they bypass this release pin and may rewrite Codex configuration, AGENTS, hooks, and indexes.

There is no automatic rollback and the launcher never falls back to an older cached version. If the pinned version is missing or fails verification, startup fails while preserving existing version directories for inspection.

## Third-party license

The runtime is distributed by DeusData under MIT; see `third-party/codebase-memory-mcp-LICENSE`.
