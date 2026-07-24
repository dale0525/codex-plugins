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

## Install Apple Design

```bash
codex plugin add apple-design@dale0525-codex-plugins
```

## Install Codex Sync

```bash
codex plugin add codex-sync@dale0525-codex-plugins
```

Start a new Codex task, invoke `$codex-sync`, and connect a selected private
GitHub configuration repository. Codex Sync previews changes before it updates
global `AGENTS.md`, portable `config.toml` values, providers, marketplaces, or
plugins. The bundled GitHub App uses Device Flow. Provider credentials remain
device-local by default; an explicitly configured `experimental_bearer_token`
can be synchronized in plaintext through the private repository.

Apple Design packages seven MIT-licensed design-engineering skills from
[emilkowalski/skills](https://github.com/emilkowalski/skills). The upstream
license is preserved in the plugin's `third-party/` directory.

## Included plugins

### Subagent Dispatch

Routes delegated work to three native Codex profiles and synchronizes their TOML definitions at session start:

- `default`: read-heavy exploration and verification
- `creative_text`: fiction, scripts, poetry, and creative revision
- `image`: raster image generation, editing, inspection, and quality control

The plugin stores no credentials and only updates its three managed files under `$CODEX_HOME/agents/`. It does not delete unrelated agent profiles.

### Apple Design

Provides Apple-inspired interface design, animation vocabulary, animation
planning and review, design-engineering guidance, and UI library selection.

### Codex Sync

Bootstraps new devices from a private GitHub repository through GitHub App
device authorization. It applies configuration with managed-key ownership,
atomic writes, drift detection, scoped secret policy, pre-apply backups, and
rollback. Private marketplaces are downloaded at immutable commit SHAs and
registered as local versioned snapshots instead of exposing GitHub credentials
to Git subprocesses.

## External content synchronization

The `Sync external skills and plugins` GitHub Actions workflow runs daily at
17:23 UTC (01:23 China Standard Time) and can also be started manually. It:

1. Reads external Git sources from `sync-sources.toml`.
2. Copies the configured skill or plugin directory and preserves its license.
3. Applies declared compatibility normalization, records the upstream commit in
   `sync-lock.json`, and increments the affected plugin's patch version when its
   packaged content changes.
4. Runs the repository tests and validators with pixi.
5. Creates or refreshes `codex/sync-apple-design` as a reviewable pull request.

The workflow intentionally does not merge upstream changes automatically.
Skills can change agent behavior, so each synchronization pull request is a
supply-chain review gate. Repository settings must allow GitHub Actions to
create pull requests with the `GITHUB_TOKEN`.

To add another source, append a `[[sources]]` entry to `sync-sources.toml`.
The source may point at a skills directory or a complete plugin directory; the
destination must remain inside this repository.

## Repository layout

```text
.agents/plugins/marketplace.json
plugins/apple-design/
plugins/codex-sync/
plugins/subagent-dispatch/
sync-sources.toml
```

## License

MIT
