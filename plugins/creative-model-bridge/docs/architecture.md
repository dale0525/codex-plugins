# Architecture and outbound boundary

`bin/creative-model-bridge` resolves the installed plugin root and invokes its
Python 3.11+ Pixi environment. `mcp/server.py` is a small newline-delimited
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
`CREATIVE_MODEL_API_KEY` channel. It is never placed in the JSON payload, prompt
report, logs, or error messages. The bridge does not inject Codex instructions,
model-specific adapters, retries, provider switching, or conversation history.

`creative_preview` stops before credential resolution and network I/O. This
makes the returned `payload` and `prompt_report` suitable for a local audit.
`creative_generate` resolves the credential immediately before one request and
returns the response text without post-processing.
