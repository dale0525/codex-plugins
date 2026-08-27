---
name: improve-codebase-architecture
description: Manually generate a read-only architecture report using deep-module and seam analysis.
disable-model-invocation: true
---

# Improve codebase architecture

This skill is manual-only. Run it only when the user explicitly asks for an
architecture review or refactoring candidates.

## Procedure

1. Confirm the requested repository or bounded area and the review snapshot.
2. Read relevant code, `CONTEXT.md`, ADRs, and recent history.
3. Use the vocabulary of module, interface, depth, seam, adapter, leverage,
   locality, and deletion of unnecessary tests.
4. Use one existing `technical_lead` or `default` subagent for bounded,
   read-only exploration when needed. It must not modify production code or
   spawn more agents.
5. Produce a self-contained report with evidence, candidate changes, expected
   benefits, risks, and confidence (`Strong`, `Worth exploring`, or
   `Speculative`).
6. Stop after the report. Do not refactor, edit `CONTEXT.md`, create an ADR, or
   start implementation until the user chooses a candidate explicitly.
