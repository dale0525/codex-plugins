---
name: grill-with-docs
description: Clarify a repository change spanning multiple files or modules when unresolved goals, terminology, boundaries, observable behavior, or acceptance checks could materially change the solution or its architecture. Persist only durable terminology and hard-to-reverse decisions. Use for repository-backed ambiguity instead of grilling; skip small or already-specified changes and no-repository ideas.
---

# Grill with docs

Turn material repository ambiguity into a sufficiently defined change while
preserving only durable terminology and decisions.

## Procedure

1. Read the applicable `AGENTS.md`, relevant code, `CONTEXT.md`, and ADRs.
2. Read and follow the bundled [grilling procedure](../grilling/SKILL.md).
3. Use the bundled `domain-modeling` rules when a term is vague, overloaded, or
   inconsistent with the repository vocabulary.
4. Write a resolved term to the relevant `CONTEXT.md` immediately when the
   current mode permits repository mutation. Keep that file a glossary only;
   do not put specs, task lists, or implementation notes there. If the current
   mode forbids writes, do not silently defer it: record each qualifying term or
   decision as pending persistence, name the target `CONTEXT.md` path, state why
   it was not written and the recovery condition, and stop before taking any
   action that depends on the missing entry. When the task returns to a mode
   that permits mutation, its first action is to write and verify every pending
   entry, then continue.
5. Offer an ADR only when the decision is hard to reverse, surprising without
   context, and the result of a real trade-off. Do not create an ADR for every
   answer.
6. Stop after the requested change is sufficiently defined. If pending
   persistence remains, stop in an explicit `待持久化` state rather than
   claiming the task is complete, and report the recovery action. Do not
   implement, publish issues, commit, or broaden scope.

## Concurrency and reporting

- One writer owns a repository's `CONTEXT.md` and each ADR during a session.
- If another writer is active, stop before writing and report the conflict.
- At the end, report the outcome in Chinese, including files written, files not
  written because no decision met the persistence bar, every pending entry
  (term or decision, target path, reason, and recovery condition), unresolved
  questions, and the recommended next action. When pending entries exist, the
  recommended next action must be to return to a writable mode, persist and
  verify them first, then resume; do not report the interview as complete.
