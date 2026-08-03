# Outbound payload boundary

`creative_preview` and `creative_generate` share the same request builder. For
the same arguments, the preview's `payload` is byte-for-byte equivalent after
JSON serialization to the body sent by `creative_generate`:

```json
{
  "model": "the-requested-or-configured-opaque-name",
  "input": "the deterministic user prompt",
  "instructions": "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。",
  "max_output_tokens": 60000
}
```

`instructions` is omitted for `system_mode: "none"`. `temperature` is added
only when the caller supplied it. The bearer token is an HTTP header and never
appears in this body, in the preview result, or in errors. The user prompt is
assembled in the fixed order `task` → `constraints` → `output_spec` →
`context_text` → `context_files`; there is no hidden Codex prompt or model
adapter. `prompt_report` records the exact system prompt (or `null`), section
order, user/total character counts, context details, and `truncated: false`.

The HTTP transport sends `User-Agent: creative-model-bridge/0.1.13` on both
`/models` and `/responses`. This is an honest bridge identifier used for edge
compatibility; no Codex-specific identity, originator, or session headers are
sent.

The provisioned MCP process receives `CODEX_HOME`, the fixed credential channel,
the selected provider `env_key`, and (when resolved) `SSL_CERT_FILE`. The CA
variable is ordered after credential entries and is never included in the JSON
payload or prompt report.
