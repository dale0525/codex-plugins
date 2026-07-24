#!/usr/bin/env python3
"""Collect a small, auditable evidence bundle for a public YouTube video.

The script intentionally does not bypass login, DRM, age gates, CAPTCHA, or rate
limits. Captions/media should only be collected when the caller is allowed to
access and process them. It uses optional yt-dlp, ffmpeg/ffprobe, and
youtube-transcript-api executables/packages when available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def video_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", value):
        return value
    parsed = urlparse(value)
    if parsed.netloc not in YOUTUBE_HOSTS:
        raise ValueError("expected a YouTube URL or video id")
    if parsed.netloc == "youtu.be":
        result = parsed.path.strip("/").split("/")[0]
    else:
        match = re.search(r"(?:^|/)shorts/([^/?]+)|(?:^|/)embed/([^/?]+)", parsed.path)
        result = (match.group(1) or match.group(2)) if match else ""
        if not result:
            from urllib.parse import parse_qs

            result = parse_qs(parsed.query).get("v", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", result):
        raise ValueError("could not find a YouTube video id")
    return result


def youtube_url(value: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id(value)}"


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} is not available; use pixi exec or provide a local file")
    return path


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise RuntimeError(f"could not run {command[0]}: {exc}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout)[-1200:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def curated_metadata(raw: dict[str, Any], url: str) -> dict[str, Any]:
    fields = (
        "id", "title", "channel", "channel_id", "channel_url", "uploader",
        "upload_date", "timestamp", "duration", "duration_string", "view_count",
        "categories", "description", "webpage_url", "availability", "license",
        "thumbnail",
    )
    data = {key: raw.get(key) for key in fields if key in raw}
    data.setdefault("id", video_id(url))
    data.setdefault("webpage_url", url)
    data["retrieved_at"] = utc_now()
    data["note"] = "Signed media URLs and format details are intentionally omitted."
    return data


def collect_metadata(value: str, output: Path) -> None:
    url = youtube_url(value)
    executable = require_binary("yt-dlp")
    result = run([executable, "--dump-single-json", "--skip-download", "--no-playlist", url])
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned non-JSON metadata") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(curated_metadata(raw, url), ensure_ascii=False, indent=2) + "\n")


def parse_vtt(path: Path) -> list[dict[str, Any]]:
    """Parse basic WebVTT cues without external dependencies."""
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    lines = text.splitlines()
    cues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = [item.strip() for item in line.split("-->", 1)]
        right = right.split(" ", 1)[0]
        try:
            start = timestamp_seconds(left)
            end = timestamp_seconds(right)
        except ValueError:
            index += 1
            continue
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(re.sub(r"<[^>]+>", "", lines[index]).strip())
            index += 1
        merged = re.sub(r"\s+", " ", " ".join(body)).strip()
        if merged:
            cues.append({"start_sec": start, "end_sec": end, "text": merged})
        index += 1
    return cues


def timestamp_seconds(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    raise ValueError(f"bad timestamp: {value}")


def transcript_snippets(value: str, languages: list[str]) -> tuple[list[dict[str, Any]], str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return [], "youtube-transcript-api not installed"
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id(value), languages=languages)
    except Exception as exc:  # library has version-specific exception classes
        return [], f"transcript API unavailable: {type(exc).__name__}: {str(exc)[:240]}"
    snippets: list[dict[str, Any]] = []
    for item in fetched:
        if hasattr(item, "text"):
            text, start, duration = item.text, item.start, item.duration
        else:
            text = item.get("text", "")
            start = float(item.get("start", 0))
            duration = float(item.get("duration", 0))
        snippets.append({
            "start_sec": float(start),
            "end_sec": float(start) + float(duration),
            "text": re.sub(r"\s+", " ", str(text)).strip(),
            "source": "caption_api",
            "language": languages[0] if languages else "unknown",
        })
    return snippets, "caption_api"


def yt_dlp_captions(value: str, languages: list[str], work_dir: Path) -> tuple[list[dict[str, Any]], str]:
    executable = require_binary("yt-dlp")
    work_dir.mkdir(parents=True, exist_ok=True)
    language_arg = ",".join(languages)
    output_template = str(work_dir / "%(id)s.%(ext)s")
    command = [
        executable, "--ignore-errors", "--no-playlist", "--skip-download",
        "--write-subs", "--write-auto-subs", "--sub-langs", language_arg,
        "--sub-format", "vtt", "-o", output_template, youtube_url(value),
    ]
    result = run(command, check=False)
    vtt_files = sorted(work_dir.glob("*.vtt"))
    if not vtt_files:
        detail = (result.stderr or result.stdout)[-800:]
        return [], f"yt-dlp captions unavailable: {detail}"
    cues = parse_vtt(vtt_files[0])
    for cue in cues:
        cue.update({"source": "yt-dlp_vtt", "language": vtt_files[0].stem.split(".")[-1]})
    return cues, "yt-dlp_vtt"


def collect_captions(value: str, languages: list[str], output: Path) -> None:
    snippets, method = transcript_snippets(value, languages)
    errors: list[str] = []
    if not snippets:
        errors.append(method)
        with tempfile.TemporaryDirectory(prefix="film-captions-") as temp:
            try:
                snippets, method = yt_dlp_captions(value, languages, Path(temp))
            except (RuntimeError, ValueError) as exc:
                snippets, method = [], "unavailable"
                errors.append(str(exc))
            if not snippets:
                errors.append(method)
    bundle = {
        "video_id": video_id(value),
        "url": youtube_url(value),
        "retrieved_at": utc_now(),
        "method": method if snippets else "unavailable",
        "segments": snippets,
        "errors": errors,
        "copyright_note": "Local evidence only; do not redistribute a full transcript.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")


def download_media(value: str, kind: str, output: Path, confirm_rights: bool) -> None:
    if not confirm_rights:
        raise RuntimeError("pass --confirm-rights only when you are allowed to download/process this media")
    executable = require_binary("yt-dlp")
    output.parent.mkdir(parents=True, exist_ok=True)
    selector = "worstaudio/worst" if kind == "audio" else "worst[ext=mp4]/worst"
    command = [
        executable, "--no-playlist", "--no-overwrites", "-f", selector,
        "-o", str(output), youtube_url(value),
    ]
    if kind == "audio":
        command[1:1] = ["-x", "--audio-format", "wav"]
    run(command)


def probe_frames(input_path: Path, output_dir: Path, interval: float, overwrite: bool) -> None:
    ffmpeg = require_binary("ffmpeg")
    ffprobe = require_binary("ffprobe")
    if interval <= 0:
        raise ValueError("interval must be positive")
    probe = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(input_path)])
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "frame_%06d.jpg"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    command.append("-y" if overwrite else "-n")
    command += [
        "-i", str(input_path), "-vf", f"fps=1/{interval},scale=1280:-1",
        "-q:v", "2", str(pattern),
    ]
    run(command)
    frames = []
    for index, frame in enumerate(sorted(output_dir.glob("frame_*.jpg"))):
        frames.append({
            "path": str(frame),
            "timestamp_sec": round(index * interval, 3),
            "sha256": sha256(frame),
        })
    manifest = {
        "input": str(input_path),
        "retrieved_at": utc_now(),
        "interval_sec": interval,
        "probe": json.loads(probe.stdout),
        "frame_status": "sampled",
        "frames": frames,
        "note": "Fixed-rate samples are not scene-change truth; inspect and annotate selected frames.",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    meta = sub.add_parser("metadata", help="save curated yt-dlp metadata")
    meta.add_argument("source")
    meta.add_argument("--out", type=Path, required=True)

    captions = sub.add_parser("captions", help="fetch captions or report a safe fallback")
    captions.add_argument("source")
    captions.add_argument("--languages", default="en,zh-Hans,zh-CN")
    captions.add_argument("--out", type=Path, required=True)

    media = sub.add_parser("download", help="download authorized audio/video")
    media.add_argument("source")
    media.add_argument("--kind", choices=["audio", "video"], default="audio")
    media.add_argument("--out", type=Path, required=True)
    media.add_argument("--confirm-rights", action="store_true")

    frames = sub.add_parser("frames", help="sample a local video with ffmpeg")
    frames.add_argument("input", type=Path)
    frames.add_argument("--out-dir", type=Path, required=True)
    frames.add_argument("--interval", type=float, default=10.0)
    frames.add_argument("--overwrite", action="store_true")
    return root


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "metadata":
            collect_metadata(args.source, args.out)
        elif args.command == "captions":
            collect_captions(args.source, [x.strip() for x in args.languages.split(",") if x.strip()], args.out)
        elif args.command == "download":
            download_media(args.source, args.kind, args.out, args.confirm_rights)
        elif args.command == "frames":
            probe_frames(args.input, args.out_dir, args.interval, args.overwrite)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
