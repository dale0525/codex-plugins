#!/usr/bin/env python3
"""Normalize a YouTube JSON3 caption track into the local evidence schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SPACE_RE = re.compile(r"\s+")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def event_text(event: dict[str, Any]) -> str:
    parts = [str(item.get("utf8", "")) for item in event.get("segs", [])]
    return SPACE_RE.sub(" ", "".join(parts)).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("caption", type=Path)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.caption.read_text(encoding="utf-8"))
    segments: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        text = event_text(event)
        if not text:
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        end = start + duration
        if segments and segments[-1]["text"] == text and start <= segments[-1]["end_sec"]:
            segments[-1]["end_sec"] = round(max(end, segments[-1]["end_sec"]), 3)
            continue
        segments.append(
            {
                "id": f"seg-{len(segments) + 1:05d}",
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "text": text,
                "source": "youtube_json3_caption",
                "language": args.language,
            }
        )

    if not segments:
        raise SystemExit("caption contains no timed text segments")
    output = {
        "schema_version": 1,
        "video_id": args.video_id,
        "url": args.url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "method": "yt_dlp_youtube_json3",
        "caption": {
            "filename": args.caption.name,
            "sha256": sha256(args.caption),
            "language": args.language,
        },
        "segments": segments,
        "copyright_note": (
            "Local research evidence only. Do not redistribute the complete caption."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "segments": len(segments),
                "coverage_end_sec": segments[-1]["end_sec"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
