---
name: domain-modeling
description: Resolve an ambiguous or conflicting repository term when its meaning could change scope, behavior, architecture, or acceptance, and maintain a glossary without turning it into a spec. Do not trigger for stable, unambiguous new names.
---

# Domain modeling

Run when a new domain term appears, an existing term is overloaded, a synonym
conflicts with `CONTEXT.md`, or code and documentation disagree. Do not run for
ordinary edits with stable vocabulary.

## Procedure

1. Read the applicable `CONTEXT.md`, `CONTEXT-MAP.md`, ADRs, and the relevant
   code before proposing a word.
2. State the candidate term, meaning, rejected synonyms, and one concrete
   scenario that tests its boundary.
3. Resolve the term against the user and repository evidence; never silently
   rename an established concept.
4. Write only the concise term definition and rejected synonyms to the relevant
   `CONTEXT.md` at the moment it is resolved.
5. Offer an ADR only when the decision is hard to reverse, surprising without
   context, and based on a real trade-off.

Do not search or modify the issue tracker automatically. Report possible prior
decisions or naming collisions to the main thread for verification.
