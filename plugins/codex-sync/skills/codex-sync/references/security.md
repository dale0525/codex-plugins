# Security and approval policy

## High-risk changes

Always require explicit user confirmation before applying or publishing changes involving:

- global `AGENTS.md`
- native agent profiles
- hooks or executable commands
- MCP servers
- model providers or provider base URLs
- sandbox, permissions, or approval policy
- shell environment policy
- marketplace registration or replacement
- plugin installation or removal
- publication to GitHub
- rollback

Codex Sync ships without lifecycle hooks. Synchronization, drift checks, and apply operations run only when the user invokes them.

## Credential rules

GitHub access and refresh tokens belong in the operating-system credential store. `CODEX_SYNC_GITHUB_TOKEN` is an ephemeral automation override for trusted noninteractive environments only. Inject it for one process, ensure the process environment is not logged, and unset it immediately afterward. Never persist it in shell profiles, repository files, logs, or prompts. The engine strips it before invoking Codex child processes.

Provider secrets should use an OS credential store, `env_key`, or command-backed authentication. As an explicit exception for private configuration repositories, `providers.<name>.experimental_bearer_token` may contain a plaintext static bearer token for zero-setup cross-device synchronization. It is copied into global `config.toml` and persists in Git history, clones, backups, and GitHub audit surfaces. Confirm the repository is private with narrowly selected access before publishing, never place the token in chat or logs, and rotate it after any suspected exposure. No other probable secret field is allowed.

Current-device capture copies complete `model_providers` tables into the repository cache. Validate them with the same secret policy before replacing the cache, never print an `experimental_bearer_token` value in capture output or review summaries, and retain the explicit publication approval gate.

Publication rejects obvious private-key filenames, `.env`, `auth.json`, GitHub token prefixes, and private-key markers. Treat a rejection as a security incident to investigate; do not rename a secret merely to bypass detection.

## Supply-chain controls

- Resolve private repositories and marketplaces to immutable commit SHAs before downloading.
- Follow GitHub archive redirects only through the engine's bounded HTTPS client.
- Reject ZIP path traversal and multiple archive roots.
- Verify released engine binaries with the published SHA-256 checksum.
- Keep marketplace source changes visible in the synchronization plan.
- Exclude app-managed `openai` and `openai-*` marketplaces and plugins from current-device capture. This exclusion affects synchronized declarations only and never uninstalls the desktop App's local runtime plugins.
- Applying a plan may download and register plugins, but never invoke their new capabilities in the current task. Start a new task before use.

## Concurrency and rollback

The engine uses a per-user process lock and hashes `config.toml`, `AGENTS.md`, and every managed agent profile into each pending plan. Apply is rejected if any managed input changes after planning. Writes are atomic per file, and a pre-apply backup is restored automatically when the file or plugin transaction fails.

The fetched repository cache also has a deterministic tree digest. Synchronization refuses to replace or apply unpublished cache edits unless the user explicitly selects `--discard-local`.

GitHub publication compares the current remote branch SHA with the fetched base and uses a non-forced reference update. It never force-pushes.
