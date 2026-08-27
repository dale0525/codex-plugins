---
name: writing-for-agents
description: Review agent-facing documents for predictable behavior, low context load, and complete stopping rules.
---

# Writing for agents

Run automatically when a non-trivial `AGENTS.md`, `SKILL.md`, spec, prompt,
README, or other agent-facing document is created or edited. Small edits can use
the checklist in the global `AGENTS.md` without invoking this full review.

## Review checklist

- Every sentence changes agent behavior; remove no-op explanation.
- Each fact has one source of truth; remove duplicated or stale wording.
- Put high-frequency rules in the current file and low-frequency detail behind
  a clear context pointer.
- State trigger, inputs, outputs, stopping conditions, side effects, and
  observable acceptance checks.
- Keep behavior-changing leading words and concrete failure boundaries.
- Do not optimize for line count by deleting required behavior.
- Separate user-invoked orchestration from model-invocable reusable discipline.
- Keep user-facing output in Chinese unless code, commands, or quoted source
  requires another language.

For a high-impact global-document change, use one existing `reviewer` subagent
with the exact diff and this checklist. Return findings with file/line evidence;
do not rewrite unrelated documents.
