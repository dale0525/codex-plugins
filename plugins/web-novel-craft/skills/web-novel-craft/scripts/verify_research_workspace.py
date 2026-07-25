#!/usr/bin/env python3
"""Resolve distributed timestamp claims against local non-distributed transcripts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_FILES = (
    "video-knowledge-base.json",
    "video-extension-knowledge-base.json",
    "video-priority-234-knowledge-base.json",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def distance_to_segment(timestamp: float, segment: dict[str, Any]) -> float:
    start = float(segment["start_sec"])
    end = float(segment.get("end_sec", start))
    if start <= timestamp <= end:
        return 0.0
    return min(abs(timestamp - start), abs(timestamp - end))


def index_transcripts(directories: list[Path]) -> tuple[dict[str, Path], list[str]]:
    indexed: dict[str, Path] = {}
    failures: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            failures.append(f"missing transcript directory: {directory}")
            continue
        for path in directory.glob("*.json"):
            source_id = path.stem
            if source_id in indexed:
                failures.append(f"duplicate transcript ID {source_id}: {indexed[source_id]} and {path}")
            else:
                indexed[source_id] = path
    return indexed, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("transcript_dirs", type=Path, nargs="+")
    parser.add_argument("--max-distance-sec", type=float, default=15.0)
    args = parser.parse_args()

    transcript_paths, failures = index_transcripts(args.transcript_dirs)
    knowledge_sources: list[dict[str, Any]] = []
    for filename in KNOWLEDGE_FILES:
        payload = read_json(ROOT / "references" / filename)
        knowledge_sources.extend(payload["sources"])

    checked = 0
    seen: set[str] = set()
    for source in knowledge_sources:
        source_id = source["id"]
        if source_id in seen:
            failures.append(f"duplicate knowledge source: {source_id}")
            continue
        seen.add(source_id)
        transcript_path = transcript_paths.get(source_id)
        if transcript_path is None:
            failures.append(f"{source_id}: missing transcript")
            continue
        segments = read_json(transcript_path).get("segments", [])
        if not segments:
            failures.append(f"{source_id}: no segments")
            continue
        last = max(float(item.get("end_sec", item["start_sec"])) for item in segments)
        if float(source["reviewed_to_sec"]) < last - args.max_distance_sec:
            failures.append(f"{source_id}: review endpoint precedes transcript endpoint")
        for index, claim in enumerate(source["claims"]):
            timestamp = float(claim["timestamp_sec"])
            nearest = min(distance_to_segment(timestamp, item) for item in segments)
            checked += 1
            if nearest > args.max_distance_sec:
                failures.append(
                    f"{source_id} claim {index}: {timestamp:.3f}s is {nearest:.3f}s from speech"
                )

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1
    print(f"Research workspace valid: {checked} claims resolve across {len(seen)} transcripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
