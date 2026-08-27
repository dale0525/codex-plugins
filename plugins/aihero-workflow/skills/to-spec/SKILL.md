---
name: to-spec
description: Turn an agreed multi-session decision into a local, reviewable Chinese spec.
---

# To spec

Run automatically only after the decision is settled and the work will outlive
one context window, session, or owner. If the work is small enough for one
session, skip this skill and implement directly.

## Procedure

1. Read the current conversation, applicable `AGENTS.md`, relevant code,
   `CONTEXT.md`, ADRs, and nearby specs.
2. Do not interview or make new product decisions. Surface missing decisions as
   an `INTERNAL_GATE` or ask the user only when authority is required.
3. Agree the smallest observable public seam before writing the document.
4. Write `docs/specs/<feature>.md` with:
   - problem and intended outcome;
   - in-scope behavior and explicit out-of-scope behavior;
   - constraints and compatibility requirements;
   - decisions already made, using repository terminology;
   - public seams and testing decisions;
   - observable acceptance checks;
   - risks, evidence gaps, and unresolved questions.
5. Return the absolute path and a concise Chinese summary. Do not start
   implementation automatically.

## External boundary

Writing the local spec is the default. Do not create, label, update, or close a
GitHub, Linear, Jira, or other external issue unless the user explicitly
authorizes that operation.
