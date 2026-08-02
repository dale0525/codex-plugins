#!/usr/bin/env python3
"""Pure release-state planner used before any GitHub release mutation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


ASSETS = {
    "creative-model-bridge-aarch64-apple-darwin",
    "creative-model-bridge-x86_64-apple-darwin",
    "creative-model-bridge-x86_64-unknown-linux-gnu",
    "creative-model-bridge-aarch64-unknown-linux-gnu",
    "creative-model-bridge-x86_64-pc-windows-msvc.exe",
    "checksums.txt",
}
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def normalize_digest(value: str) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("asset digest must be exactly sha256:<64 lowercase hex>")
    return value[7:]


def desired_from_checksums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {"checksums.txt": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 2 or not DIGEST.fullmatch(fields[0]) or fields[1] not in ASSETS - {"checksums.txt"} or fields[1] in values:
            raise ValueError("checksums.txt contains an invalid, duplicate, or unknown asset")
        values[fields[1]] = "sha256:" + fields[0]
    if set(values) != ASSETS or len(values) != 6:
        raise ValueError("checksums.txt does not describe exactly the five binaries")
    values["checksums.txt"] = ""  # Its digest is checked after remote download.
    return values


def plan(state: dict[str, Any], desired: dict[str, str]) -> dict[str, Any]:
    status = state.get("status", "absent")
    if status not in {"absent", "draft", "published"}:
        raise ValueError("release status must be absent, draft, or published")
    assets = state.get("assets", [])
    desired = {name: normalize_digest(value) for name, value in desired.items()}
    digest_map: dict[str, str] = ({name: normalize_digest(value) if value else "" for name, value in assets.items()} if isinstance(assets, dict) else {})
    if isinstance(assets, dict):
        names = set(assets)
    elif isinstance(assets, list) and all(isinstance(item, str) for item in assets):
        names = set(assets)
    else:
        raise ValueError("release assets must be a name list or object")
    unknown = names - ASSETS
    if unknown:
        raise ValueError(f"unknown release assets: {sorted(unknown)}")
    missing = ASSETS - names
    if status == "absent":
        return {"action": "create-draft", "mutation": True}
    if status == "published":
        if missing:
            raise ValueError(f"published release is missing assets: {sorted(missing)}")
        mismatched = [name for name in names if digest_map.get(name) and desired.get(name) and digest_map[name] != desired[name]]
        if mismatched:
            raise ValueError(f"published release has mismatched assets: {sorted(mismatched)}")
        return {"action": "published-read-only", "mutation": False}
    if missing:
        return {"action": "complete-draft", "mutation": True}
    mismatched = [name for name in names if digest_map.get(name) and desired.get(name) and digest_map[name] != desired[name]]
    if mismatched:
        return {"action": "replace-draft", "mutation": True}
    return {"action": "verify-draft", "mutation": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--checksums", type=Path, required=True)
    args = parser.parse_args()
    state = {"status": "absent", "assets": []}
    if args.state_file and args.state_file.exists():
        state = json.loads(args.state_file.read_text(encoding="utf-8"))
        if state.get("isDraft") is True:
            state["status"] = "draft"
        elif state.get("isDraft") is False:
            state["status"] = "published"
        assets = state.get("assets", [])
        if isinstance(assets, list) and assets and isinstance(assets[0], dict):
            state["assets"] = {item.get("name"): item.get("digest", "") for item in assets}
    try:
        result = plan(state, desired_from_checksums(args.checksums))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
