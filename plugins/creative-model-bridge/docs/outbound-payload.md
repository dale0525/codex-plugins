# Outbound payload boundary

For a request without a model, with any caller-supplied token limit, and with
minimal system mode, the one HTTP POST body is:

```json
{
  "model": "gemini-3-pro",
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
system message. A request model is passed byte-for-byte; otherwise
`gemini-3-pro` is used. Caller-provided `max_tokens` and `max_output_tokens`
values are ignored. The bearer is only an HTTP header. No hidden Codex prompt,
adapter, or conversation state is added.

SSE `data:` frames are parsed until `[DONE]`. Text from
`choices[0].delta.reasoning_content` (or the compatible `reasoning` key) is
accumulated into `reasoning`; text from `choices[0].delta.content` is
accumulated into `output`. Whitespace, newlines, and empty strings in output
are not trimmed or otherwise post-processed. A usage object, provider request
ID, and response model are retained when present.
