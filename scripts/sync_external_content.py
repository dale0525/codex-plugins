#!/usr/bin/env python3
"""Synchronize configured external skill or plugin directories into this repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPOSITORY_ROOT / "sync-sources.toml"
DEFAULT_LOCK = REPOSITORY_ROOT / "sync-lock.json"
SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SyncError(RuntimeError):
    """Raised for invalid configuration or unsafe upstream content."""


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    repository: str
    ref: str
    source: str
    destination: str
    plugin_manifest: str
    license_source: str | None
    license_destination: str | None
    remove_frontmatter_fields: tuple[str, ...]
    skill_description_suffixes: tuple[tuple[str, str], ...]
    skill_implicit_invocation: tuple[tuple[str, bool], ...]
    skill_text_replacements: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class StagedSource:
    spec: SourceSpec
    commit: str
    content: Path
    license_content: bytes | None


@dataclass(frozen=True)
class GithubReleaseSpec:
    source_id: str
    repository: str
    metadata_destination: str
    plugin_manifest: str
    required_assets: tuple[str, ...]
    checksum_asset: str


@dataclass(frozen=True)
class StagedGithubRelease:
    spec: GithubReleaseSpec
    metadata: bytes
    lock_entry: dict[str, Any]


def _required_string(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f"{context}.{key} must be a non-empty string")
    return value


def load_config(config_path: Path, repository_root: Path = REPOSITORY_ROOT) -> list[SourceSpec]:
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    if config.get("version") != 1:
        raise SyncError("sync-sources.toml must declare version = 1")
    raw_sources = config.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SyncError("sync-sources.toml must contain at least one [[sources]] entry")

    specs: list[SourceSpec] = []
    seen_ids: set[str] = set()
    destinations: list[Path] = []
    for index, raw in enumerate(raw_sources):
        context = f"sources[{index}]"
        if not isinstance(raw, dict):
            raise SyncError(f"{context} must be a table")
        source_id = _required_string(raw, "id", context)
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise SyncError(f"{context}.id must use lower-case letters, digits, and hyphens")
        if source_id in seen_ids:
            raise SyncError(f"duplicate source id: {source_id}")
        seen_ids.add(source_id)

        license_source = raw.get("license_source")
        license_destination = raw.get("license_destination")
        if (license_source is None) != (license_destination is None):
            raise SyncError(
                f"{context} must set license_source and license_destination together"
            )
        if license_source is not None and not isinstance(license_source, str):
            raise SyncError(f"{context}.license_source must be a string")
        if license_destination is not None and not isinstance(license_destination, str):
            raise SyncError(f"{context}.license_destination must be a string")

        fields = raw.get("remove_skill_frontmatter_fields", [])
        if not isinstance(fields, list) or not all(isinstance(item, str) for item in fields):
            raise SyncError(f"{context}.remove_skill_frontmatter_fields must be strings")
        if {"name", "description"}.intersection(fields):
            raise SyncError(f"{context} cannot remove required skill frontmatter fields")
        suffixes = raw.get("skill_description_suffixes", {})
        if not isinstance(suffixes, dict) or not all(
            isinstance(name, str) and isinstance(suffix, str) and suffix.strip()
            for name, suffix in suffixes.items()
        ):
            raise SyncError(f"{context}.skill_description_suffixes must map names to text")
        implicit_invocation = raw.get("skill_implicit_invocation", {})
        if not isinstance(implicit_invocation, dict) or not all(
            isinstance(name, str) and isinstance(value, bool)
            for name, value in implicit_invocation.items()
        ):
            raise SyncError(
                f"{context}.skill_implicit_invocation must map names to booleans"
            )
        text_replacements = raw.get("skill_text_replacements", [])
        if not isinstance(text_replacements, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("skill"), str)
            and item.get("skill", "").strip()
            and isinstance(item.get("find"), str)
            and item.get("find", "")
            and isinstance(item.get("replace"), str)
            for item in text_replacements
        ):
            raise SyncError(
                f"{context}.skill_text_replacements must contain skill/find/replace strings"
            )

        repository = _required_string(raw, "repository", context)
        parsed_repository = urlsplit(repository)
        if parsed_repository.scheme not in {"https", "file"}:
            raise SyncError(f"{context}.repository must use https:// or file://")
        if parsed_repository.username or parsed_repository.password:
            raise SyncError(f"{context}.repository must not contain credentials")

        destination_value = _required_string(raw, "destination", context)
        manifest_value = _required_string(raw, "plugin_manifest", context)
        for field_name, value in (
            ("destination", destination_value),
            ("plugin_manifest", manifest_value),
        ):
            if not Path(value).parts or Path(value).parts[0] != "plugins":
                raise SyncError(f"{context}.{field_name} must stay under plugins/")
        if license_destination is not None:
            if not Path(license_destination).parts or Path(license_destination).parts[0] != "plugins":
                raise SyncError(f"{context}.license_destination must stay under plugins/")

        spec = SourceSpec(
            source_id=source_id,
            repository=repository,
            ref=_required_string(raw, "ref", context),
            source=_required_string(raw, "source", context),
            destination=destination_value,
            plugin_manifest=manifest_value,
            license_source=license_source,
            license_destination=license_destination,
            remove_frontmatter_fields=tuple(fields),
            skill_description_suffixes=tuple(sorted(suffixes.items())),
            skill_implicit_invocation=tuple(sorted(implicit_invocation.items())),
            skill_text_replacements=tuple(
                (
                    item["skill"],
                    item["find"],
                    item["replace"],
                )
                for item in text_replacements
            ),
        )
        destination = _safe_repository_path(repository_root, spec.destination)
        for existing in destinations:
            if destination == existing or destination in existing.parents or existing in destination.parents:
                raise SyncError(f"overlapping destinations: {existing} and {destination}")
        destinations.append(destination)
        specs.append(spec)
    return specs


def load_github_release_config(
    config_path: Path,
    repository_root: Path = REPOSITORY_ROOT,
) -> list[GithubReleaseSpec]:
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    specs: list[GithubReleaseSpec] = []
    seen_ids = {
        item.get("id")
        for item in config.get("sources", [])
        if isinstance(item, dict)
    }
    for index, raw in enumerate(config.get("github_releases", [])):
        context = f"github_releases[{index}]"
        if not isinstance(raw, dict):
            raise SyncError(f"{context} must be a table")
        source_id = _required_string(raw, "id", context)
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise SyncError(f"{context}.id must use lower-case letters, digits, and hyphens")
        if source_id in seen_ids:
            raise SyncError(f"duplicate source id: {source_id}")
        seen_ids.add(source_id)
        repository = _required_string(raw, "repository", context)
        if not REPOSITORY_SLUG_PATTERN.fullmatch(repository):
            raise SyncError(f"{context}.repository must use owner/name syntax")
        metadata_destination = _required_string(raw, "metadata_destination", context)
        plugin_manifest = _required_string(raw, "plugin_manifest", context)
        for field_name, value in (
            ("metadata_destination", metadata_destination),
            ("plugin_manifest", plugin_manifest),
        ):
            path = _safe_repository_path(repository_root, value)
            if not Path(value).parts or Path(value).parts[0] != "plugins":
                raise SyncError(f"{context}.{field_name} must stay under plugins/")
            if field_name == "plugin_manifest" and not path.is_file():
                raise SyncError(f"{context}.{field_name} does not exist: {value}")
        required_assets = raw.get("required_assets")
        if (
            not isinstance(required_assets, list)
            or not required_assets
            or not all(isinstance(item, str) and item for item in required_assets)
            or len(set(required_assets)) != len(required_assets)
        ):
            raise SyncError(f"{context}.required_assets must be unique non-empty strings")
        checksum_asset = _required_string(raw, "checksum_asset", context)
        if checksum_asset in required_assets:
            raise SyncError(
                f"{context}.checksum_asset must not also appear in required_assets"
            )
        specs.append(
            GithubReleaseSpec(
                source_id=source_id,
                repository=repository,
                metadata_destination=metadata_destination,
                plugin_manifest=plugin_manifest,
                required_assets=tuple(required_assets),
                checksum_asset=checksum_asset,
            )
        )
    return specs


def _safe_repository_path(root: Path, relative: str, *, allow_root: bool = False) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise SyncError(f"path must be relative: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise SyncError(f"path escapes repository root: {relative}")
    if resolved == resolved_root and not allow_root:
        raise SyncError("repository root cannot be a synchronization target")
    if ".git" in path.parts:
        raise SyncError(f".git paths cannot be synchronized: {relative}")
    return resolved


def _run(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise SyncError(f"command failed ({command[0]}): {detail}")
    return result.stdout.strip()


def _reject_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SyncError(f"upstream content contains a symlink: {path.relative_to(root)}")


def _normalize_skill_frontmatter(
    skill_path: Path,
    fields: tuple[str, ...],
    description_suffixes: tuple[tuple[str, str], ...],
    skill_name: str | None = None,
) -> None:
    skill_name = skill_name or skill_path.parent.name
    suffix = dict(description_suffixes).get(skill_name)
    if not fields and suffix is None:
        return
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SyncError(f"SKILL.md has no YAML frontmatter: {skill_path}")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as error:
        raise SyncError(f"SKILL.md has unterminated YAML frontmatter: {skill_path}") from error

    field_set = set(fields)
    normalized = [lines[0]]
    skip_field = False
    for line in lines[1:end]:
        # A frontmatter field can contain an indented block scalar, sequence,
        # or mapping. Skip that complete block until the next top-level key;
        # removing only the key line would leave invalid YAML behind.
        match = re.match(r"^([A-Za-z0-9_-]+):", line)
        if match:
            skip_field = match.group(1) in field_set
            if not skip_field:
                normalized.append(line)
            continue
        if not skip_field:
            normalized.append(line)
    if suffix is not None:
        description_indexes = [
            index for index, line in enumerate(normalized) if line.startswith("description:")
        ]
        if len(description_indexes) != 1:
            raise SyncError(f"cannot adapt skill description: {skill_path}")
        index = description_indexes[0]
        line = normalized[index]
        value = line.split(":", 1)[1].strip()
        if value.startswith(("|", ">")):
            block_end = next(
                (
                    position
                    for position in range(index + 1, len(normalized))
                    if re.match(r"^[A-Za-z0-9_-]+:", normalized[position])
                ),
                len(normalized),
            )
            if not any(suffix in item for item in normalized[index + 1 : block_end]):
                normalized.insert(block_end, f"  {suffix.strip()}\n")
        else:
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            if suffix not in body:
                normalized[index] = f"{body} {suffix.strip()}{newline}"
    normalized.extend(lines[end:])
    skill_path.write_text("".join(normalized), encoding="utf-8")


def _normalize_skill_invocation_policy(
    skill_path: Path,
    policies: tuple[tuple[str, bool], ...],
    skill_name: str | None = None,
) -> None:
    """Apply repository-owned invocation policy to synchronized skills."""

    skill_name = skill_name or skill_path.parent.name
    allow_implicit = dict(policies).get(skill_name)
    if allow_implicit is None:
        return
    policy_path = skill_path.parent / "agents/openai.yaml"
    value = "true" if allow_implicit else "false"
    if policy_path.is_file():
        text = policy_path.read_text(encoding="utf-8")
        updated, replacements = re.subn(
            r"(?m)^(\s*allow_implicit_invocation\s*:\s*).*$",
            rf"\g<1>{value}",
            text,
            count=1,
        )
        if replacements == 0:
            if re.search(r"(?m)^policy:\s*$", updated):
                updated = re.sub(
                    r"(?m)^policy:\s*$",
                    f"policy:\n  allow_implicit_invocation: {value}",
                    updated,
                    count=1,
                )
            else:
                updated = updated.rstrip("\n") + (
                    f"\npolicy:\n  allow_implicit_invocation: {value}\n"
                )
        elif not updated.endswith("\n"):
            updated += "\n"
        policy_path.write_text(updated, encoding="utf-8")
        return

    display_name = skill_name.replace("-", " ").title()
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "interface:\n"
        f'  display_name: "{display_name}"\n'
        f'  short_description: "Use ${skill_name} according to its declared invocation policy."\n'
        f'  default_prompt: "Use ${skill_name} for this task."\n'
        "policy:\n"
        f"  allow_implicit_invocation: {value}\n",
        encoding="utf-8",
    )


def _normalize_skill_text(
    skill_path: Path,
    replacements: tuple[tuple[str, str, str], ...],
    skill_name: str | None = None,
) -> None:
    skill_name = skill_name or skill_path.parent.name
    applicable = [
        (find, replace)
        for target_skill, find, replace in replacements
        if target_skill == skill_name
    ]
    if not applicable:
        return
    text = skill_path.read_text(encoding="utf-8")
    for find, replace in applicable:
        if find in text:
            text = text.replace(find, replace, 1)
        elif replace not in text:
            raise SyncError(
                f"skill text replacement did not match upstream content: {skill_path}"
            )
    skill_path.write_text(text, encoding="utf-8")


def _stage_source(
    spec: SourceSpec,
    temporary_root: Path,
    repository_root: Path,
) -> StagedSource:
    clone_path = temporary_root / f"clone-{spec.source_id}"
    _run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            spec.ref,
            "--no-tags",
            "--",
            spec.repository,
            str(clone_path),
        ]
    )
    commit = _run(["git", "-C", str(clone_path), "rev-parse", "HEAD"])
    source_path = _safe_repository_path(clone_path, spec.source, allow_root=True)
    if not source_path.is_dir():
        raise SyncError(f"upstream directory does not exist: {spec.source_id}/{spec.source}")
    _reject_symlinks(source_path)

    staged_content = temporary_root / f"content-{spec.source_id}"
    shutil.copytree(source_path, staged_content, ignore=shutil.ignore_patterns(".git"))
    destination = _safe_repository_path(repository_root, spec.destination)
    for skill_path in staged_content.rglob("SKILL.md"):
        relative_skill_dir = skill_path.parent.relative_to(staged_content)
        skill_name = (
            destination.name
            if relative_skill_dir == Path(".")
            else skill_path.parent.name
        )
        # Invocation policy files are repository-owned compatibility metadata.
        # Preserve a tracked local interface when upstream does not provide one;
        # otherwise a full-tree replacement would reset it on every sync.
        staged_policy = skill_path.parent / "agents/openai.yaml"
        if not staged_policy.exists():
            local_policy = destination / relative_skill_dir / "agents/openai.yaml"
            if local_policy.is_symlink():
                raise SyncError(f"destination contains a symlink: {local_policy}")
            if local_policy.is_file():
                staged_policy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_policy, staged_policy)
        _normalize_skill_frontmatter(
            skill_path,
            spec.remove_frontmatter_fields,
            spec.skill_description_suffixes,
            skill_name,
        )
        _normalize_skill_invocation_policy(
            skill_path,
            spec.skill_implicit_invocation,
            skill_name,
        )
        _normalize_skill_text(
            skill_path,
            spec.skill_text_replacements,
            skill_name,
        )

    license_content = None
    if spec.license_source is not None:
        license_path = _safe_repository_path(clone_path, spec.license_source, allow_root=True)
        if not license_path.is_file():
            raise SyncError(f"upstream license does not exist: {spec.license_source}")
        if license_path.is_symlink():
            raise SyncError("upstream license cannot be a symlink")
        license_content = license_path.read_bytes()

    return StagedSource(spec, commit, staged_content, license_content)


def _github_json(url: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dale0525-codex-plugins-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SyncError(f"GitHub request failed for {url}: {error}") from error


def _download(url: str, destination: Path) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "dale0525-codex-plugins-sync"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content = response.read()
    except urllib.error.URLError as error:
        raise SyncError(f"download failed for {url}: {error}") from error
    destination.write_bytes(content)
    return content


def _release_version(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def _peel_tag(api_base: str, repository: str, tag: str) -> tuple[str, str]:
    reference = _github_json(f"{api_base}/repos/{repository}/git/ref/tags/{tag}")
    if not isinstance(reference, dict) or not isinstance(reference.get("object"), dict):
        raise SyncError(f"GitHub tag reference is malformed: {repository}@{tag}")
    tag_object = reference["object"]
    object_type = tag_object.get("type")
    object_sha = tag_object.get("sha")
    if not isinstance(object_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", object_sha):
        raise SyncError(f"GitHub tag reference has an invalid SHA: {repository}@{tag}")
    if object_type == "commit":
        return object_sha, object_sha
    if object_type != "tag":
        raise SyncError(f"GitHub tag points to unsupported object type {object_type!r}")
    tag_data = _github_json(f"{api_base}/repos/{repository}/git/tags/{object_sha}")
    peeled = tag_data.get("object") if isinstance(tag_data, dict) else None
    commit_sha = peeled.get("sha") if isinstance(peeled, dict) else None
    if (
        not isinstance(peeled, dict)
        or peeled.get("type") != "commit"
        or not isinstance(commit_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit_sha)
    ):
        raise SyncError(f"annotated GitHub tag does not peel to a commit: {repository}@{tag}")
    return object_sha, commit_sha


def _parse_checksums(content: bytes) -> dict[str, str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SyncError("SHA256SUMS is not UTF-8") from error
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^/\\\s]+)", line.strip())
        if match is None:
            raise SyncError(f"SHA256SUMS line {line_number} is malformed")
        digest, name = match.groups()
        if name in checksums:
            raise SyncError(f"SHA256SUMS contains duplicate asset {name}")
        checksums[name] = digest.lower()
    return checksums


def _stage_github_release(
    spec: GithubReleaseSpec,
    temporary_root: Path,
    previous_lock: dict[str, Any] | None,
) -> StagedGithubRelease:
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    releases = _github_json(f"{api_base}/repos/{spec.repository}/releases?per_page=100")
    if not isinstance(releases, list):
        raise SyncError(f"GitHub releases response is malformed for {spec.repository}")
    candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        version = _release_version(tag) if isinstance(tag, str) else None
        if version is not None:
            candidates.append((version, release))
    if not candidates:
        raise SyncError(f"no stable semantic GitHub Release found for {spec.repository}")
    version_tuple, release = max(candidates, key=lambda item: item[0])
    tag = release["tag_name"]
    version = ".".join(str(value) for value in version_tuple)
    release_id = release.get("id")
    published_at = release.get("published_at")
    if not isinstance(release_id, int) or not isinstance(published_at, str):
        raise SyncError(f"GitHub Release metadata is incomplete for {spec.repository}@{tag}")
    tag_object_sha, commit_sha = _peel_tag(api_base, spec.repository, tag)

    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise SyncError(f"GitHub Release assets are malformed for {spec.repository}@{tag}")
    assets_by_name: dict[str, dict[str, Any]] = {}
    for asset in raw_assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise SyncError(f"GitHub Release contains a malformed asset for {spec.repository}@{tag}")
        if asset["name"] in assets_by_name:
            raise SyncError(f"GitHub Release contains duplicate asset {asset['name']}")
        assets_by_name[asset["name"]] = asset
    required_names = [*spec.required_assets, spec.checksum_asset]
    missing = [name for name in required_names if name not in assets_by_name]
    if missing:
        raise SyncError(f"GitHub Release is missing required assets: {', '.join(missing)}")

    checksum_asset = assets_by_name[spec.checksum_asset]
    checksum_url = checksum_asset.get("browser_download_url")
    if not isinstance(checksum_url, str):
        raise SyncError("checksum asset has no download URL")
    checksum_content = _download(
        checksum_url,
        temporary_root / f"{spec.source_id}-{spec.checksum_asset}",
    )
    checksum_digest = hashlib.sha256(checksum_content).hexdigest()
    api_checksum_digest = checksum_asset.get("digest")
    if api_checksum_digest != f"sha256:{checksum_digest}":
        raise SyncError("checksum asset digest does not match the GitHub API digest")
    checksums = _parse_checksums(checksum_content)

    staged_assets: dict[str, dict[str, Any]] = {}
    for name in spec.required_assets:
        asset = assets_by_name[name]
        url = asset.get("browser_download_url")
        size = asset.get("size")
        api_digest = asset.get("digest")
        if not isinstance(url, str) or not isinstance(size, int):
            raise SyncError(f"GitHub Release asset metadata is incomplete: {name}")
        content = _download(url, temporary_root / f"{spec.source_id}-{name}")
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != size:
            raise SyncError(f"GitHub Release asset size does not match: {name}")
        if checksums.get(name) != digest or api_digest != f"sha256:{digest}":
            raise SyncError(f"GitHub Release asset digest verification failed: {name}")
        target = name.removeprefix("fastctx-").removesuffix(".tar.gz").removesuffix(".zip")
        staged_assets[target] = {
            "name": name,
            "url": url,
            "size": size,
            "sha256": digest,
        }

    checksum_record = {
        "name": spec.checksum_asset,
        "url": checksum_url,
        "size": len(checksum_content),
        "sha256": checksum_digest,
    }
    ignored_assets = sorted(set(assets_by_name) - set(required_names))
    lock_entry: dict[str, Any] = {
        "kind": "github-release",
        "repository": spec.repository,
        "version": version,
        "tag": tag,
        "release_id": release_id,
        "published_at": published_at,
        "tag_object_sha": tag_object_sha,
        "commit": commit_sha,
        "metadata_destination": spec.metadata_destination,
        "assets": staged_assets,
        "checksum_asset": checksum_record,
        "ignored_assets": ignored_assets,
    }
    if previous_lock and previous_lock.get("tag") == tag and previous_lock != lock_entry:
        raise SyncError(
            f"GitHub Release {spec.repository}@{tag} changed after it was locked; review the upstream release manually"
        )
    metadata = {
        "schema_version": 1,
        "repository": spec.repository,
        "version": version,
        "tag": tag,
        "release_id": release_id,
        "published_at": published_at,
        "tag_object_sha": tag_object_sha,
        "commit_sha": commit_sha,
        "assets": staged_assets,
        "checksum_asset": checksum_record,
    }
    return StagedGithubRelease(
        spec=spec,
        metadata=(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        lock_entry=lock_entry,
    )


def _directory_digest(path: Path) -> str | None:
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        if item.is_symlink():
            raise SyncError(f"destination contains a symlink: {item}")
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = item.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _replace_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    incoming = destination.parent / f".{destination.name}.sync-new-{token}"
    backup = destination.parent / f".{destination.name}.sync-old-{token}"
    shutil.copytree(source, incoming)
    moved_old = False
    try:
        if destination.exists():
            destination.rename(backup)
            moved_old = True
        incoming.rename(destination)
        if moved_old:
            shutil.rmtree(backup)
    except Exception:
        if incoming.exists():
            shutil.rmtree(incoming)
        if moved_old and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise


def _write_bytes_atomic(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")
    temporary.write_bytes(content)
    temporary.replace(destination)


def _write_json_atomic(destination: Path, data: dict[str, Any]) -> None:
    content = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _write_bytes_atomic(destination, content)


def _bumped_manifest(manifest_path: Path) -> tuple[bytes, str, str]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SyncError(f"cannot read plugin manifest: {manifest_path}") from error
    version = manifest.get("version")
    if not isinstance(version, str) or not (match := SEMVER_PATTERN.fullmatch(version)):
        raise SyncError(f"plugin version must be numeric semver: {manifest_path}")
    old_version = version
    manifest["version"] = f"{match.group(1)}.{match.group(2)}.{int(match.group(3)) + 1}"
    content = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return content, old_version, manifest["version"]


def _snapshot_targets(targets: set[Path], backup_root: Path) -> dict[Path, tuple[str, Path | None]]:
    snapshots: dict[Path, tuple[str, Path | None]] = {}
    for index, target in enumerate(sorted(targets)):
        if target.is_dir():
            backup = backup_root / f"target-{index}"
            shutil.copytree(target, backup)
            snapshots[target] = ("directory", backup)
        elif target.is_file():
            backup = backup_root / f"target-{index}"
            backup.write_bytes(target.read_bytes())
            snapshots[target] = ("file", backup)
        else:
            snapshots[target] = ("absent", None)
    return snapshots


def _restore_targets(snapshots: dict[Path, tuple[str, Path | None]]) -> None:
    for target, (kind, backup) in reversed(list(snapshots.items())):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if kind == "directory" and backup is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backup, target)
        elif kind == "file" and backup is not None:
            _write_bytes_atomic(target, backup.read_bytes())


def synchronize(config_path: Path = DEFAULT_CONFIG, lock_path: Path = DEFAULT_LOCK) -> bool:
    config_path = config_path.resolve()
    lock_path = lock_path.resolve()
    repository_root = config_path.parent
    if lock_path != repository_root and repository_root not in lock_path.parents:
        raise SyncError("lock path must stay inside the repository")
    specs = load_config(config_path, repository_root)
    release_specs = load_github_release_config(config_path, repository_root)
    changed_manifests: set[Path] = set()
    lock_sources: dict[str, dict[str, Any]] = {}
    previous_lock: dict[str, Any] = {}
    if lock_path.is_file():
        try:
            parsed_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SyncError(f"cannot read synchronization lock: {error}") from error
        if isinstance(parsed_lock, dict) and isinstance(parsed_lock.get("sources"), dict):
            previous_lock = parsed_lock["sources"]

    with tempfile.TemporaryDirectory(prefix="codex-plugin-sync-") as temporary:
        temporary_root = Path(temporary)
        staged_sources = [
            _stage_source(spec, temporary_root, repository_root) for spec in specs
        ]
        staged_releases = [
            _stage_github_release(
                spec,
                temporary_root,
                previous_lock.get(spec.source_id)
                if isinstance(previous_lock.get(spec.source_id), dict)
                else None,
            )
            for spec in release_specs
        ]
        directory_writes: list[tuple[Path, Path]] = []
        file_writes: dict[Path, bytes] = {}

        for staged in staged_sources:
            spec = staged.spec
            destination = _safe_repository_path(repository_root, spec.destination)
            content_changed = _directory_digest(staged.content) != _directory_digest(destination)
            license_changed = False
            if spec.license_destination is not None and staged.license_content is not None:
                license_destination = _safe_repository_path(
                    repository_root, spec.license_destination
                )
                current_license = (
                    license_destination.read_bytes() if license_destination.is_file() else None
                )
                license_changed = current_license != staged.license_content
            if content_changed:
                directory_writes.append((staged.content, destination))
            if license_changed:
                file_writes[license_destination] = staged.license_content
            if content_changed or license_changed:
                changed_manifests.add(
                    _safe_repository_path(repository_root, spec.plugin_manifest)
                )
            lock_sources[spec.source_id] = {
                "kind": "git-tree",
                "repository": spec.repository,
                "ref": spec.ref,
                "commit": staged.commit,
                "destination": spec.destination,
            }
            state = "updated" if content_changed or license_changed else "unchanged"
            print(f"{spec.source_id}: {state} at {staged.commit[:12]}")

        for staged in staged_releases:
            destination = _safe_repository_path(
                repository_root, staged.spec.metadata_destination
            )
            current = destination.read_bytes() if destination.is_file() else None
            changed = current != staged.metadata
            if changed:
                file_writes[destination] = staged.metadata
                changed_manifests.add(
                    _safe_repository_path(repository_root, staged.spec.plugin_manifest)
                )
            lock_sources[staged.spec.source_id] = staged.lock_entry
            state = "updated" if changed else "unchanged"
            print(
                f"{staged.spec.source_id}: {state} at "
                f"{staged.lock_entry['tag']} ({staged.lock_entry['commit'][:12]})"
            )

        version_changes: list[tuple[Path, str, str]] = []
        for manifest_path in sorted(changed_manifests):
            content, old_version, new_version = _bumped_manifest(manifest_path)
            file_writes[manifest_path] = content
            version_changes.append((manifest_path, old_version, new_version))

        lock_data: dict[str, Any] = {"version": 2, "sources": lock_sources}
        serialized_lock = (json.dumps(lock_data, indent=2, ensure_ascii=False) + "\n").encode()
        current_lock = lock_path.read_bytes() if lock_path.is_file() else None
        if current_lock != serialized_lock:
            file_writes[lock_path] = serialized_lock

        if not directory_writes and not file_writes:
            return False
        targets = {destination for _, destination in directory_writes} | set(file_writes)
        backup_root = temporary_root / "rollback"
        backup_root.mkdir()
        snapshots = _snapshot_targets(targets, backup_root)
        try:
            for source, destination in directory_writes:
                _replace_directory(source, destination)
            for destination, content in file_writes.items():
                _write_bytes_atomic(destination, content)
        except Exception:
            _restore_targets(snapshots)
            raise
        for manifest_path, old_version, new_version in version_changes:
            print(f"{manifest_path.relative_to(repository_root)}: {old_version} -> {new_version}")
        if lock_path in file_writes:
            print(f"updated {lock_path.relative_to(repository_root)}")
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()
    try:
        synchronize(args.config.resolve(), args.lock.resolve())
    except SyncError as error:
        print(f"sync error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
