# Configuration reference

The script reads `config.toml` using `tomllib`. An embedding caller may pass an
explicit path; the normal path is `$CODEX_HOME/config.toml` when `CODEX_HOME` is
non-empty, otherwise `~/.codex/config.toml`.

```toml
[shell_environment_policy.set]
CREATIVE_MODEL_PROVIDER = "my-provider"

[model_providers.my-provider]
base_url = "https://provider.example/v1"
wire_api = "chat_completions" # "responses" remains accepted for compatibility
env_key = "MY_PROVIDER_API_KEY"
# experimental_bearer_token = "development-only-value"
```

`CREATIVE_MODEL_PROVIDER` selects the exact key under `model_providers`. A
request-supplied model is passed byte-for-byte; otherwise the built-in default
is `gemini-3-pro`. Model strings are opaque and are not normalized or looked
up. `base_url` must be an absolute HTTP(S) URL without embedded credentials,
query, or fragment.
`wire_api` may be `chat_completions` or legacy `responses`; both use the single
Chat Completions endpoint.

Credential precedence is:

1. the configured `env_key` when its environment variable is non-empty;
2. `CREATIVE_MODEL_API_KEY`;
3. `experimental_bearer_token`, only when no `env_key` is configured.

Missing credentials fail before network I/O. Tokens never appear in prompts,
results, diagnostics, or errors.
