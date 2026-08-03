"""Strict, byte-preserving ownership parsing for Creative Model Bridge."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any


class OwnershipError(ValueError):
    """Raised when ownership markers/tables are malformed or ambiguous."""


BEGIN_PREFIX = "creative-model-bridge:begin"
END_PREFIX = "creative-model-bridge:end"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def scan_markers(text: str, *, allow_incomplete: bool = False) -> dict[str, Any] | None:
    """Scan only canonical marker lines and return exact line offsets."""

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for raw in lines:
        offsets.append(cursor)
        cursor += len(raw)
    begin: tuple[int, str] | None = None
    end: tuple[int, str] | None = None
    begin_match_re = re.compile(r'# creative-model-bridge:begin schema=1 install_id="([^"]+)"')
    end_match_re = re.compile(r'# creative-model-bridge:end install_id="([^"]+)"')
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        has_begin = BEGIN_PREFIX in line
        has_end = END_PREFIX in line
        if not has_begin and not has_end:
            continue
        begin_match = re.fullmatch(begin_match_re, line)
        end_match = re.fullmatch(end_match_re, line)
        if (has_begin and begin_match is None) or (has_end and end_match is None):
            raise OwnershipError("creative-model-bridge marker is malformed")
        if begin_match:
            if begin is not None or end is not None:
                raise OwnershipError("creative-model-bridge begin marker is repeated or nested")
            install_id = begin_match.group(1)
            if not UUID_RE.fullmatch(install_id):
                raise OwnershipError("creative-model-bridge begin install_id is invalid")
            begin = (index, install_id)
        if end_match:
            if begin is None or end is not None:
                raise OwnershipError("creative-model-bridge end marker is isolated or repeated")
            install_id = end_match.group(1)
            if not UUID_RE.fullmatch(install_id) or install_id != begin[1]:
                raise OwnershipError("creative-model-bridge marker install_id mismatch")
            end = (index, install_id)
    if begin is None and end is None:
        return None
    if begin is None:
        raise OwnershipError("creative-model-bridge marker pair is incomplete")
    if end is None:
        if not allow_incomplete:
            raise OwnershipError("creative-model-bridge marker pair is incomplete")
        end_index = None
    else:
        if end[0] <= begin[0]:
            raise OwnershipError("creative-model-bridge marker pair is incomplete")
        end_index = end[0]
    begin_start = offsets[begin[0]]
    begin_end = begin_start + len(lines[begin[0]])
    end_start = offsets[end_index] if end_index is not None else None
    end_end = end_start + len(lines[end_index]) if end_index is not None else None
    return {
        "install_id": begin[1], "start": begin[0], "end": end_index,
        "begin_start": begin_start, "begin_end": begin_end,
        "end_start": end_start, "end_end": end_end,
        "block": "".join(lines[begin[0] : end_index + 1]) if end_index is not None else None,
        "lines": lines, "offsets": offsets,
    }


def marker(text: str) -> dict[str, Any] | None:
    parsed = scan_markers(text)
    if parsed is None:
        return None
    return {key: parsed[key] for key in ("install_id", "block", "start", "end")}


def _table_heading(raw: str) -> tuple[str, str] | None:
    line = raw.rstrip("\r\n")
    stripped = line.lstrip()
    if not stripped.startswith("["):
        return None
    if stripped.startswith("[["):
        if not stripped.endswith("]]"):
            return ("array", stripped[2:])
        return ("array", stripped[2:-2].strip())
    if not stripped.endswith("]"):
        return ("table", stripped[1:])
    return ("table", stripped[1:-1].strip())


def owned_config(text: str, parse_toml: Callable[[str], dict[str, Any]]) -> dict[str, Any] | None:
    """Parse canonical CMB tables and return original character spans."""

    marker_data = scan_markers(text, allow_incomplete=True)
    if marker_data is None:
        return None
    lines = marker_data["lines"]
    offsets = marker_data["offsets"]
    heading_indices: list[int] = []
    exact: dict[str, list[int]] = {"root": [], "env": []}
    canonical = {
        "root": "mcp_servers.creative-model-bridge",
        "env": "mcp_servers.creative-model-bridge.env",
    }
    for index, raw in enumerate(lines):
        heading = _table_heading(raw)
        if heading is None:
            continue
        heading_indices.append(index)
        kind, path = heading
        if "creative-model-bridge" not in path:
            continue
        expected_kind = "root" if path == canonical["root"] else "env" if path == canonical["env"] else ""
        if kind != "table" or not expected_kind or raw.rstrip("\r\n") != f"[{canonical[expected_kind]}]":
            raise OwnershipError("creative-model-bridge table heading is not canonical")
        exact[expected_kind].append(index)
    if len(exact["root"]) != 1 or len(exact["env"]) != 1:
        raise OwnershipError("creative-model-bridge ownership requires exactly two canonical tables")
    marker_end = marker_data["end"] if marker_data["end"] is not None else len(lines)
    expanded = any(
        marker_data["start"] < index < marker_end
        for index in heading_indices
        if index not in {exact["root"][0], exact["env"][0]}
    )
    for indices in exact.values():
        index = indices[0]
        if index <= marker_data["start"] or (marker_data["end"] is not None and index >= marker_data["end"]):
            raise OwnershipError("creative-model-bridge tables are outside ownership markers")
    spans: list[tuple[int, int, str]] = []
    for key, indices in exact.items():
        index = indices[0]
        next_heading = next((candidate for candidate in heading_indices if candidate > index), len(lines))
        end_offset = offsets[next_heading] if next_heading < len(lines) else len(text)
        if marker_data["end_start"] is not None:
            end_offset = min(end_offset, marker_data["end_start"])
        if "#" in text[offsets[index] : end_offset]:
            raise OwnershipError("creative-model-bridge table span contains an unowned comment")
        spans.append((offsets[index], end_offset, key))
    spans.sort()
    value = parse_toml(text)
    servers = value.get("mcp_servers")
    entry = servers.get("creative-model-bridge") if isinstance(servers, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("env"), dict):
        raise OwnershipError("creative-model-bridge tables are semantically incomplete")
    return {
        "install_id": marker_data["install_id"], "complete": marker_data["end"] is not None,
        "expanded": expanded,
        "start": marker_data["start"], "end": marker_data["end"],
        "begin_start": marker_data["begin_start"], "begin_end": marker_data["begin_end"],
        "end_start": marker_data["end_start"], "end_end": marker_data["end_end"],
        "root_span": spans[0][:2], "env_span": spans[1][:2],
        "spans": [(start, end) for start, end, _ in spans],
        "marker_spans": [(marker_data["begin_start"], marker_data["begin_end"])] + (
            [(marker_data["end_start"], marker_data["end_end"])] if marker_data["end_start"] is not None else []
        ),
        "block": marker_data["block"], "value": value,
    }


def remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    result = text
    for start, end in sorted(spans, reverse=True):
        result = result[:start] + result[end:]
    return result
