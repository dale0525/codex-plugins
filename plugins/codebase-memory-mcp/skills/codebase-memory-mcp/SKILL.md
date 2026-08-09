---
name: codebase-memory-mcp
description: Use Codebase Memory MCP for call chains, impact analysis, architecture questions, and code-graph exploration in the current repository.
---

# Codebase Memory MCP

Use this MCP server when the task needs a call chain, dependency or impact map,
an architectural explanation, or a code graph that spans several files. Keep
ordinary file discovery and small, direct lookups on FastCtx first.

Work only in the repository the user placed in scope. Do not index another
repository automatically. Before a destructive or broad query, narrow it to the
named component, symbol, route, or change.

Use the returned graph to choose the smallest set of source files to inspect.
For a negative conclusion (for example, that no caller, implementation, or
impact exists), read the relevant source again and confirm the absence with the
appropriate direct search before reporting it.

Always pass the current repository's absolute path when indexing or selecting a
project; do not infer it from the MCP process directory. Never invoke or
recommend the upstream `codebase-memory-mcp install` or
`codebase-memory-mcp update` commands. Plugin and runtime upgrades happen only
through the user's explicit `codex-sync pull` workflow.
