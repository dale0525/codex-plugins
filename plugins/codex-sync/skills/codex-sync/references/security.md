# Codex Sync security boundary

Codex Sync invokes the system `git` and Codex CLI. It does not use a GitHub API,
GitHub App, keyring, ZIP snapshot, or token store. Git credentials are inherited
by the child process and are never read, printed, or persisted by the engine.

Push rejects probable secret keys and URLs with embedded credentials. The sole
portable literal exception is
`model_providers.<name>.experimental_bearer_token`; it remains visible in Git
history and should be used only when explicitly required. Prefer environment
keys and the Codex credential mechanisms for all other providers.

The engine uses a process lock, an engine-owned Git cache, atomic core writes,
and one rolling pre-pull backup. A plugin or marketplace failure does not roll
back already-applied core files; state remains not converged so a later pull is
an idempotent retry. Remote pushes are ordinary non-force fast-forward pushes;
remote races fail instead of overwriting someone else's commit.

Never synchronize auth/session/history, SQLite state, project trust, caches, or
plugin provision artifacts. Existing declared configuration leaves (including
hook settings) remain within the normal config policy; Codex Sync never adds
lifecycle hooks. OpenAI-managed marketplaces and plugins
remain untouched, as do marketplaces that were never recorded as managed.
