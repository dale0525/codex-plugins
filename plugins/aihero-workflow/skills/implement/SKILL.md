---
name: implement
description: Implement an authorized frozen plan test-first without committing or changing external trackers.
---

# Implement

Run automatically only when the user has requested a code change, the outcome
and acceptance contract are frozen, and no `USER_CHOICE_REQUIRED` or
`HARD_BLOCK` remains. This skill executes a decision; it does not redesign it.

## Procedure

1. Read the local spec, ticket, or the explicitly frozen plan and restate the
   allowed behavior delta and owned files.
2. Confirm the public seam and write the smallest failing test at that seam.
3. Implement one red-green-refactor slice at a time.
4. Run type checks and focused tests during the work, then run the full relevant
   suite once at the end.
5. Report changed files, test commands/results, evidence gaps, and any finding
   that requires reopening the plan.

## Boundaries

- Do not silently broaden scope or substitute a different design.
- Do not commit, amend, push, create a pull request, close a ticket, or change
  an external tracker.
- Handle one clearly identified ticket or small behavior delta per run.
- If the plan is inconsistent or the acceptance check is not executable, stop
  and report the gate instead of guessing.
- For work large enough to isolate, use one existing `builder` subagent with a
  self-contained assignment; it must not spawn another implementer.
