# Creative Model Bridge

Creative Model Bridge is an instruction-only Codex skill for fiction, scripts,
poetry, story development, rewriting, and revision. It contains no runtime,
MCP server, Pixi environment, daemon, or bundled provider client.

For creative writing, the skill tells Codex to:

- preserve the requested language, format, constraints, and supplied material;
- use the user's exact model name or default to `gemini-3-pro`;
- reuse the active Codex provider's endpoint, authentication, and headers;
- try OpenAI-compatible Chat Completions first, then Responses, then a safely
  identified provider-native format within the same provider origin;
- stop instead of blindly retrying ambiguous, authenticated, limited, or
  partially completed requests; and
- return only the model's final visible text verbatim.

Credentials stay within the active provider boundary. The skill never asks for
a separate creative-model key, creates plaintext credential storage, or changes
Codex configuration.

After installing or updating the plugin, start a new Codex task so the revised
skill instructions are loaded.
