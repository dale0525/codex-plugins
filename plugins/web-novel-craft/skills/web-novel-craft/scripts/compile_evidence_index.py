#!/usr/bin/env python3
"""Compile non-copyright transcript provenance into a distributable evidence index."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def segment_bounds(segments: list[dict[str, Any]]) -> tuple[float, float]:
    starts = [float(item["start_sec"]) for item in segments]
    ends = [float(item.get("end_sec", item["start_sec"])) for item in segments]
    return min(starts), max(ends)


def compile_entry(source: dict[str, Any], path: Path) -> dict[str, Any]:
    payload = read_json(path)
    segments = payload.get("segments", [])
    if not segments:
        raise ValueError(f"{source['id']}: transcript has no segments")
    first, last = segment_bounds(segments)
    method = payload.get("method", "unknown")
    entry: dict[str, Any] = {
        "id": source["id"],
        "platform": source["platform"],
        "method": method,
        "language": payload.get("language")
        or segments[0].get("language")
        or segments[0].get("source")
        or "unknown",
        "segment_count": len(segments),
        "coverage_start_sec": round(first, 3),
        "coverage_end_sec": round(last, 3),
        "media_duration_sec": float(payload.get("duration_sec", source["duration_sec"])),
        "transcript_sha256": sha256(path),
        "retrieved_at": payload.get("retrieved_at"),
        "raw_transcript_distributed": False,
        "visual_review": "none",
        "coverage_status": "complete_speech_track",
    }
    if method == "faster_whisper_asr":
        entry["tool"] = payload.get("tool", {})
        entry["language_probability"] = payload.get("language_probability")
        entry["media_sha256"] = payload.get("media", {}).get("sha256")
    else:
        entry["caption_source"] = segments[0].get("source", method)
        entry["caption_language"] = segments[0].get("language", "unknown")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--transcript-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    manifest = read_json(args.manifest)
    entries = []
    for source in manifest["sources"]:
        path = args.transcript_dir / f"{source['id']}.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        entries.append(compile_entry(source, path))

    output = {
        "schema_version": 1,
        "corpus_id": manifest["corpus_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "copyright_policy": (
            "This index distributes provenance and coverage only. Raw captions, "
            "ASR text, audio, and video remain outside the plugin."
        ),
        "sources": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
