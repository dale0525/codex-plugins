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
and one rolling pre-pull backup. Pull plans before mutation, detaches plugins
before source-mismatched/extra marketplaces, refreshes every desired
marketplace, runs `plugin add` for every desired plugin, then re-lists to verify
the exact marketplace/plugin sets and installed/enabled state. A plugin or
marketplace failure does not roll back already-applied CLI operations; state
keeps the previous commit and is marked not converged so a later pull retries
from the actual local listing. Remote pushes are ordinary non-force
fast-forward pushes; remote races fail instead of overwriting someone else's
commit.

Never synchronize auth/session/history, SQLite state, project trust, caches,
automations, or plugin provision artifacts. Pull never reads or mutates the
local automation store; push removes legacy repository automation declarations
without inspecting local automations. Existing declared configuration leaves
(including hook settings) remain within the normal config policy. The only new
configuration paths auto-declared during capture are the
non-secret `model_providers.*.http_headers.x-openai-actor-authorization` marker
and `features.code_mode.direct_only_tool_namespaces` list; Codex Sync never adds
lifecycle hooks. Personal, OpenAI, and
`openai-*` names are protected at
capture, planning, mutation, and state boundaries. Non-Git marketplaces are
outside the sync domain. A desired name colliding with a protected or
non-portable local marketplace fails preflight before any device mutation.
