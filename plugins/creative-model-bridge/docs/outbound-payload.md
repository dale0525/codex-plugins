# Outbound payload boundary

For a request with model `opaque/model`, default limits, and minimal system
mode, the one HTTP POST body is:

```json
{
  "model": "opaque/model",
  "messages": [
    {"role": "system", "content": "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"},
    {"role": "user", "content": "任务:\n..."}
  ],
  "max_tokens": 60000,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

`temperature` is added only when supplied. `system_mode: "none"` omits the
system message. The request model is passed byte-for-byte, and the bearer is
only an HTTP header. No hidden Codex prompt, adapter, or conversation state is
added.

SSE `data:` frames are parsed until `[DONE]`. Text from
`choices[0].delta.reasoning_content` (or the compatible `reasoning` key) is
accumulated into `reasoning`; text from `choices[0].delta.content` is
accumulated into `output`. Whitespace, newlines, and empty strings in output
are not trimmed or otherwise post-processed. A usage object, provider request
ID, and response model are retained when present.
