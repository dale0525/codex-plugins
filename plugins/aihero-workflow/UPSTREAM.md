# AIHero Workflow provenance

This plugin is a Codex-specific adaptation of `mattpocock/skills`.

- Upstream repository: https://github.com/mattpocock/skills
- Reviewed upstream commit: `6654f6b60cd9d5be8b54c6fafe44346dabeb3b76`
- Adaptation policy: keep the workflow concepts, including the standalone
  `wait-what` recovery command, but make user-facing output
  Chinese, keep local specs local by default, forbid implicit external writes,
  forbid implicit commits, and require bounded subagents.

The adapted files are intentionally short and are not a byte-for-byte vendor
copy. Any upstream update must record a new reviewed commit and re-check the
Codex-specific safety boundaries before changing this plugin.
