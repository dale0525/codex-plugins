#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe an authorized local media file with faster-whisper."
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--download-root", type=Path, default=None)
    parser.add_argument("--beam-size", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.media.is_file():
        raise SystemExit(f"media file does not exist: {args.media}")

    from faster_whisper import WhisperModel, __version__ as faster_whisper_version

    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        download_root=str(args.download_root) if args.download_root else None,
    )
    segments_iter, info = model.transcribe(
        str(args.media),
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=True,
        word_timestamps=False,
    )
    segments = []
    for index, segment in enumerate(segments_iter, start=1):
        text = segment.text.strip()
        if not text:
            continue
        segments.append(
            {
                "id": f"seg-{index:05d}",
                "start_sec": round(float(segment.start), 3),
                "end_sec": round(float(segment.end), 3),
                "text": text,
                "avg_logprob": round(float(segment.avg_logprob), 5),
                "no_speech_prob": round(float(segment.no_speech_prob), 5),
                "source": "faster_whisper_asr",
            }
        )

    payload = {
        "schema_version": 1,
        "media": {
            "path": str(args.media.resolve()),
            "sha256": sha256_file(args.media),
        },
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "method": "faster_whisper_asr",
        "tool": {
            "name": "faster-whisper",
            "version": faster_whisper_version,
            "model": args.model,
            "device": args.device,
            "compute_type": args.compute_type,
            "beam_size": args.beam_size,
            "vad_filter": True,
        },
        "language": info.language,
        "language_probability": round(float(info.language_probability), 5),
        "duration_sec": round(float(info.duration), 3),
        "duration_after_vad_sec": round(float(info.duration_after_vad), 3),
        "segments": segments,
        "copyright_note": (
            "Local research evidence only. Do not redistribute the complete transcript."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "out": str(args.out),
                "segments": len(segments),
                "language": info.language,
                "duration_sec": payload["duration_sec"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
