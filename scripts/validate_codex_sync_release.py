#!/usr/bin/env python3
"""Validate that a Codex Sync release tag matches packaged versions."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/codex-sync"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("codex-sync-v"):
        print("usage: validate_codex_sync_release.py codex-sync-v<version>", file=sys.stderr)
        return 2
    expected = sys.argv[1].removeprefix("codex-sync-v")
    manifest = json.loads(
        (PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    with (PLUGIN / "engine/Cargo.toml").open("rb") as handle:
        cargo = tomllib.load(handle)
    observed = {
        "plugin manifest": manifest["version"],
        "Rust package": cargo["package"]["version"],
    }
    for label, version in observed.items():
        if version != expected:
            print(f"{label} version {version} does not match release {expected}", file=sys.stderr)
            return 1
    script_markers = {
        "POSIX bootstrap": (
            PLUGIN / "scripts/bootstrap.sh",
            f"CODEX_SYNC_VERSION:-{expected}",
        ),
        "Windows bootstrap": (
            PLUGIN / "scripts/bootstrap.ps1",
            f"else {{ '{expected}' }}",
        ),
    }
    for label, (path, marker) in script_markers.items():
        if marker not in path.read_text(encoding="utf-8"):
            print(f"{label} does not default to release {expected}", file=sys.stderr)
            return 1
    print(f"Codex Sync release versions match {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
