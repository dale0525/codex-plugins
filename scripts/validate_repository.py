#!/usr/bin/env python3
"""Validate marketplace, plugin, skill, sync, and workflow structure."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE = ROOT / ".agents/plugins/marketplace.json"
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


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
    validation.plugin_count += 1


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


def _validate_workflows(validation: Validation) -> None:
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        try:
            yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        except yaml.YAMLError as error:
            validation.error(f"{workflow.relative_to(ROOT)}: invalid YAML: {error}")


def main() -> int:
    validation = Validation()
    _validate_marketplace(validation)
    _validate_sync_metadata(validation)
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
