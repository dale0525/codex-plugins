---
name: grill-with-docs
description: Clarify a repository change that spans multiple files or modules when unresolved goals, terminology, boundaries, or acceptance could change scope, behavior, architecture, or acceptance; persist only durable terminology and decisions. Skip small, already-specified changes and no-repository ideas.
---

# Grill with docs

Use when a repository change spans multiple files or modules and the goal,
terminology, boundaries, or acceptance criteria are materially unclear. Skip
for a small, already-specified change or for a no-repository idea.

## Procedure

1. Read the applicable `AGENTS.md`, relevant code, `CONTEXT.md`, and ADRs.
2. Use the bundled `grilling` procedure: ask related questions in rounds, wait
   for the user's answers, and show the current understanding after each round.
3. Use the bundled `domain-modeling` rules when a term is vague, overloaded, or
   inconsistent with the repository vocabulary.
4. Write a resolved term to the relevant `CONTEXT.md` immediately. Keep that
   file a glossary only; do not put specs, task lists, or implementation notes
   there.
5. Offer an ADR only when the decision is hard to reverse, surprising without
   context, and the result of a real trade-off. Do not create an ADR for every
   answer.
6. Stop after the requested change is sufficiently defined. Do not implement,
   publish issues, commit, or broaden scope.

## Concurrency and reporting

- One writer owns a repository's `CONTEXT.md` and each ADR during a session.
- If another writer is active, stop before writing and report the conflict.
- At the end, report the outcome in Chinese, including files written, files not
  written because no decision met the persistence bar, unresolved questions,
  and the recommended next action.
