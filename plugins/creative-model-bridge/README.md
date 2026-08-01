# Creative Model Bridge

Creative Model Bridge exposes three stdio MCP tools backed by the OpenAI
Responses API shape:

- `creative_models` calls the configured provider's `/models` endpoint.
- `creative_preview` validates files and builds the exact outbound payload
  without network access.
- `creative_generate` makes one `/responses` request and returns generated text
  verbatim with usage, request ID, and a prompt report.

The server is launched from the installed plugin root by `.mcp.json` using the
relative `bin/creative-model-bridge` launcher. That launcher runs the bundled
plugin `pixi.toml` environment (Python 3.11–3.13) and forwards only
`CODEX_HOME` plus the fixed `CREATIVE_MODEL_API_KEY` credential channel, so the
same launcher works from a copied plugin cache.

## Configuration

The bridge reads `$CODEX_HOME/config.toml` with `tomllib`. The provider name and
default model are selected from:

```toml
[shell_environment_policy.set]
CREATIVE_MODEL_PROVIDER = "my-provider"
CREATIVE_MODEL_DEFAULT = "my-opaque-model"

[model_providers.my-provider]
base_url = "https://provider.example/v1"
wire_api = "responses"
env_key = "MY_PROVIDER_API_KEY"
# experimental_bearer_token = "development-only-value"
```

`wire_api` must be exactly `"responses"`. An explicitly supplied request model
overrides `CREATIVE_MODEL_DEFAULT` exactly; no model auto-selection or adapter
is performed. A bundled stdio MCP cannot dynamically forward arbitrary
provider-specific environment names from its host, so `.mcp.json` forwards one
fixed plugin channel: `CREATIVE_MODEL_API_KEY`. The Codex host must expose that
variable when the configured provider's `env_key` is not otherwise present.
Credential precedence is: the configured `env_key`, then
`CREATIVE_MODEL_API_KEY`, and finally `experimental_bearer_token` only when no
`env_key` is configured. Credentials never appear in tool results or errors.

## Materials and preview

`context_text` accepts ordered labeled blocks (`label` and `text`).
`context_files` accepts ordered absolute paths to regular text files only. Each
file is limited to 2 MiB and the total decoded file context to 180,000
characters. UTF-8, BOM UTF-16, and supported East Asian legacy encodings are
detected strictly; binary signatures and ambiguous byte streams are rejected.
No file is truncated or summarized. The final assembled user prompt, including
all task, constraints, output specification, and context sections, is also
limited to 180,000 characters. The prompt report records each resolved path,
decoded character count, encoding, and raw-byte SHA-256 digest.

## Audit boundary

The preview shows what this plugin would send, but it cannot audit provider-side
CPA routing, logging, retention, moderation, or model internals. Review the
provider's policy separately before sending sensitive material. The bridge does
not retry, switch providers, or hide additional prompts.
Provider requests identify themselves honestly as
`User-Agent: creative-model-bridge/0.1.2` for transport compatibility; no
Codex-specific identity or session headers are sent.

## Install and test

```bash
codex plugin add creative-model-bridge@dale0525-codex-plugins
pixi run creative-model-bridge-test
pixi run test
pixi run validate
```

Focused tests use an in-process mock HTTP opener; they never make a live CPA
request and contain no credentials.
