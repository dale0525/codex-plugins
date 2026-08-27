---
name: wait-what
description: Re-explain the previous answer in plain Chinese when the user did not follow it.
disable-model-invocation: true
---

# Wait, what?

Use `/wait-what` when the user signals that the previous explanation did not
land. Re-pitch the relevant answer rather than merely repeating or truncating
it.

## Response rules

- Restore the missing premise, context, or decision boundary.
- Use the project's `CONTEXT.md` vocabulary when it exists.
- Prefer plain Chinese and concrete examples; preserve exact code, commands,
  API names, and quoted source text.
- Be shorter only after making the explanation clearer. Do not collapse it into
  a context-free summary or a terse list.
- Do not introduce new decisions, broaden scope, or change the previous answer's
  commitments while re-explaining it.

The global `AGENTS.md` may invoke this behavior automatically when confusion is
detected; this skill remains available for explicit `/wait-what` use.
