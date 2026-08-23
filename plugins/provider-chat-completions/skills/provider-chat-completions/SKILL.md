---
name: provider-chat-completions
description: Call the effective Codex model provider's OpenAI-compatible Chat Completions API when a task explicitly needs a chosen model and caller-supplied messages; do not use for ordinary Codex responses or creative-writing guidance by itself.
---

# Provider Chat Completions

Use this skill as a transport utility, not as a writing or model-selection
policy.

1. Build the exact `messages` array required by the calling task. Preserve its
   roles, content, order, and named parameters; do not add a system prompt or
   read files unless the caller asked for that.
2. Set the requested model explicitly. Do not infer a vendor from its name and
   do not silently substitute another model.
3. Invoke `<plugin-root>/scripts/run.sh` on macOS/Linux or
   `<plugin-root>/scripts/run.ps1` on Windows with the JSON request on stdin. It
   resolves the current working directory's effective Codex provider and uses
   only that provider's configured origin, credential, and headers.
4. Treat the returned JSON as the transport result. On `ok: false`, report the
   safe failure boundary to the caller; do not retry, switch providers, or draft
   a replacement unless the caller's own policy explicitly says to do so.

The runtime makes one non-streaming `POST /chat/completions` call. It does not
assemble prompts, validate creative quality, expose reasoning fields, follow
redirects, or persist credentials. Do not pass a credential in the request or
ask the user to paste one.
