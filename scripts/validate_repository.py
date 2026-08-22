#!/usr/bin/env python3
"""Validate marketplace, plugin, skill, sync, and workflow structure."""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".agents/plugins/marketplace.json"
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:\+codex\.[0-9A-Za-z.-]+)?$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PIXI_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORKFLOW_ACTION_REF_PATTERN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
CREATIVE_SKILL_REQUIRED_MARKERS = (
    "`gemini-3-pro`",
    "`gemini-3-flash`",
    "`deepseek-flash`",
    "`deepseek-pro`",
    "`gpt-5.6-terra`",
    "`gpt-5.6-sol`",
    "`gpt-5.6-luna`",
    "`experimental_bearer_token`",
    "Do not read `auth.json`",
    "`curl`",
    '"stream": true',
    "`POST /chat/completions`",
    "`choices[].delta.content`",
    "`data: [DONE]`",
    "`reasoning_content`",
    "Keep every credential in process memory",
    "Send credentials only to the exact origin",
    "Codex's own cross-platform effective-configuration",
    "Do not parse `config.toml` with the system `python3`",
    "single preflight",
    "this is not a model",
    "Treat every other model-attempt outcome as an unsuccessful attempt",
    "continue with the next candidate",
    "HTTP response is 2xx",
    "normal text-completion `finish_reason`",
    "non-whitespace text",
    "protocol error remains a failure",
    "Do not fallback after an explicit user cancellation",
    "Disable automatic redirects",
    "Discard all visible text",
    "Return the concatenated visible text",
)


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.plugin_count = 0
        self.skill_count = 0

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _read_json(path: Path, validation: Validation) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        validation.error(f"{path.relative_to(ROOT)}: invalid JSON: {error}")
        return None
    if not isinstance(value, dict):
        validation.error(f"{path.relative_to(ROOT)}: root must be an object")
        return None
    return value


def _frontmatter(path: Path, validation: Validation) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        validation.error(f"{path.relative_to(ROOT)}: missing YAML frontmatter")
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        validation.error(f"{path.relative_to(ROOT)}: unterminated YAML frontmatter")
        return None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        validation.error(f"{path.relative_to(ROOT)}: invalid YAML: {error}")
        return None
    if not isinstance(data, dict):
        validation.error(f"{path.relative_to(ROOT)}: frontmatter must be an object")
        return None
    return data


def _validate_local_links(path: Path, validation: Validation) -> None:
    text = path.read_text(encoding="utf-8")
    for target in LOCAL_LINK_PATTERN.findall(text):
        clean_target = target.split("#", 1)[0]
        if not clean_target or "://" in clean_target or clean_target.startswith(("#", "mailto:")):
            continue
        linked = (path.parent / clean_target).resolve()
        if not linked.exists():
            validation.error(
                f"{path.relative_to(ROOT)}: broken local link: {target}"
            )


def _validate_skill(skill_directory: Path, validation: Validation) -> None:
    skill_path = skill_directory / "SKILL.md"
    metadata = _frontmatter(skill_path, validation)
    if metadata is None:
        return
    extra = set(metadata) - {"name", "description"}
    missing = {"name", "description"} - set(metadata)
    if extra:
        validation.error(
            f"{skill_path.relative_to(ROOT)}: unsupported frontmatter fields: {sorted(extra)}"
        )
    if missing:
        validation.error(
            f"{skill_path.relative_to(ROOT)}: missing frontmatter fields: {sorted(missing)}"
        )
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
        validation.error(f"{skill_path.relative_to(ROOT)}: invalid skill name")
    elif name != skill_directory.name:
        validation.error(
            f"{skill_path.relative_to(ROOT)}: name must match directory {skill_directory.name}"
        )
    if not isinstance(description, str) or not description.strip():
        validation.error(f"{skill_path.relative_to(ROOT)}: description must be non-empty")
    line_count = len(skill_path.read_text(encoding="utf-8").splitlines())
    if line_count > 500:
        validation.warning(
            f"{skill_path.relative_to(ROOT)}: {line_count} lines exceeds the 500-line guidance"
        )
    for markdown in skill_directory.rglob("*.md"):
        _validate_local_links(markdown, validation)
    validation.skill_count += 1


def _validate_plugin(plugin_path: Path, expected_name: str, validation: Validation) -> None:
    manifest_path = plugin_path / ".codex-plugin/plugin.json"
    manifest = _read_json(manifest_path, validation)
    if manifest is None:
        return
    if manifest.get("name") != expected_name or plugin_path.name != expected_name:
        validation.error(f"{manifest_path.relative_to(ROOT)}: plugin names do not match")
    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        validation.error(f"{manifest_path.relative_to(ROOT)}: version must be numeric semver")
    for field in ("description", "license", "homepage", "repository"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            validation.error(f"{manifest_path.relative_to(ROOT)}: missing {field}")
    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str):
        validation.error(f"{manifest_path.relative_to(ROOT)}: invalid author")
    skills_value = manifest.get("skills")
    if not isinstance(skills_value, str):
        validation.error(f"{manifest_path.relative_to(ROOT)}: missing skills directory")
    else:
        skills_path = (plugin_path / skills_value).resolve()
        if not skills_path.is_dir():
            validation.error(f"{manifest_path.relative_to(ROOT)}: skills directory is missing")
        else:
            skill_directories = sorted(path.parent for path in skills_path.glob("*/SKILL.md"))
            if not skill_directories:
                validation.error(f"{manifest_path.relative_to(ROOT)}: no skills found")
            for skill_directory in skill_directories:
                _validate_skill(skill_directory, validation)
    _validate_mcp_servers(plugin_path, manifest, validation)
    if plugin_path.name == "creative-model-bridge":
        _validate_creative_skill(plugin_path, manifest, validation)
    validation.plugin_count += 1


def _validate_creative_skill(
    plugin_path: Path, manifest: dict[str, Any], validation: Validation
) -> None:
    """Keep Creative Model Bridge instruction-only and security explicit."""
    allowed_files = {
        Path(".codex-plugin/plugin.json"),
        Path("README.md"),
        Path("skills/creative-model-bridge/SKILL.md"),
    }
    for path in plugin_path.rglob("*"):
        relative = path.relative_to(plugin_path)
        if path.is_symlink() or (path.is_file() and relative not in allowed_files):
            validation.error(
                f"{path.relative_to(ROOT)} is not allowed in the instruction-only plugin"
            )
    manifest_path = plugin_path / ".codex-plugin/plugin.json"
    if "mcpServers" in manifest or "mcp_servers" in manifest:
        validation.error(f"{manifest_path.relative_to(ROOT)}: MCP companions are not allowed")
    for relative in (
        ".codex-sync/provision.json",
        ".mcp.json",
        "mcp",
        "scripts",
        "tests",
        "docs",
        "pixi.toml",
        "pixi.lock",
    ):
        if (plugin_path / relative).exists():
            validation.error(f"{plugin_path.joinpath(relative).relative_to(ROOT)} must be removed")
    skill = plugin_path / "skills/creative-model-bridge/SKILL.md"
    try:
        skill_text = skill.read_text(encoding="utf-8")
    except OSError:
        validation.error(f"{skill.relative_to(ROOT)} must be readable")
        skill_text = ""
    for marker in CREATIVE_SKILL_REQUIRED_MARKERS:
        if marker not in skill_text:
            validation.error(
                f"{skill.relative_to(ROOT)} must contain the skill contract marker {marker!r}"
            )


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_mcp_servers(plugin_path: Path, manifest: dict[str, Any], validation: Validation) -> None:
    """Validate companion MCP launchers without assuming a particular server."""

    value = manifest.get("mcpServers")
    if value is None:
        return
    if isinstance(value, str):
        companion = (plugin_path / value).resolve()
        if not _inside(plugin_path.resolve(), companion):
            validation.error(f"{plugin_path.relative_to(ROOT)}: mcpServers path escapes plugin")
            return
        if not companion.is_file():
            validation.error(f"{companion.relative_to(ROOT)}: mcpServers companion is missing")
            return
        payload = _read_json(companion, validation)
        if payload is None:
            return
        if set(payload) == {"mcpServers"}:
            value = payload.get("mcpServers")
        else:
            value = payload
    if not isinstance(value, dict):
        validation.error(f"{plugin_path.relative_to(ROOT)}: mcpServers must be an object or companion path")
        return
    for name, server in value.items():
        context = f"{plugin_path.relative_to(ROOT)} mcp server {name!r}"
        if not isinstance(name, str) or not name.strip():
            validation.error(f"{context}: server name must be non-empty")
            continue
        if not isinstance(server, dict):
            validation.error(f"{context}: entry must be an object")
            continue
        _validate_mcp_server_entry(plugin_path, context, server, validation)


def _validate_pixi_launcher(
    plugin_path: Path,
    launcher: Path | None,
    context: str,
    validation: Validation,
    *,
    launcher_bytes: bytes | None = None,
) -> None:
    """Require and sanity-check a lockfile for launchers that invoke Pixi."""

    if launcher_bytes is None:
        assert launcher is not None
        try:
            launcher_bytes = launcher.read_bytes()
        except OSError as error:
            validation.error(f"{context}: Pixi launcher could not be read: {error}")
            return
    if b"pixi run" not in launcher_bytes or b"pixi.toml" not in launcher_bytes:
        return
    if b"--locked" not in launcher_bytes and b"--frozen" not in launcher_bytes:
        validation.error(f"{context}: Pixi launcher must use --locked or --frozen")
    try:
        launcher_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        validation.error(f"{context}: Pixi launcher is not valid UTF-8: {error}")
        return

    manifest_path = plugin_path / "pixi.toml"
    lock_path = plugin_path / "pixi.lock"
    if not manifest_path.is_file():
        validation.error(f"{context}: Pixi launcher requires pixi.toml")
        return
    if not lock_path.is_file():
        validation.error(f"{context}: Pixi launcher requires pixi.lock")
        return

    try:
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        validation.error(f"{context}: invalid pixi.toml: {error}")
        return
    try:
        lock = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        validation.error(f"{context}: invalid pixi.lock: {error}")
        return
    if not isinstance(lock, dict):
        validation.error(f"{context}: pixi.lock root must be an object")
        return
    if not isinstance(lock.get("version"), int) or lock["version"] < 6:
        validation.error(f"{context}: pixi.lock version must be at least 6")

    workspace = manifest.get("workspace")
    platforms = workspace.get("platforms") if isinstance(workspace, dict) else None
    if not isinstance(platforms, list) or any(not isinstance(item, str) or not item for item in platforms):
        validation.error(f"{context}: pixi.toml workspace.platforms must be a non-empty array")
        platforms = []
    environments = lock.get("environments")
    default_environment = environments.get("default") if isinstance(environments, dict) else None
    package_map = default_environment.get("packages") if isinstance(default_environment, dict) else None
    if not isinstance(package_map, dict):
        validation.error(f"{context}: pixi.lock default environment packages are missing")
    else:
        for platform in platforms:
            packages = package_map.get(platform)
            if not isinstance(packages, list) or not packages:
                validation.error(f"{context}: pixi.lock is missing packages for {platform}")

    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        validation.error(f"{context}: pixi.lock packages must be a non-empty array")
        return
    for index, package in enumerate(packages):
        package_context = f"{context}: pixi.lock packages[{index}]"
        if not isinstance(package, dict):
            validation.error(f"{package_context} must be an object")
            continue
        references = [package.get("conda"), package.get("pypi")]
        references = [item for item in references if item is not None]
        if len(references) != 1 or not isinstance(references[0], str) or not references[0].startswith("https://"):
            validation.error(f"{package_context} must contain one https package URL")
        digest = package.get("sha256")
        if not isinstance(digest, str) or not PIXI_SHA256_PATTERN.fullmatch(digest):
            validation.error(f"{package_context} has an invalid sha256 digest")


def _validate_mcp_server_entry(plugin_path: Path, context: str, server: dict[str, Any], validation: Validation) -> None:
    allowed_fields = {"command", "args", "cwd", "env_vars", "url", "startup_timeout_sec"}
    unknown = set(server) - allowed_fields
    if unknown:
        validation.error(f"{context}: unsupported fields {sorted(unknown)}")
    startup_timeout = server.get("startup_timeout_sec")
    if startup_timeout is not None and (
        isinstance(startup_timeout, bool)
        or not isinstance(startup_timeout, (int, float))
        or startup_timeout <= 0
    ):
        validation.error(f"{context}: startup_timeout_sec must be a positive number")
    env_vars = server.get("env_vars", [])
    if not isinstance(env_vars, list) or any(not isinstance(item, str) or not item for item in env_vars):
        validation.error(f"{context}: env_vars must be an array of non-empty strings")
    elif len(env_vars) != len(set(env_vars)):
        validation.error(f"{context}: env_vars must not contain duplicates")
    else:
        allowed_env = {"CODEX_HOME"}
        if any(item not in allowed_env for item in env_vars):
            validation.error(f"{context}: env_vars contains a non-allowlisted variable")

    cwd = server.get("cwd", ".")
    if not isinstance(cwd, str) or Path(cwd).is_absolute():
        validation.error(f"{context}: cwd must be a relative path")
        cwd_path = plugin_path.resolve()
    else:
        cwd_path = (plugin_path / cwd).resolve()
        if not _inside(plugin_path.resolve(), cwd_path) or not cwd_path.is_dir():
            validation.error(f"{context}: cwd must stay inside the plugin and exist")
    args = server.get("args", [])
    if not isinstance(args, list) or any(not isinstance(item, str) for item in args):
        validation.error(f"{context}: args must be an array of strings")

    command = server.get("command")
    url = server.get("url")
    if command is None and url is None:
        validation.error(f"{context}: command or url is required")
        return
    if command is not None:
        if not isinstance(command, str) or not command:
            validation.error(f"{context}: command must be a non-empty string")
            return
        if command == "pixi":
            validation.error(f"{context}: direct Pixi command is not permitted for target runtime")
        elif command == "git":
            if cwd != ".":
                validation.error(f"{context}: Git alias command must run from plugin root (cwd '.')")
            validation.error(f"{context}: Git alias command is not allowed for this plugin")
        elif "/" in command or "\\" in command or command.startswith("."):
            target = (cwd_path / command).resolve()
            if not _inside(plugin_path.resolve(), target):
                validation.error(f"{context}: command target escapes plugin")
            elif not target.is_file():
                validation.error(f"{context}: command target does not exist: {target.relative_to(ROOT)}")
            elif not os.access(target, os.X_OK):
                validation.error(f"{context}: command target is not executable: {target.relative_to(ROOT)}")
            else:
                _validate_pixi_launcher(plugin_path, target, context, validation)
    if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
        validation.error(f"{context}: url must be an https URL")


def _validate_marketplace(validation: Validation) -> None:
    marketplace = _read_json(MARKETPLACE, validation)
    if marketplace is None:
        return
    if not isinstance(marketplace.get("name"), str):
        validation.error("marketplace: missing name")
    interface = marketplace.get("interface")
    if not isinstance(interface, dict) or not isinstance(interface.get("displayName"), str):
        validation.error("marketplace: missing interface.displayName")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        validation.error("marketplace: plugins must be an array")
        return
    seen: set[str] = set()
    for index, entry in enumerate(plugins):
        context = f"marketplace plugins[{index}]"
        if not isinstance(entry, dict):
            validation.error(f"{context}: must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not SKILL_NAME_PATTERN.fullmatch(name):
            validation.error(f"{context}: invalid name")
            continue
        if name in seen:
            validation.error(f"{context}: duplicate name {name}")
        seen.add(name)
        source = entry.get("source")
        if not isinstance(source, dict) or source.get("source") != "local":
            validation.error(f"{context}: source must be local")
            continue
        source_path = source.get("path")
        if not isinstance(source_path, str) or not source_path.startswith("./plugins/"):
            validation.error(f"{context}: invalid source path")
            continue
        policy = entry.get("policy")
        if not isinstance(policy, dict):
            validation.error(f"{context}: policy is required")
        else:
            if policy.get("installation") not in {
                "NOT_AVAILABLE",
                "AVAILABLE",
                "INSTALLED_BY_DEFAULT",
            }:
                validation.error(f"{context}: invalid installation policy")
            if policy.get("authentication") not in {"ON_INSTALL", "ON_USE"}:
                validation.error(f"{context}: invalid authentication policy")
        if not isinstance(entry.get("category"), str):
            validation.error(f"{context}: category is required")
        _validate_plugin((ROOT / source_path).resolve(), name, validation)


def _validate_sync_metadata(validation: Validation) -> None:
    config_path = ROOT / "sync-sources.toml"
    lock_path = ROOT / "sync-lock.json"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        validation.error(f"sync-sources.toml: {error}")
        return
    lock = _read_json(lock_path, validation)
    if lock is None:
        return
    if lock.get("version") != 2:
        validation.error("sync-lock.json: version must be 2")
    configured = {
        item.get("id")
        for section in ("sources", "github_releases")
        for item in config.get(section, [])
        if isinstance(item, dict)
    }
    locked = set(lock.get("sources", {})) if isinstance(lock.get("sources"), dict) else set()
    if configured != locked:
        validation.error("sync source ids and sync-lock.json source ids do not match")
    for source in config.get("sources", []):
        if not isinstance(source, dict):
            continue
        for field in ("destination", "plugin_manifest", "license_destination"):
            value = source.get(field)
            if value is not None and not (ROOT / value).exists():
                validation.error(f"sync source {source.get('id')}: missing {field} {value}")
    for source in config.get("github_releases", []):
        if not isinstance(source, dict):
            continue
        for field in ("metadata_destination", "plugin_manifest"):
            value = source.get(field)
            if not isinstance(value, str) or not (ROOT / value).is_file():
                validation.error(
                    f"sync GitHub Release {source.get('id')}: missing {field} {value}"
                )
    lock_sources = lock.get("sources", {})
    if isinstance(lock_sources, dict):
        for source_id, entry in lock_sources.items():
            if not isinstance(entry, dict):
                validation.error(f"sync lock {source_id}: entry must be an object")
                continue
            kind = entry.get("kind", "git-tree")
            if kind == "git-tree":
                commit = entry.get("commit")
                if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                    validation.error(f"sync lock {source_id}: invalid commit")
            elif kind == "github-release":
                for field in ("tag_object_sha", "commit"):
                    value = entry.get(field)
                    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
                        validation.error(f"sync lock {source_id}: invalid {field}")
                assets = entry.get("assets")
                if not isinstance(assets, dict) or not assets:
                    validation.error(f"sync lock {source_id}: assets must be non-empty")
                else:
                    for name, asset in assets.items():
                        digest = asset.get("sha256") if isinstance(asset, dict) else None
                        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                            validation.error(
                                f"sync lock {source_id}: invalid asset digest for {name}"
                            )
                checksum_asset = entry.get("checksum_asset")
                checksum_digest = (
                    checksum_asset.get("sha256")
                    if isinstance(checksum_asset, dict)
                    else None
                )
                if not isinstance(checksum_digest, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", checksum_digest
                ):
                    validation.error(
                        f"sync lock {source_id}: invalid checksum asset digest"
                    )
            else:
                validation.error(f"sync lock {source_id}: unsupported kind {kind}")


def _validate_fastctx_windows_runtime(validation: Validation) -> None:
    path = ROOT / "plugins/fastctx/windows-bash-runtime.json"
    metadata = _read_json(path, validation)
    if metadata is None:
        return
    if metadata.get("schema_version") != 1:
        validation.error("FastCtx Windows Bash runtime: schema_version must be 1")
    if metadata.get("repository") != "git-for-windows/git":
        validation.error("FastCtx Windows Bash runtime: unexpected repository")
    for field in ("version", "tag", "published_at"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            validation.error(f"FastCtx Windows Bash runtime: invalid {field}")
    if not isinstance(metadata.get("release_id"), int):
        validation.error("FastCtx Windows Bash runtime: invalid release_id")
    asset = metadata.get("asset")
    if not isinstance(asset, dict):
        validation.error("FastCtx Windows Bash runtime: asset must be an object")
        return
    name = asset.get("name")
    url = asset.get("url")
    tag = metadata.get("tag")
    if not isinstance(name, str) or not re.fullmatch(r"Git-.+-64-bit\.tar\.bz2", name):
        validation.error("FastCtx Windows Bash runtime: invalid asset name")
    expected_prefix = f"https://github.com/git-for-windows/git/releases/download/{tag}/"
    if not isinstance(url, str) or not url.startswith(expected_prefix) or not url.endswith(f"/{name}"):
        validation.error("FastCtx Windows Bash runtime: invalid asset URL")
    if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
        validation.error("FastCtx Windows Bash runtime: invalid asset size")
    if asset.get("archive_format") != "tar.bz2":
        validation.error("FastCtx Windows Bash runtime: archive_format must be tar.bz2")
    digest = asset.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        validation.error("FastCtx Windows Bash runtime: invalid asset digest")


def _validate_fastctx_runtime_release(validation: Validation) -> None:
    path = ROOT / "plugins/fastctx/runtime-release.json"
    metadata = _read_json(path, validation)
    if metadata is None:
        return
    if metadata.get("schema_version") != 2:
        validation.error("FastCtx runtime release: schema_version must be 2")
        return
    assets = metadata.get("assets")
    expected = {
        "aarch64-apple-darwin": "fastctx-aarch64-apple-darwin.tar.gz",
        "x86_64-apple-darwin": "fastctx-x86_64-apple-darwin.tar.gz",
        "x86_64-pc-windows-msvc": "fastctx-x86_64-pc-windows-msvc.zip",
        "x86_64-unknown-linux-gnu": "fastctx-x86_64-unknown-linux-gnu.tar.gz",
    }
    if not isinstance(assets, dict) or set(assets) != set(expected):
        validation.error("FastCtx runtime release: exactly four platform assets are required")
        return
    final = metadata.get("distribution") == "codex-plugin" and metadata.get("transitional") is False
    transition = metadata.get("distribution") == "transitional-upstream" and metadata.get("transitional") is True
    if not (final or transition):
        validation.error("FastCtx runtime release: invalid distribution state")
    repository = metadata.get("repository")
    if repository != ("dale0525/codex-plugins" if final else "yc-duan/fastctx"):
        validation.error("FastCtx runtime release: repository does not match distribution state")
    version = metadata.get("version")
    tag = metadata.get("tag")
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        validation.error("FastCtx runtime release: invalid version")
    if not isinstance(tag, str) or not tag:
        validation.error("FastCtx runtime release: invalid tag")
    if final and tag != f"fastctx-v{version}":
        validation.error("FastCtx runtime release: final tag must match version")
    if transition and not isinstance(metadata.get("transition_note"), str):
        validation.error("FastCtx runtime release: transition note is required")
    for target, name in expected.items():
        asset = assets[target]
        if not isinstance(asset, dict) or asset.get("name") != name:
            validation.error(f"FastCtx runtime release: invalid asset {target}")
            continue
        if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
            validation.error(f"FastCtx runtime release: invalid asset size for {target}")
        digest = asset.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            validation.error(f"FastCtx runtime release: invalid asset digest for {target}")
        if not isinstance(asset.get("url"), str) or not asset["url"].endswith(f"/{name}"):
            validation.error(f"FastCtx runtime release: invalid asset URL for {target}")


def _validate_workflows(validation: Validation) -> None:
    workflow_directory = ROOT / ".github/workflows"
    workflows = sorted((*workflow_directory.glob("*.yml"), *workflow_directory.glob("*.yaml")))
    for workflow in workflows:
        try:
            payload = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        except yaml.YAMLError as error:
            validation.error(f"{workflow.relative_to(ROOT)}: invalid YAML: {error}")
            continue

        def visit(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    if key == "uses":
                        context = f"{workflow.relative_to(ROOT)} ({child_path})"
                        if not isinstance(child, str):
                            validation.error(f"{context}: uses must be a string")
                        elif not child.startswith("./") and not WORKFLOW_ACTION_REF_PATTERN.fullmatch(child):
                            validation.error(
                                f"{context}: external action uses must pin a full lowercase commit SHA"
                            )
                    visit(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")

        visit(payload)


def main() -> int:
    validation = Validation()
    _validate_marketplace(validation)
    _validate_sync_metadata(validation)
    _validate_fastctx_windows_runtime(validation)
    _validate_fastctx_runtime_release(validation)
    _validate_workflows(validation)
    for warning in validation.warnings:
        print(f"warning: {warning}")
    for error in validation.errors:
        print(f"error: {error}", file=sys.stderr)
    if validation.errors:
        print(f"validation failed with {len(validation.errors)} error(s)", file=sys.stderr)
        return 1
    print(
        f"validated {validation.plugin_count} plugin(s) and "
        f"{validation.skill_count} skill(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
