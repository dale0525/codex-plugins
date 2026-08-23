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
   On Windows, config discovery prefers `PROVIDER_CHAT_CODEX_BIN`, then the
   directly launchable helper at `%CODEX_HOME%\plugins\.plugin-appserver\codex.exe`
   or `%USERPROFILE%\.codex\plugins\.plugin-appserver\codex.exe`. It does not
   fall back to PATH or the Windows App Execution Alias named `codex`. An
   explicit override must be an absolute path on every platform.
4. Treat the returned JSON as the transport result. On `ok: false`, report the
   safe failure boundary to the caller; do not retry, switch providers, or draft
   a replacement unless the caller's own policy explicitly says to do so.

If a Windows helper launch is denied, the utility returns
`{"ok":false,"stage":"config","code":"codex_launch_denied","retryable":false}`
with only bounded, redacted startup diagnostics (executable path, Win32/OS
error, exit code, and stderr presence/byte-count metadata). Raw stderr text is
never returned. Never expose credentials, authorization headers, tokens, or
provider response bodies in diagnostics.

The runtime makes one non-streaming `POST /chat/completions` call. It does not
assemble prompts, validate creative quality, expose reasoning fields, follow
redirects, or persist credentials. Do not pass a credential in the request or
ask the user to paste one.
