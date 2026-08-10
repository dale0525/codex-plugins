---
name: gortex
description: Use Gortex for structural code search, symbol relationships, call chains, impact analysis, and architecture exploration in the current repository.
---

# Gortex

Use Gortex when the task needs repository-wide structural search, symbol callers
or implementations, dependency and impact analysis, or an architecture view that
spans several files. Keep small direct file lookups on FastCtx first.

Work only in the repository the user placed in scope. Select or index the current
repository before graph queries, and use its absolute path when a tool asks for a
project path. Do not automatically index unrelated repositories.

Use graph results to narrow the source files that need direct inspection. Confirm
negative conclusions with a direct source search before reporting that a caller,
implementation, dependency, or impact does not exist.

Never invoke or recommend `gortex install`; it can modify Codex configuration,
hooks, and instruction files. Plugin and runtime upgrades happen through the
repository's reviewed release pin and the user's explicit `codex-sync pull` flow.
