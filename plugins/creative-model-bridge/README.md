# Creative Model Bridge

Creative Model Bridge is an instruction-only Codex skill for fiction, scripts,
poetry, story development, rewriting, and revision. It contains no runtime,
MCP server, Pixi environment, daemon, or bundled provider client.

For creative writing, the skill tells Codex to:

- preserve the requested language, format, constraints, and supplied material;
- use `curl` or `curl.exe`, with no extra dependency or bundled client;
- reuse the active Codex provider's base URL, provider API key, and headers;
- call only the OpenAI-compatible streaming `POST /chat/completions` endpoint;
- try `gemini-3-pro`, `gemini-3-flash`, `deepseek-flash`, `deepseek-pro`,
  `gpt-5.6-terra`, `gpt-5.6-sol`, and `gpt-5.6-luna` in that fallback order;
- accept only `choices[].delta.content` as visible text, ignoring thinking and
  reasoning fields; and
- stop instead of retrying an ambiguous, authenticated, limited, timed-out, or
  partially completed request; and
- return only the model's final visible text verbatim.

Credentials stay within the active provider boundary. The skill never reads the
Codex/ChatGPT login session, asks for a separate creative-model key, creates
plaintext credential storage, or changes Codex configuration.

After installing or updating the plugin, start a new Codex task so the revised
skill instructions are loaded.
