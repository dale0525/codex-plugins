---
name: provider-chat-completions
description: Call the active Codex provider's OpenAI-compatible Chat Completions API when a task explicitly needs a chosen model and caller-supplied messages; do not use for ordinary Codex responses or creative-writing guidance by itself.
---

# Provider Chat Completions

Use this skill as a transport utility for the effective Codex provider, not as a
writing or model-selection policy.

1. Build the exact `messages` array required by the calling task. Preserve its
   roles, content, order, and named parameters; do not add a system prompt or
   read files unless the caller asked for that.
2. Set the requested model explicitly. Do not infer a vendor from its name and
   do not silently substitute another model.
3. Invoke `<plugin-root>/scripts/run.sh` on macOS/Linux or
   `<plugin-root>/scripts/run.ps1` on Windows with the JSON request on stdin.
   The CLI reads the credential cache created by Codex Sync after a successful
   pull and uses only the cached provider origin, headers, and query parameters.
   It does not launch Codex app-server or parse the live Codex configuration.
   For any response that may exceed the tool display limit, create a private
   temporary path and pass `--output-file <absolute-path>` to the launcher. The
   launcher atomically writes the complete normalized result to that file and
   prints only a bounded manifest (`result_file`, byte count, and status) to
   stdout. Read the saved file with local file tools; never print or `cat` its
   full contents into the tool output. Keep the file until review and
   validation are complete.
4. Without capture mode, treat the returned JSON as the transport result. With
   capture mode, treat the bounded manifest as a handle and read `result_file`
   to obtain the complete transport result. On `ok: false`, report the safe
   failure boundary to the caller; do not retry, switch providers, or draft a
   replacement unless the caller's own policy explicitly says to do so.

## Credential cache

Codex Sync writes
`<CODEX_HOME>/plugins/cache/<marketplace>/provider-chat-completions/<version>/.codex-provider/credential.json`
after it applies synchronized provider settings and converges plugins. The
directory is owner-only (`0700` on POSIX) and the file is owner-only (`0600`);
the write is atomic and the cache never enters the synchronization repository.
The cache contains the active provider endpoint, configured headers, optional
environment-variable references, query parameters, and a non-secret
configuration fingerprint. A missing, malformed, symlinked, or weakly
permissioned cache fails as `credential_cache_missing`,
`credential_cache_invalid`, or `credential_cache_permissions`.

`env_key` and `env_http_headers` remain references and are resolved only from
the current process environment. Providers that expose only a Codex login
session or command-backed auth do not produce a cache and fail safely as
`credential_cache_missing` or `credential_unavailable`.

The runtime makes one non-streaming `POST /chat/completions` call. It does not
assemble prompts, validate creative quality, expose reasoning fields, follow
redirects, or persist credentials. Credential-bearing remote HTTP endpoints
and credential-like query parameters fail before any request; loopback HTTP is
reserved for an explicitly configured local gateway. Capture mode persists
only the normalized result requested by the caller, using owner-only file
permissions (including removing inherited Windows ACLs); it fails closed if
that restriction cannot be applied. It never writes authorization headers or provider diagnostics to that
file. Do not pass a credential in the request or ask the user to paste one.
