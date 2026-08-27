---
name: research
description: Produce a bounded Chinese research note from primary sources when repository work depends on external facts.
---

# Research

Run when a current or external fact is necessary: an API, version, standard,
regulation, price, model capability, or behavior not established by the
repository.

## Procedure

1. State the narrow question, intended decision, source requirements, output
   path, and stop condition.
2. Use one bounded `default` subagent for independent source collection when
   the material is large. The subagent must not invoke `research` again or
   spawn children.
3. Prefer official documentation, specifications, source code, and first-party
   announcements. Record the page title, URL, publication/update date, and
   relevant scope for every material claim.
4. Write a Chinese Markdown report at the repository's established notes path;
   if none exists, use `.tool/research/<slug>.md` and report that path.
5. Separate verified facts, inference, uncertainty, and recommendations.

Research is evidence, not authority to expand a frozen product scope. Return the
report path and a short summary to the main thread; do not assume later agents
will load the report unless it is explicitly referenced.
