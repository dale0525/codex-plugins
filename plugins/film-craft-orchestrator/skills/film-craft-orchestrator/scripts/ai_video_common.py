#!/usr/bin/env python3
"""Shared deterministic helpers for AI video package validators."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required; run with `pixi exec --with pyyaml ...`") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def require_fields(
    value: dict[str, Any],
    fields: list[str],
    label: str,
    errors: list[str],
    *,
    empty_list_ok_fields: set[str] | None = None,
) -> None:
    empty_list_ok_fields = empty_list_ok_fields or set()
    for field in fields:
        if field not in value or value[field] in (None, ""):
            errors.append(f"{label}: missing {field}")
        elif value[field] in ([], {}) and field not in empty_list_ok_fields:
            errors.append(f"{label}: missing {field}")


def parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    separator = "|" if "|" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: str) -> bool:
    text = value.removeprefix("sha256:")
    return len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text)


def die_on_errors(errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    return 0
