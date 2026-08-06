# Outbound payload boundary

`creative_preview` and `creative_generate` share the same request builder. For
the same arguments, the preview's `payload` is byte-for-byte equivalent after
JSON serialization to the body sent by `creative_generate`:

```json
{
  "model": "the-requested-or-configured-opaque-name",
  "messages": [
    {"role": "system", "content": "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"},
    {"role": "user", "content": "the deterministic user prompt"}
  ],
  "max_tokens": 60000,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

The system message is omitted for `system_mode: "none"`; `temperature` is
added only when supplied. The bearer token is an HTTP header and never appears
in this body, protocol frames, or errors. Prompt sections are ordered `task` →
`constraints` → `output_spec` → `context_text` → `context_files`; there is no
hidden Codex prompt or model adapter.

The CLI's protocol v1 wraps the compact serialized result in bounded NDJSON
frames. The response frame declares overall `sha256`, UTF-8 byte count, and
chunk count. Data frames carry `seq`, `data`, per-chunk `chunk_sha256`, the same
overall digest, and `done` on the final sequence only. The caller must verify
all fields and then return the result `text` verbatim.
