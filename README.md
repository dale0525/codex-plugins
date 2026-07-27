# Codex Plugins

Public Codex plugin marketplace maintained by [dale0525](https://github.com/dale0525).

## Install the marketplace

```bash
codex plugin marketplace add dale0525/codex-plugins
```

## Install Apple Design

```bash
codex plugin add apple-design@dale0525-codex-plugins
```

## Install Codex Sync

```bash
codex plugin add codex-sync@dale0525-codex-plugins
```

## Install FastCtx

```bash
codex plugin add fastctx@dale0525-codex-plugins
```

## Install Film Craft Orchestrator

```bash
codex plugin add film-craft-orchestrator@dale0525-codex-plugins
```

## Install Web Novel Craft

```bash
codex plugin add web-novel-craft@dale0525-codex-plugins
```

Start a new Codex task, invoke `$codex-sync`, and connect a selected private
GitHub configuration repository. Codex Sync previews changes before it updates
global `AGENTS.md`, native agent profiles, portable `config.toml` values,
providers, marketplaces, or plugins. It uses no lifecycle hooks. The bundled
GitHub App uses Device Flow. A complete provider definition, including an
explicitly configured plaintext `experimental_bearer_token`, can be synchronized
through the private repository for zero-setup use on every device.

Codex Sync 0.4.1 launches reviewed Windows plugin provisioners with a
process-scoped PowerShell execution-policy bypass, avoiding AuthorizationManager
failures without changing user or machine policy. Codex Sync 0.4.0 preserves
declared external marker sections in global `AGENTS.md` while keeping the
repository copy canonical. It can also run a
plugin's reviewed high-risk provisioner after installation when
`auto_provision = true`. The bundled FastCtx plugin uses this path to download
a locked native release, verify its SHA-256 digest, enable repository and Bash
MCP tools, and apply the integration without a global npm installation. Codex
Sync 0.3.5 captures `plugins.toml` as the complete desired installed set,
removing absent or disabled plugin entries instead of retaining `enabled = false`
tombstones. Legacy disabled entries remain readable for backward compatibility.
Codex Sync 0.3.4 runs Codex child processes from a stable configuration
directory so refreshing the marketplace that contains Codex Sync cannot invalidate
their working directory. Codex Sync 0.3.3 refreshes an unchanged Git marketplace
in place instead of destructively unregistering it first, preserving installed plugin paths during
configuration apply. Codex Sync 0.3.2 made agent-driven onboarding non-blocking:
the device-login URL and one-time code are flushed before authorization polling begins. On
Windows, the engine probes `codex.exe`, `codex.cmd`, and `codex.bat` launchers,
skips broken PATH entries, and uses the same resolved CLI for diagnostics and
synchronization, so setup no longer needs a temporary hard link or PATH change.

When asked to upload the current device configuration, Codex Sync first
captures current values for already managed settings, complete providers, global
instructions, synchronized profiles, and installed non-OpenAI plugins into the
local repository cache. It excludes app-managed `openai` and `openai-*`
marketplaces and plugins, shows the resulting diff, and publishes only after
explicit approval. Portable HTTPS Git marketplaces can be captured automatically;
local-only plugin sources are reported and skipped because their code cannot be
restored on another device.

Apple Design packages seven MIT-licensed design-engineering skills from
[emilkowalski/skills](https://github.com/emilkowalski/skills). The upstream
license is preserved in the plugin's `third-party/` directory.

## Included plugins

### Apple Design

Provides Apple-inspired interface design, animation vocabulary, animation
planning and review, design-engineering guidance, and UI library selection.

### Codex Sync

Bootstraps new devices from a private GitHub repository through GitHub App
device authorization. It synchronizes global instructions and shared native
agent profiles, including `default`, `creative_text`, and `image`, without
lifecycle hooks. Configuration is applied with managed ownership, atomic writes,
drift detection, scoped secret policy, pre-apply backups, and rollback. Private
marketplaces are downloaded at immutable commit SHAs and registered as local
versioned snapshots instead of exposing GitHub credentials to Git subprocesses.

### FastCtx

Packages the native [yc-duan/fastctx](https://github.com/yc-duan/fastctx)
runtime for reviewed, npm-free provisioning. FastCtx supplies structured local
file reading, search, discovery, mechanical replacement, Bash execution, and
persistent background jobs through MCP. Release metadata and supported-platform
archive digests are synchronized daily behind the pull-request supply-chain
review gate.

FastCtx and Codex Sync have explicit ownership boundaries:

| Location | Owner | Managed content |
| --- | --- | --- |
| `~/.codex/config.toml` | FastCtx | `mcp_servers.fastctx`, its token-budget environment, the `mcp__fastctx` member of `features.code_mode.direct_only_tool_namespaces`, and `tool_output_token_limit` |
| `~/.codex/AGENTS.md` | Codex Sync + FastCtx | Codex Sync owns the canonical base; FastCtx owns only `<!-- fastctx:begin -->` through `<!-- fastctx:end -->` |
| `~/.fastctx/bin/fastctx` | FastCtx | The checksum-verified native executable |
| `~/.fastctx/config.toml` | FastCtx | Shell enablement, tier and budgets, update preferences, and Apply rollback metadata |
| `~/.fastctx/jobs/` | FastCtx | Created on demand for persistent Bash jobs |
| `~/.fastctx/portable-git/` | FastCtx plugin | Windows-only, checksum-verified GNU Bash fallback when Git Bash is unavailable |
| User `FASTCTX_BASH` | FastCtx plugin | Windows-only and set only for the managed fallback; Unapply removes it only if unchanged |

Do not duplicate the FastCtx-owned Codex keys in Codex Sync's common or device
configuration. On Windows, FastCtx shell tools run through Git Bash and the
FastCtx-owned AGENTS block requires POSIX Bash syntax for those tools. A private
canonical AGENTS file can therefore remove a blanket PowerShell-only rule when
FastCtx is enabled; this public repository cannot rewrite that private file.
When no standalone Git Bash exists, the plugin downloads the locked Git for
Windows `tar.bz2` runtime recorded in `windows-bash-runtime.json`, verifies its
size and SHA-256 digest, and installs it under `~/.fastctx` instead of a project
`.tool/` directory or a system-wide location. The bzip2 format avoids the LZMA
compatibility gap in older Windows `tar.exe` builds.

#### Migrate from Subagent Dispatch

Before applying Codex Sync 0.2.0 on an existing configuration repository:

1. Set `schema_version = 2` and `agent_profiles = "agents"` in `codex-sync.toml`.
2. Merge the `Subagent orchestration` section from the template `AGENTS.md` into the private repository and remove instructions that require loading the retired `subagent-dispatch` skill.
3. Copy the three profile templates from `plugins/codex-sync/templates/config-repository/agents/` into the private repository's `agents/` directory.
4. Keep every complete custom provider definition in `providers.toml`. Do not add partial `model_providers` tables to agent profiles; omitted provider settings inherit from the synchronized parent configuration.
5. Remove `subagent-dispatch@dale0525-codex-plugins` from the private repository's `plugins.toml`.
6. Publish the private repository change, run the reviewed `sync` and `apply` workflow on each device, then start a new Codex task.

The 0.2.0 apply transaction takes ownership of the synchronized profile filenames while preserving unrelated files under `$CODEX_HOME/agents/`.

### Film Craft Orchestrator

Provides one end-to-end orchestration skill and eight focused skills for source
adaptation, screenwriting, directing, cinematography, AI-video production,
continuity and quality control, editing and sound, and timestamped video-evidence
research. Shared templates, evidence corpora, compilers, and deterministic
validators remain in one canonical skill root so the focused skills do not drift.

### Web Novel Craft

Provides one writer/editor orchestration skill and eight focused skills for web-novel
development, long-form structure, characters and viewpoint, progression and
LitRPG systems, Chinese mainstream genres, scene prose, direct writing,
evidence-based critique, revision, evaluation, and video-evidence research. It
deliberately excludes scheduling, backlog, team, publishing, provider, and
engineering-oriented production management. Its shared corpus deeply distills 67
YouTube and Bilibili sources into timestamped claims, boundaries,
counterexamples, and executable procedures without distributing raw captions.
One source retains an explicitly excluded, uncharacterized media-tail gap rather
than claiming unsupported transcript coverage.

## External content synchronization

The `Sync external skills and plugins` GitHub Actions workflow runs daily at
17:23 UTC (01:23 China Standard Time) and can also be started manually. It:

1. Reads external Git-tree and GitHub Release sources from `sync-sources.toml`.
2. Copies configured skill/plugin directories and verifies every required
   GitHub Release asset against both `SHA256SUMS` and the GitHub asset digest.
3. Applies declared compatibility normalization, records immutable commits,
   release tags, asset digests, and destinations in `sync-lock.json`, and
   increments an affected plugin's patch version when packaged content changes.
4. Runs the repository tests and validators with pixi.
5. Creates or refreshes `codex/sync-external-content` as a reviewable pull request.

The workflow intentionally does not merge upstream changes automatically.
Skills can change agent behavior, so each synchronization pull request is a
supply-chain review gate. Repository settings must allow GitHub Actions to
create pull requests with the `GITHUB_TOKEN`.

To add another Git-tree source, append a `[[sources]]` entry. To track a stable
GitHub Release, append `[[github_releases]]` with an explicit required-asset
set and checksum asset. Every destination must remain inside this repository.

## Repository layout

```text
.agents/plugins/marketplace.json
plugins/apple-design/
plugins/codex-sync/
plugins/fastctx/
plugins/film-craft-orchestrator/
plugins/web-novel-craft/
sync-sources.toml
```

## License

MIT
