# Security and approval policy

## High-risk changes

Always require explicit user confirmation before applying or publishing changes involving:

- global `AGENTS.md`
- hooks or executable commands
- MCP servers
- model providers or provider base URLs
- sandbox, permissions, or approval policy
- shell environment policy
- marketplace registration or replacement
- plugin installation or removal
- publication to GitHub
- rollback

Installing Codex Sync does not automatically trust its update-check hook. Review and trust that hook independently on every device.

## Credential rules

GitHub access and refresh tokens belong in the operating-system credential store. `CODEX_SYNC_GITHUB_TOKEN` is an ephemeral automation override for trusted noninteractive environments only. Inject it for one process, ensure the process environment is not logged, and unset it immediately afterward. Never persist it in shell profiles, repository files, logs, or prompts. The engine strips it before invoking Codex child processes.

Provider secrets belong in an OS credential store or environment injection mechanism. Repository files may contain only secret references such as `env_key`.

Publication rejects obvious private-key filenames, `.env`, `auth.json`, GitHub token prefixes, and private-key markers. Treat a rejection as a security incident to investigate; do not rename a secret merely to bypass detection.

## Supply-chain controls

- Resolve private repositories and marketplaces to immutable commit SHAs before downloading.
- Follow GitHub archive redirects only through the engine's bounded HTTPS client.
- Reject ZIP path traversal and multiple archive roots.
- Verify released engine binaries with the published SHA-256 checksum.
- Keep marketplace source changes visible in the synchronization plan.
- Applying a plan may download and register hooks or plugins, but never invoke their new capabilities in the current task. Review hook definitions locally and start a new task before use.

## Concurrency and rollback

The engine uses a per-user process lock, hashes `config.toml` and `AGENTS.md` into every pending plan, and rejects apply if either file changes after planning. Writes are atomic. A pre-apply backup is restored automatically when the file or plugin transaction fails.

The fetched repository cache also has a deterministic tree digest. Synchronization refuses to replace or apply unpublished cache edits unless the user explicitly selects `--discard-local`.

GitHub publication compares the current remote branch SHA with the fetched base and uses a non-forced reference update. It never force-pushes.
