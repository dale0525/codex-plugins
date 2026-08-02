# Architecture and outbound boundary

The plugin has no bundled `.mcp.json`: its provision launcher downloads an
immutable binary and asks that binary to write one owner-marked global
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
        │  (optional exact minimal instructions)
        ▼
{ model, input, instructions?, max_output_tokens, temperature? }
        │
        ▼
configured-provider /responses
```

Only the provider's bearer credential is added as an HTTP `Authorization`
header. A bundled stdio server cannot dynamically add arbitrary `env_key` names
to its host declaration, so the host forwards the fixed
`CREATIVE_MODEL_API_KEY` channel and the selected provider `env_key`. These are never placed in the JSON payload, prompt
report, logs, or error messages. The bridge does not inject Codex instructions,
model-specific adapters, retries, provider switching, or conversation history.

Every provider request also carries the explicit honest `User-Agent`
`creative-model-bridge/0.1.5`. This stable product identifier is a transport
compatibility measure for provider edges that reject Python's default user
agent; it does not claim to be Codex and is not accompanied by Codex-,
originator-, or session-spoofing headers.

`creative_preview` stops before credential resolution and network I/O. This
makes the returned `payload` and `prompt_report` suitable for a local audit.
`creative_generate` resolves the credential immediately before one request and
returns the response text without post-processing.

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
