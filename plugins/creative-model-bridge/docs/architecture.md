# Architecture and outbound boundary

The plugin bundles one self-contained executable. Its normal entry point is a
one-shot stdin/stdout CLI; no MCP server, Codex profile, daemon, or global
configuration entry is required. The CLI imports the stateless `Bridge` and
keeps the provider boundary in `transport_client.py`.

```text
Codex exec session
      │  request JSON on stdin
      ▼
ready → response metadata → bounded data frames
      │
      ▼
Bridge.call(operation, arguments)
      │
      ├─ creative_preview: validate/build, stop before credential/network
      ├─ creative_models: one GET /models
      └─ creative_generate: one streaming POST /chat/completions
```

Protocol v1 is newline-delimited JSON. A process emits exactly one `ready`
frame before config/provider setup. The caller sends one request envelope:

```json
{"protocol":1,"type":"request","id":"1","operation":"creative_generate","arguments":{"task":"..."}}
```

The response metadata frame declares `ok`, total UTF-8 `bytes`, `chunks`, and
the SHA-256 of the serialized result. Each `data` frame carries contiguous
zero-based `seq`, the serialized JSON substring, `chunk_sha256`, the overall
`sha256`, and `done`. Four-kilobyte UTF-8 chunks keep each NDJSON line bounded
even when a result exceeds 70,000 characters. No ACK is needed: stdin is one
request and stdout is one finite response.

The request builder remains unchanged: task, constraints, output specification,
labeled context text, and ordered context files form one deterministic user
prompt. The only optional system instruction is the exact documented Chinese
sentence. The provider receives one Chat Completions request with SSE parsing,
usage tail handling, and verbatim text extraction. No retry, provider/model
switch, hidden context, or session state is introduced by the CLI.

Credential precedence remains configured `env_key`, fixed
`CREATIVE_MODEL_API_KEY`, then development-only `experimental_bearer_token`
when no `env_key` is configured. Credentials are not placed in argv, protocol
frames, logs, prompts, or errors. A preview stops before credential resolution
and network I/O.

The POSIX and PowerShell launchers select a target asset, verify the published
SHA-256 checksum, and atomically publish an immutable v4 cache object plus
`active` pointer. Cached starts re-hash the executable and never download;
offline uncached or tampered starts fail. A bounded lock handles concurrent
cold starts and stale owners without deleting immutable objects. `cache` is a
non-interactive verification/warm-up action that exits before the CLI; the
metadata `install` hook performs that action and then calls
`migrate --codex-home <resolved CODEX_HOME>`, treating absent historical state
as success. A local executable override follows the same object/pointer/digest
path for `cache` and `install`, while `run` keeps its direct override behavior.

## One-time legacy cleanup

`mcp/migrate.py` is not a provisioner. Its `migrate` command (called by the
normal `install` hook, or explicitly for a direct run) accepts
only a historical CMB ownership marker whose install ID, runtime command,
`CODEX_HOME`, credential-channel list, and state file all agree. It writes a
byte-for-byte backup, atomically removes that marker/table and any pre-v4
`active` pointer, and rolls back on failure. It refuses repeated/mismatched
markers, foreign same-name entries, unrelated tables inside the marker, or
concurrent config edits. Current v4 pointers and all non-CMB configuration are
left untouched.
