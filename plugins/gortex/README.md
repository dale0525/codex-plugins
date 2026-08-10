# Gortex

This plugin starts a pinned, verified [Gortex](https://github.com/zzet/gortex)
runtime without running the upstream installer or changing Codex configuration.
On first MCP startup it downloads the locked archive, verifies its byte size and
SHA-256 digest, and publishes the binary into a versioned device-local cache.

Supported platforms are Apple Silicon macOS and Git Bash/MSYS2 on 64-bit Windows.
The cache is `~/.local/share/gortex/versions/<version>` on macOS and
`%LOCALAPPDATA%/gortex/versions/<version>` on Windows.

The launcher executes `gortex mcp`. Gortex starts or reuses one detached local
daemon, while each Codex MCP connection is a lightweight stdio relay. Graph and
index state live in the shared daemon rather than in every relay process.

The launcher never checks for or switches to a newer runtime itself. A daily
GitHub Actions workflow reviews the newest stable upstream release, verifies its
checksums, updates `runtime-release.json`, and bumps the plugin patch version.
Devices update only after an explicit `codex-sync pull` followed by a new task.

Do not run `gortex install`; it bypasses this release pin and can rewrite Codex
configuration, hooks, and instruction files. There is no automatic rollback or
fallback to an older cached version.

Gortex is distributed under Apache-2.0. See `third-party/gortex-LICENSE`.
