---
name: research
description: Produce a bounded Chinese research note from primary sources only when an external fact affects a repository decision and the evidence needs to be reusable beyond a one-time answer. Do not trigger for ordinary fact questions or small version checks.
---

# Research

Run when a current or external fact is necessary: an API, version, standard,
regulation, price, model capability, or behavior not established by the
repository.

## Procedure

1. State the narrow question, intended decision, source requirements, output
   path only if a durable artifact is needed, and stop condition.
2. Use one bounded `default` subagent for independent source collection when
   the material is large. The subagent must not invoke `research` again or
   spawn children.
3. Prefer official documentation, specifications, source code, and first-party
   announcements. Record the page title, URL, publication/update date, and
   relevant scope for every material claim.
4. Write a Chinese Markdown report only when it will be reused across sessions
   or by another agent. Use the repository's established notes path; if none
   exists, use `.tool/research/<slug>.md` and report that path. For a small
   check, return the evidence in the task response without creating a file.
5. Separate verified facts, inference, uncertainty, and recommendations.

Research is evidence, not authority to expand a frozen product scope. If
reliable sources are inaccessible or conflict, stop and report the evidence gap
and uncertainty instead of presenting a low-grade source as verified fact.
Return a report path only when one was written, plus a short summary to the main
thread; do not assume later agents will load the report unless it is explicitly
referenced.
