# Codex Plugins

Public Codex plugin marketplace maintained by [dale0525](https://github.com/dale0525).

## Install the marketplace

```bash
codex plugin marketplace add dale0525/codex-plugins
```

## Install Subagent Dispatch

```bash
codex plugin add subagent-dispatch@dale0525-codex-plugins
```

Start a new Codex task after installation. Review and trust the plugin hooks on each device; hook trust is intentionally device-local.

## Included plugins

### Subagent Dispatch

Routes delegated work to three native Codex profiles and synchronizes their TOML definitions at session start:

- `default`: read-heavy exploration and verification
- `creative_text`: fiction, scripts, poetry, and creative revision
- `image`: raster image generation, editing, inspection, and quality control

The plugin stores no credentials and only updates its three managed files under `$CODEX_HOME/agents/`. It does not delete unrelated agent profiles.

## Repository layout

```text
.agents/plugins/marketplace.json
plugins/subagent-dispatch/
```

## License

MIT
