---
name: grilling
description: Run a bounded, multi-question decision interview for an idea or requested outcome with no repository change in scope when unresolved goals, recipients, boundaries, observable behavior, or acceptance checks could materially change the result. Skip repository changes, ordinary implementation planning, and requests whose decisions are already stable.
---

# Grilling primitive

Use this as a standalone decision interview and as the interview primitive for
`grill-with-docs`. It is not a product implementation step.

## Interaction surface

- When the `request_user_input` tool is available, use it for each decision
  round rather than asking the round in a normal message.
- Put 1–3 related questions into one tool call. The client may present them
  sequentially; do not split one round across multiple tool calls.
- Give every tool question 2–3 meaningful, mutually exclusive options, put the
  best suggestion first, and append `(Recommended)` to that option's label.
  Keep the client-provided free-form `Other` option available. Keep headers
  short and option labels to 1–5 words.
- Keep each native `questions[].question` as one short Chinese sentence. Put
  its required example inside that same sentence as a parenthetical
  `（例如……）`; do not append a second sentence or invent a separate example
  field. A valid call shape is:

  ```text
  request_user_input({
    questions: [{
      header: "首批用户",
      question: "这次先让谁使用（例如客服每天登记问题，还是经理偶尔查看）？",
      options: [
        { label: "客服先用 (Recommended)", description: "先验证每天登记是否顺手。" },
        { label: "经理先看", description: "先验证汇总查看是否有用。" }
      ]
    }]
  })
  ```

  Replace the demonstration facts with facts from the current project.
- Treat the returned answers as authoritative, reflect them in the updated
  understanding, and only then choose the next frontier of questions.
- If the tool is unavailable, ask the same coherent round in a normal message.
  In that fallback, put the short `例如：...` example immediately after the
  question as described below. Ask directly in chat only when an important
  question cannot reasonably be represented by meaningful choices.

## Procedure

1. State the current decision, scope, and what is already known.
2. Ask one coherent round of related, high-leverage questions in Chinese.
   Multiple questions in a round are intentional; batch them in one
   `request_user_input` call when available. The client may show them one at a
   time, but do not split one round across calls. Apply the question rules
   below to every question before sending the round.
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
- For a native tool question, include one short, simple project example inside
  the same sentence using `（例如……）`; keep one question mark and no second
  sentence. For a normal-message fallback, put the example immediately after
  the question using `例如：...`. The example must come from the project's
  stated goal, people, feature, file, or workflow, and must make the question
  easier to answer rather than suggest an answer.
- Do not invent project facts for an example. If the project context is too
  thin, first ask for the missing context in plain language and use the
  user's own goal or wording as the example; replace it with a concrete
  project example in the next round.
- Before sending a round, check each question: a non-technical reader can
  understand it, it has exactly one project-related example, and the example
  does not hide a decision or combine several questions. Native questions must
  also be one sentence; fallback questions must use the separate `例如：...`
  line.
- A useful shape is:
  `问题：这次先让谁使用？`
  `例如：如果这是记录客户问题的小工具，可以说明是客服每天登记，还是经理偶尔查看。`
  Replace this demonstration with facts from the current project.

Never invent an answer on the user's behalf. If a question cannot be settled by
conversation, recommend `prototype` or `research` instead of guessing.
