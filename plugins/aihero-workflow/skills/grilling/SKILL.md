---
name: grilling
description: Run a bounded, multi-question decision interview without silently deciding for the user. Use only when explicitly invoked as the internal primitive of grill-with-docs or when the user explicitly requests a decision interview; do not trigger for ordinary implementation planning.
---

# Grilling primitive

Use this as the interview primitive for `grill-with-docs`. It is not a product
implementation step.

## Procedure

1. State the current decision, scope, and what is already known.
2. Ask one coherent round of related, high-leverage questions in Chinese.
   Multiple questions in a round are intentional; do not turn this into a
   one-question-at-a-time interview. Apply the question rules below to every
   question before sending the round.
3. Wait for the user's answers before asking the next round.
4. Reflect the updated understanding, list remaining uncertainty, and choose
   the next frontier of questions.
5. Stop when the requested outcome, boundaries, observable behavior, and
   acceptance checks are stable, or when the user says to stop.

## Question rules

- Write every user-facing question in everyday Chinese. Do not put technical
  terms such as API, schema, dependency, module, interface, architecture, or
  deployment in the question. If the project has a necessary proper name,
  keep the name and explain it in ordinary words.
- Put one short, simple example immediately after every question, using the
  format `例如：...`. The example must come from the project's stated goal,
  people, feature, file, or workflow, and must make the question easier to
  answer rather than suggest an answer.
- Do not invent project facts for an example. If the project context is too
  thin, first ask for the missing context in plain language and use the
  user's own goal or wording as the example; replace it with a concrete
  project example in the next round.
- Before sending a round, check each question: a non-technical reader can
  understand it, it has exactly one project-related example, and the example
  does not hide a decision or combine several questions.
- A useful shape is:
  `问题：这次先让谁使用？`
  `例如：如果这是记录客户问题的小工具，可以说明是客服每天登记，还是经理偶尔查看。`
  Replace this demonstration with facts from the current project.

Never invent an answer on the user's behalf. If a question cannot be settled by
conversation, recommend `prototype` or `research` instead of guessing.
