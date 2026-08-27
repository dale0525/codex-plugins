---
name: grilling
description: Run a bounded, multi-question decision interview without silently deciding for the user.
---

# Grilling primitive

Use this as the interview primitive for `grill-with-docs`. It is not a product
implementation step.

## Procedure

1. State the current decision, scope, and what is already known.
2. Ask one coherent round of related, high-leverage questions in Chinese.
   Multiple questions in a round are intentional; do not turn this into a
   one-question-at-a-time interview.
3. Wait for the user's answers before asking the next round.
4. Reflect the updated understanding, list remaining uncertainty, and choose
   the next frontier of questions.
5. Stop when the requested outcome, boundaries, observable behavior, and
   acceptance checks are stable, or when the user says to stop.

Never invent an answer on the user's behalf. If a question cannot be settled by
conversation, recommend `prototype` or `research` instead of guessing.
