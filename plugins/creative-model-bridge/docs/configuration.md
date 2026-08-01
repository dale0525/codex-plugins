# Configuration reference

The plugin reads `$CODEX_HOME/config.toml` using Python `tomllib`.

| Key | Required | Meaning |
| --- | --- | --- |
| `shell_environment_policy.set.CREATIVE_MODEL_PROVIDER` | yes | Exact key under `model_providers` to use |
| `shell_environment_policy.set.CREATIVE_MODEL_DEFAULT` | for omitted request model | Opaque default model identifier |
| `model_providers.<provider>.base_url` | yes | Responses-compatible API root |
| `model_providers.<provider>.wire_api` | yes | Must be exactly `responses` |
| `model_providers.<provider>.env_key` | no | Preferred provider environment variable; if unavailable, the fixed `CREATIVE_MODEL_API_KEY` channel is used |
| `CREATIVE_MODEL_API_KEY` | host channel | Fixed environment variable forwarded by `.mcp.json` for provider credentials |
| `model_providers.<provider>.experimental_bearer_token` | no | Development-only credential, used only without `env_key` and the fixed channel |

An explicit `model` in `creative_preview` or `creative_generate` wins over the
configured default byte-for-byte. The bridge never guesses a model from
`creative_models`; that tool reports only the provider's current `/models`
response.
