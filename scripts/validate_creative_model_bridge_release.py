#!/usr/bin/env python3
"""Validate the Creative Model Bridge release contract without network access."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ASSETS = {
    "creative-model-bridge-aarch64-apple-darwin",
    "creative-model-bridge-x86_64-apple-darwin",
    "creative-model-bridge-x86_64-unknown-linux-gnu",
    "creative-model-bridge-aarch64-unknown-linux-gnu",
    "creative-model-bridge-x86_64-pc-windows-msvc.exe",
}
RELEASE_ASSETS = ASSETS | {"checksums.txt"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
TAG_RE = re.compile(r"^creative-model-bridge-v(\d+\.\d+\.\d+)$")
PUBLISH_STEP_NAME = "Publish verified draft, or confirm published exact no-op"


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


def validate(root: Path, tag: str | None = None) -> list[str]:
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
    if not bootstrap.is_file() or not bootstrap.stat().st_mode & 0o111:
        errors.append("scripts/bootstrap.sh must exist and be executable")
    else:
        bootstrap_text = bootstrap.read_text(encoding="utf-8")
        default_match = re.search(r'CREATIVE_MODEL_BRIDGE_VERSION:-([^}]+)', bootstrap_text)
        if not default_match or default_match.group(1) != version:
            errors.append("bootstrap default version must equal plugin manifest version")
    if (plugin / ".mcp.json").exists():
        errors.append("bundled .mcp.json must not be distributed; provisioning owns global MCP config")
    provision = plugin / "scripts/provision.ps1"
    if not provision.is_file():
        errors.append("scripts/provision.ps1 is required for Windows PowerShell 5.1")
    else:
        ps_text = provision.read_text(encoding="utf-8")
        if "Tls12" not in ps_text or "Get-FileHash" not in ps_text or "-in @('.', '..')" not in ps_text:
            errors.append("PowerShell provisioner must pin TLS 1.2, verify SHA-256, and reject dot generations")
    workflow = root / ".github/workflows/release-creative-model-bridge.yml"
    workflow_text = workflow.read_text(encoding="utf-8") if workflow.is_file() else ""
    if workflow.is_file():
        errors.extend(validate_publish_step_structure(workflow_text))
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
    for platform in ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64"):
        if f'"{platform}"' not in pixi_text:
            errors.append(f"pixi.toml is missing locked build platform {platform}")
    lock = plugin / "pixi.lock"
    if not lock.is_file() or "pyinstaller" not in lock.read_text(encoding="utf-8").lower():
        errors.append("pixi.lock must contain PyInstaller")
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
