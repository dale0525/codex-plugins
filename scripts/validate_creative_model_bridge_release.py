#!/usr/bin/env python3
"""Validate the Creative Model Bridge release contract without network access."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


ASSETS = {
    "creative-model-bridge-aarch64-apple-darwin",
    "creative-model-bridge-x86_64-apple-darwin",
    "creative-model-bridge-x86_64-unknown-linux-gnu",
    "creative-model-bridge-aarch64-unknown-linux-gnu",
    "creative-model-bridge-x86_64-pc-windows-msvc.exe",
}
PLATFORMS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64")
RELEASE_ASSETS = ASSETS | {"checksums.txt"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TAG_RE = re.compile(r"^creative-model-bridge-v(\d+\.\d+\.\d+)$")
PUBLISH_STEP_NAME = "Publish verified draft, or confirm published exact no-op"
BUILD_PIXI_MANIFEST = "plugins/creative-model-bridge/pixi.toml"
BOOTSTRAP_RELATIVE_PATH = "plugins/creative-model-bridge/scripts/bootstrap.sh"
BUNDLED_MCP_PATH = ".mcp.json"
BUNDLED_MCP_SERVER = "creative-model-bridge-bundled"
LEGACY_GLOBAL_MCP_SERVER = "creative-model-bridge"
BUNDLED_MCP_REQUIRED_ENV_VARS = ("CODEX_HOME", "CREATIVE_MODEL_API_KEY")
BUNDLED_MCP_ALLOWED_ENV_VARS = frozenset(
    {
        *BUNDLED_MCP_REQUIRED_ENV_VARS,
        "SSL_CERT_FILE",
        "CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE",
        "CREATIVE_MODEL_BRIDGE_BIN",
        "CREATIVE_MODEL_BRIDGE_VERSION",
        "CREATIVE_MODEL_BRIDGE_OFFLINE",
    }
)


def validate_publish_step_structure(workflow_text: str) -> list[str]:
    """Check that the publish step re-verifies the remote release immediately before editing it."""

    errors: list[str] = []
    step_match = re.search(
        rf"(?ms)^\s*- name: {re.escape(PUBLISH_STEP_NAME)}\s*\n(?P<body>.*?)(?=^\s*- name:|\Z)",
        workflow_text,
    )
    if not step_match:
        return [f"release workflow must contain the {PUBLISH_STEP_NAME!r} step"]
    body = step_match.group("body")
    edit_match = re.search(r"(?m)^\s*gh release edit\s+[^\n]*--draft=false\s*$", body)
    if not edit_match:
        errors.append("publish step must edit the release with --draft=false")
        return errors

    verify_definition = re.search(r"(?ms)^\s*verify_remote\(\) \{\n(?P<body>.*?)(?=^\s*\}\s*$)", body)
    if not verify_definition:
        errors.append("publish step must define a strict verify_remote function")
    else:
        verify_body = verify_definition.group("body")
        required_markers = (
            "gh release download",
            "checksums.txt",
            "cmp -s dist/checksums.txt remote-release/checksums.txt",
            "sha256sum -c checksums.txt",
        )
        for marker in required_markers:
            if marker not in verify_body:
                errors.append(f"final remote verification is missing {marker}")
        if not all(asset in verify_body for asset in ASSETS):
            errors.append("final remote verification must enumerate all five binary assets")

    calls = list(re.finditer(r"(?m)^[ \t]*verify_remote[ \t]*$", body))
    calls_before_edit = [match for match in calls if match.start() < edit_match.start()]
    if not calls_before_edit:
        errors.append("publish step must call verify_remote before gh release edit")
    else:
        final_call = calls_before_edit[-1]
        between = body[final_call.end() : edit_match.start()]
        if between.strip():
            errors.append("gh release edit must be the next command after final remote verification")
        if re.search(r"gh (?:release|api)\b", between) or "--clobber" in between:
            errors.append("no remote mutation may occur between final verification and publish")
    return errors


def validate_build_pixi_manifest_path(workflow_text: str) -> list[str]:
    """Require the build job to resolve its own locked plugin workspace."""

    errors: list[str] = []
    build_match = re.search(r"(?ms)^  build:\n(?P<body>.*?)(?=^  release:|\Z)", workflow_text)
    if not build_match:
        return ["release workflow must contain a build job"]
    setup_match = re.search(
        r"(?ms)^      - name: Set up Pixi\n(?P<body>.*?)(?=^      - name:|\Z)",
        build_match.group("body"),
    )
    if not setup_match:
        return ["build job must contain its Set up Pixi step"]
    manifest_match = re.search(r"(?m)^\s*manifest-path:\s*(\S+)\s*$", setup_match.group("body"))
    if not manifest_match:
        errors.append(f"build Set up Pixi step must set manifest-path to {BUILD_PIXI_MANIFEST}")
    elif manifest_match.group(1) != BUILD_PIXI_MANIFEST:
        errors.append(
            f"build Set up Pixi manifest-path must be {BUILD_PIXI_MANIFEST}, got {manifest_match.group(1)}"
        )
    return errors


def validate_pixi_lock_platforms(lock_text: str) -> list[str]:
    """Check non-empty per-platform environments and native package URLs."""

    errors: list[str] = []
    packages_match = re.search(r"(?ms)^    packages:\n(?P<body>.*?)(?=^packages:\n|\Z)", lock_text)
    if not packages_match:
        return ["pixi.lock default environment packages are missing"]
    packages = packages_match.group("body")
    for platform in PLATFORMS:
        platform_match = re.search(
            rf"(?ms)^      {re.escape(platform)}:\n(?P<body>.*?)(?=^      [^:\n]+:\n|\Z)",
            packages,
        )
        if not platform_match or not platform_match.group("body").strip():
            errors.append(f"pixi.lock platform environment is missing or empty: {platform}")
            continue
        body = platform_match.group("body")
        if not re.search(rf"https?://\S+/{re.escape(platform)}/python-\S+", body):
            errors.append(f"pixi.lock platform {platform} lacks a platform-specific python URL")
        if not re.search(rf"https?://\S+/{re.escape(platform)}/pyinstaller-\S+", body):
            errors.append(f"pixi.lock platform {platform} lacks a platform-specific pyinstaller URL")
    return errors


def validate_bootstrap_tracking(
    root: Path,
    bootstrap: Path,
    *,
    platform_name: str | None = None,
    git_runner: Callable[..., Any] = subprocess.run,
    stat_fn: Callable[[Path], Any] = os.stat,
) -> list[str]:
    """Require a uniquely tracked executable launcher without shell evaluation."""

    errors: list[str] = []
    try:
        relative = bootstrap.relative_to(root).as_posix()
    except ValueError:
        relative = BOOTSTRAP_RELATIVE_PATH
    try:
        result = git_runner(
            ["git", "ls-files", "--stage", "--", relative],
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, TypeError) as error:
        return [f"git ls-files bootstrap check failed: {error}"]
    if result.returncode != 0:
        errors.append("git ls-files bootstrap check failed")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        errors.append("bootstrap.sh must be tracked exactly once with git ls-files")
    elif len(lines) != 1:
        errors.append("bootstrap.sh has duplicate tracked index entries")
    else:
        fields = lines[0].split(None, 3)
        if len(fields) != 4 or fields[3] != relative:
            errors.append("git ls-files bootstrap entry has an unexpected path")
        elif fields[0] != "100755":
            errors.append(f"git-index bootstrap mode must be 100755, got {fields[0]}")
    if (platform_name or os.name) != "nt":
        try:
            if not stat_fn(bootstrap).st_mode & 0o111:
                errors.append("POSIX bootstrap.sh working-tree mode must be executable")
        except OSError as error:
            errors.append(f"bootstrap.sh working-tree stat failed: {error}")
    return errors


def validate_provisioner_contract(ps_text: str, version: object) -> list[str]:
    """Validate PowerShell safety markers and its default release version."""

    errors: list[str] = []
    if "Tls12" not in ps_text or "Get-FileHash" not in ps_text or "-in @('.', '..')" not in ps_text:
        errors.append("PowerShell provisioner must pin TLS 1.2, verify SHA-256, and reject dot generations")
    default_match = re.search(
        r"\$version\s*=\s*if\s*\([^\n]+\)\s*\{[^\n]+\}\s*else\s*\{\s*'([^']+)'\s*\}",
        ps_text,
    )
    if not default_match or default_match.group(1) != version:
        errors.append("PowerShell provisioner default version must equal plugin manifest version")
    return errors


def validate_provision_version(provision_text: str, version: object) -> list[str]:
    """Require the provision implementation's release marker to match."""

    match = re.search(r'^PROVISION_VERSION\s*=\s*["\']([^"\']+)', provision_text, re.MULTILINE)
    if not match or match.group(1) != version:
        return ["mcp/provision.py PROVISION_VERSION must equal plugin manifest version"]
    return []


def validate_bundled_mcp_contract(plugin: Path, manifest: dict[str, Any]) -> list[str]:
    """Validate the standard bundled MCP declaration and its safe launcher."""

    errors: list[str] = []
    if BUNDLED_MCP_SERVER == LEGACY_GLOBAL_MCP_SERVER:
        errors.append("bundled MCP server ID must not collide with the legacy global provision ID")
    if manifest.get("mcpServers") != f"./{BUNDLED_MCP_PATH}":
        errors.append("plugin.json mcpServers must equal './.mcp.json'")
    path = plugin / BUNDLED_MCP_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return errors + ["bundled .mcp.json is required when plugin.json mcpServers is declared"]
    except (OSError, json.JSONDecodeError):
        return errors + ["bundled .mcp.json must contain valid JSON"]
    if not isinstance(payload, dict):
        return errors + ["bundled .mcp.json must contain an object"]
    if set(payload) != {"mcpServers"} or not isinstance(payload.get("mcpServers"), dict):
        return errors + ["bundled .mcp.json must contain an mcpServers object"]
    servers = payload["mcpServers"]
    if set(servers) != {BUNDLED_MCP_SERVER}:
        return errors + ["bundled .mcp.json must contain exactly one creative-model-bridge-bundled server"]
    entry = servers[BUNDLED_MCP_SERVER]
    if not isinstance(entry, dict):
        return errors + ["bundled creative-model-bridge server must be an object"]
    if set(entry) != {"command", "args", "cwd", "env_vars"}:
        errors.append("bundled creative-model-bridge server fields are not the stdio contract")
    if entry.get("command") != "./scripts/bootstrap.sh":
        errors.append("bundled creative-model-bridge command must be relative ./scripts/bootstrap.sh")
    if entry.get("args") != ["serve"]:
        errors.append("bundled creative-model-bridge args must be ['serve']")
    if entry.get("cwd") != ".":
        errors.append("bundled creative-model-bridge cwd must be '.'")
    env_vars = entry.get("env_vars")
    valid_env_vars = isinstance(env_vars, list) and all(isinstance(value, str) for value in env_vars)
    if not valid_env_vars or len(env_vars) != len(set(env_vars)) or set(env_vars) != BUNDLED_MCP_ALLOWED_ENV_VARS:
        errors.append("bundled creative-model-bridge env_vars must use the approved runtime environment set")
    return errors


def validate(
    root: Path,
    tag: str | None = None,
    *,
    platform_name: str | None = None,
    git_runner: Callable[..., Any] = subprocess.run,
    stat_fn: Callable[[Path], Any] = os.stat,
) -> list[str]:
    errors: list[str] = []
    plugin = root / "plugins/creative-model-bridge"
    try:
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"plugin manifest is unreadable: {error}"]
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        errors.append("plugin manifest version must be numeric semver")
    if tag is not None:
        match = TAG_RE.fullmatch(tag)
        if not match:
            errors.append("tag must match creative-model-bridge-vX.Y.Z")
        elif match.group(1) != version:
            errors.append("tag version, plugin manifest, and bridge version differ")
    bridge = plugin / "mcp/bridge.py"
    bridge_text = bridge.read_text(encoding="utf-8") if bridge.is_file() else ""
    bridge_match = re.search(r'^BRIDGE_VERSION\s*=\s*["\']([^"\']+)', bridge_text, re.MULTILINE)
    if not bridge_match or bridge_match.group(1) != version:
        errors.append("mcp/bridge.py BRIDGE_VERSION must equal plugin manifest version")
    bootstrap = plugin / "scripts/bootstrap.sh"
    bootstrap_text = ""
    if not bootstrap.is_file():
        errors.append("scripts/bootstrap.sh must exist and be readable")
    else:
        try:
            bootstrap_text = bootstrap.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"scripts/bootstrap.sh must be readable: {error}")
        errors.extend(
            validate_bootstrap_tracking(
                root,
                bootstrap,
                platform_name=platform_name,
                git_runner=git_runner,
                stat_fn=stat_fn,
            )
        )
        default_match = re.search(r'CREATIVE_MODEL_BRIDGE_VERSION:-([^}]+)', bootstrap_text)
        if not default_match or default_match.group(1) != version:
            errors.append("bootstrap default version must equal plugin manifest version")
    if not bootstrap.is_file():
        errors.extend(
            validate_bootstrap_tracking(
                root,
                bootstrap,
                platform_name=platform_name,
                git_runner=git_runner,
                stat_fn=stat_fn,
            )
        )
    errors.extend(validate_bundled_mcp_contract(plugin, manifest))
    provision = plugin / "scripts/provision.ps1"
    if not provision.is_file():
        errors.append("scripts/provision.ps1 is required for Windows PowerShell 5.1")
    else:
        ps_text = provision.read_text(encoding="utf-8")
        errors.extend(validate_provisioner_contract(ps_text, version))
    workflow = root / ".github/workflows/release-creative-model-bridge.yml"
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.is_file() else ""
    if workflow.is_file():
        errors.extend(validate_publish_step_structure(workflow_text))
        errors.extend(validate_build_pixi_manifest_path(workflow_text))
    for asset in RELEASE_ASSETS:
        if asset not in workflow_text:
            errors.append(f"release workflow is missing asset contract: {asset}")
    if "creative-model-bridge-v*" not in workflow_text:
        errors.append("release workflow must trigger on creative-model-bridge-v* tags")
    if "--verify-tag" not in workflow_text:
        errors.append("release workflow must verify the tag before creating a release")
    if "--draft" not in workflow_text or "gh release download" not in workflow_text or "--draft=false" not in workflow_text:
        errors.append("release workflow must draft, remotely verify, then publish")
    if "concurrency:" not in workflow_text or "--clobber" not in workflow_text or "isDraft" not in workflow_text:
        errors.append("release workflow must reconcile concurrent absent/draft/published states")
    if re.search(r"uses:\s*[^@\n]+@(?![0-9a-f]{40}\b)", workflow_text):
        errors.append("release workflow actions must be pinned to commit SHAs")
    if "source\" != \"$destination" not in workflow_text:
        errors.append("release workflow must avoid same-file mv on native runners")
    if "if: runner.os != 'Windows'" not in workflow_text:
        errors.append("release workflow must not execute POSIX .sh tests on Windows")
    pixi = plugin / "pixi.toml"
    pixi_text = pixi.read_text(encoding="utf-8") if pixi.is_file() else ""
    if "pyinstaller" not in pixi_text.lower() or "build" not in pixi_text:
        errors.append("pixi.toml must lock the PyInstaller build task")
    for platform in PLATFORMS:
        if f'"{platform}"' not in pixi_text:
            errors.append(f"pixi.toml is missing locked build platform {platform}")
    lock = plugin / "pixi.lock"
    lock_text = lock.read_text(encoding="utf-8") if lock.is_file() else ""
    if not lock.is_file() or "pyinstaller" not in lock_text.lower():
        errors.append("pixi.lock must contain PyInstaller")
    else:
        errors.extend(validate_pixi_lock_platforms(lock_text))
    if (plugin / ".pixi/config.toml").exists():
        errors.append("plugin .pixi/config.toml direct-runtime target must be removed")
    bootstrap_contract = bootstrap.read_text(encoding="utf-8") if bootstrap.is_file() else ""
    for marker in ("runtime/v$version", "target_root", "active", "valid_digest", "cmb-active-v4", "generation", "complete", "staging.", "checksum", "provision \"$@\""):
        if marker not in bootstrap_contract:
            errors.append(f"bootstrap is missing immutable/token-lock contract marker: {marker}")
    if not (root / "scripts/reconcile_creative_model_bridge_release.py").is_file():
        errors.append("release state planner is missing")
    provision_py = plugin / "mcp/provision.py"
    provision_text = provision_py.read_text(encoding="utf-8") if provision_py.is_file() else ""
    errors.extend(validate_provision_version(provision_text, version))
    for marker in ("SCHEMA_VERSION = 2", '"phase": "prepared"', '"config_after"', "manual_required", "rollback_requested", "managed_digest"):
        if marker not in provision_text:
            errors.append(f"provision implementation is missing schema/WAL contract marker: {marker}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", nargs="?")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    errors = validate(root, args.tag)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("creative-model-bridge release contract validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
