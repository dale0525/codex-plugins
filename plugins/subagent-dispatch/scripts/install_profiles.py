#!/usr/bin/env python3
"""Materialize plugin-owned native Codex subagent profiles idempotently."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - Python older than 3.11
    raise SystemExit("Python 3.11+ is required for subagent profile synchronization") from exc


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = PLUGIN_ROOT / "profiles"
PROFILE_NAMES = ("default", "creative_text", "image")
REQUIRED_KEYS = {
    "name",
    "description",
    "model",
    "model_reasoning_effort",
    "developer_instructions",
}


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def load_profiles() -> dict[str, str]:
    loaded: dict[str, str] = {}
    for profile_name in PROFILE_NAMES:
        path = PROFILE_ROOT / f"{profile_name}.toml"
        source = path.read_text(encoding="utf-8")
        data = tomllib.loads(source)
        missing = REQUIRED_KEYS - data.keys()
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"{path} is missing required keys: {missing_text}")
        if data["name"] != profile_name:
            raise ValueError(f"{path} name must be {profile_name!r}")
        loaded[profile_name] = source
    return loaded


def atomic_write(path: Path, content: str) -> bool:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary_path = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return True


def synchronize(check: bool) -> tuple[bool, list[dict[str, str]]]:
    destination = codex_home() / "agents"
    drift = False
    results: list[dict[str, str]] = []
    for profile_name, source in load_profiles().items():
        target = destination / f"{profile_name}.toml"
        missing = not target.exists()
        stale = not missing and target.read_text(encoding="utf-8") != source
        drift = drift or missing or stale
        if check:
            state = "missing" if missing else "stale" if stale else "ok"
        else:
            state = "updated" if atomic_write(target, source) else "ok"
        results.append({"profile": profile_name, "state": state, "path": str(target)})
    return drift, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check without writing")
    parser.add_argument("--quiet", action="store_true", help="suppress successful status lines")
    parser.add_argument("--json", action="store_true", help="emit machine-readable status")
    args = parser.parse_args()

    try:
        drift, results = synchronize(args.check)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": not drift if args.check else True,
                        "drift_detected": drift,
                        "profiles": results,
                    },
                    ensure_ascii=False,
                )
            )
        elif not args.quiet:
            for result in results:
                print(f"{result['profile']}: {result['state']} -> {result['path']}")
        return 1 if args.check and drift else 0
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"subagent profile synchronization failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
