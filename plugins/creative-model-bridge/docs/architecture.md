# Architecture and outbound boundary

The plugin manifest declares no local MCP companion. The global provision
launcher downloads an immutable binary and asks that binary to write one
owner-marked global
`[mcp_servers.creative-model-bridge]` entry to `$CODEX_HOME/config.toml`.
`mcp/server.py` is a small newline-delimited
JSON-RPC adapter. It exposes
`tools/list` and dispatches `tools/call` to the stateless `Bridge` class in
`mcp/bridge.py`. The bridge reads configuration for each call, validates all
materials, builds one deterministic request body, and then optionally invokes
the provider transport.

The outbound boundary is intentionally narrow:

```text
task + labeled text blocks + ordered file text
        │
        ▼
deterministic user input string
        │  (optional exact minimal system message)
        ▼
{ model, messages, max_tokens, stream: true, stream_options: { include_usage: true }, temperature? }
        │
        ▼
configured-provider /chat/completions (SSE; JSON fallback when media type is not SSE)
```

Only the provider's bearer credential is added as an HTTP `Authorization`
header. The provisioned entry forwards the fixed `CREATIVE_MODEL_API_KEY`
channel and explicit runtime/CA override channels; it additionally appends the selected provider
`env_key`. These are never placed in the JSON payload, prompt report, logs, or
error messages. The bridge does not inject Codex instructions, model-specific
adapters, retries, provider switching, or conversation history.

Every provider request also carries the explicit honest `User-Agent`
`creative-model-bridge/0.1.18`. This stable product identifier is a transport
compatibility measure for provider edges that reject Python's default user
agent; it does not claim to be Codex and is not accompanied by Codex-,
originator-, or session-spoofing headers.

`creative_preview` stops before credential resolution and network I/O. This
makes the returned `payload` and `prompt_report` suitable for a local audit.
`creative_generate` resolves the credential immediately before one request and
returns only `choices[0].delta.content` fragments (or the JSON fallback's
`choices[0].message.content`) without post-processing. Reasoning, tool calls,
refusals, and other non-text fields never enter the returned body. SSE parsing
uses an incremental UTF-8 decoder, accepts LF/CRLF and multi-line data, waits
for usage-only tail chunks, and requires a finish reason before natural EOF.

The POSIX bootstrap and PowerShell 5.1 provisioner select one of five release
assets for macOS arm64/x64, Linux arm64/x64, or Windows x64. They download only
the fixed versioned GitHub Release URL, strictly parse the lowercase digest for
the expected filename, then atomically rename a same-filesystem temporary file
into the version-bound v4 target cache (`v<version>/objects/<target>/<sha256>/<generation>/binary[.exe]` plus
`complete` and an atomic `active` pointer), alongside immutable digest content.
Every cached start re-hashes the executable; a bounded cross-process lock with
lock-internal second check handles concurrent cold starts and stale locks, so a
partial file is never published or executed. Cached starts do not use the
network; offline uncached or tampered starts fail. Bootstrap never GC's old
objects, so a Windows executable that is still running is never overwritten or
deleted when a new digest becomes active. Provision setup/status/repair/
uninstall use a lock, owner marker, state file, journal, and config
compare-and-swap. Provision state schema 2 has explicit installed/uninstalled
states; strict UUID marker pairs reject malformed, repeated, nested, or
mismatched ownership. Healthy setup/repair are no-ops, repair preserves
outside edits, and uninstall removes only the owned block. A schema-2 WAL
stores before/after bytes and digests for config and state; unknown external
edits retain the WAL and report pending manual recovery. A foreign same-name table hard fails. Build-time Pixi is
locked; target machines do not run Pixi or Python. Windows is tested by the
native workflow matrix and has not been run on this local host.

The canonical install and download locks contain exactly one `owner.<token>`
marker. A stale owner is moved by atomic rename to an isolated retired path;
live owners are never reclaimed, and release removes only its own lock.

Standard stdio startup also resolves trust before constructing the bridge or
reading a request: it reuses `provision.resolve_ssl_cert_file`, sets
the resolver's selected path into `SSL_CERT_FILE` when a CA is selected (so the
plugin-specific alias also wins in urllib), and leaves Windows on native trust
by default. Provision-time trust resolution is deterministic and
happens before lock/state writes: explicit absolute CA files are validated for readability, non-empty
regular-file type; macOS uses `/etc/ssl/cert.pem`, Linux uses a fixed ordered
candidate list, and Windows preserves the platform trust store by omitting
`SSL_CERT_FILE` unless explicitly selected. The optional state `ssl_cert_file`
and managed block digest make trust drift observable. Missing trust material
does not block uninstall. A consistent prior 0.1.5 through 0.1.16 owned image
is recognized as a migration input and rewritten as one setup transaction. The
0.1.16 legacy state accepts `ssl_cert_file` on POSIX and omits it on Windows,
with no extra fields.
Ownership removal is a strict line-span operation over the two
canonical CMB tables and marker lines, so expanded marker regions retain
unrelated tables/comments verbatim; foreign, quoted, repeated, nested, or
tampered images remain fail-closed.
