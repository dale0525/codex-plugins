#!/usr/bin/env python3
"""Validate a production shot-list CSV and reconcile runtime/work totals."""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path


REQUIRED_COLUMNS = {
    "shot_id",
    "scene_id",
    "beat_id",
    "setup_id",
    "shoot_order",
    "priority",
    "purpose",
    "framing",
    "lens_or_fov",
    "camera_format",
    "fps_shutter",
    "camera_position",
    "movement",
    "axis",
    "focus_target",
    "focus_limits",
    "exposure_limits",
    "light",
    "sound",
    "audio_track_mic",
    "edit_trigger",
    "screen_duration_est",
    "screen_duration_contribution_est",
    "shoot_time_est",
    "schedule_start",
    "schedule_end",
    "cast_props",
    "location",
    "gear_crew_status",
    "slate",
    "continuity_risk",
    "safety_note",
    "fallback",
    "status",
}


def number(value: str, label: str, errors: list[str]) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label}: expected a number, got {value!r}")
        return 0.0
    if not math.isfinite(result) or result < 0:
        errors.append(f"{label}: must be a finite non-negative number")
    return result


def clock_minutes(value: str, label: str, errors: list[str]) -> int | None:
    parts = value.strip().split(":")
    if len(parts) != 2:
        errors.append(f"{label}: expected HH:MM")
        return None
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError:
        errors.append(f"{label}: expected HH:MM")
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        errors.append(f"{label}: invalid clock time")
        return None
    return hour * 60 + minute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--runtime-target-sec", type=float)
    parser.add_argument("--runtime-tolerance", type=float, default=0.10)
    parser.add_argument("--schedule-limit-min", type=float)
    parser.add_argument("--common-work-min", type=float, default=0.0)
    parser.add_argument("--required-beat", action="append", default=[])
    args = parser.parse_args()

    try:
        with args.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            rows = list(reader)
    except OSError as exc:
        print(f"ERROR: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    missing_columns = sorted(REQUIRED_COLUMNS - columns)
    if missing_columns:
        errors.append(f"missing columns: {', '.join(missing_columns)}")
    if not rows:
        errors.append("shot list has no rows")

    shot_ids: set[str] = set()
    must_beats: set[str] = set()
    runtime_total = 0.0
    shoot_total = args.common_work_min
    row_shoot_total = 0.0
    scheduled_intervals: list[tuple[int, int, str]] = []
    for index, row in enumerate(rows, start=2):
        label = f"row {index}"
        shot_id = row.get("shot_id", "").strip()
        if not shot_id:
            errors.append(f"{label}: shot_id is required")
        elif shot_id in shot_ids:
            errors.append(f"{label}: duplicate shot_id {shot_id}")
        shot_ids.add(shot_id)

        priority = row.get("priority", "").strip()
        if priority not in {"must", "should", "optional"}:
            errors.append(f"{label}: priority must be must, should or optional")
        if priority == "must":
            beat_id = row.get("beat_id", "").strip()
            if beat_id:
                must_beats.update(part.strip() for part in beat_id.split("|") if part.strip())
            if not row.get("fallback", "").strip():
                errors.append(f"{label}: must shot requires fallback")

        status = row.get("status", "").strip()
        if status not in {"planned", "shootable"}:
            errors.append(f"{label}: status must be planned or shootable")
        if status == "shootable":
            availability = row.get("gear_crew_status", "").lower()
            if any(flag in availability for flag in ("tbd", "unknown", "unconfirmed", "pending", "planned")):
                errors.append(f"{label}: shootable row has unresolved gear_crew_status")
            if not row.get("schedule_start", "").strip() or not row.get("schedule_end", "").strip():
                errors.append(f"{label}: shootable row requires schedule_start and schedule_end")

        for field in (
            "scene_id",
            "beat_id",
            "setup_id",
            "shoot_order",
            "purpose",
            "framing",
            "camera_format",
            "fps_shutter",
            "focus_limits",
            "exposure_limits",
            "audio_track_mic",
            "gear_crew_status",
            "safety_note",
        ):
            if not row.get(field, "").strip():
                errors.append(f"{label}: {field} is required")

        available_duration = number(
            row.get("screen_duration_est", ""),
            f"{label}.screen_duration_est",
            errors,
        )
        contribution = number(
            row.get("screen_duration_contribution_est", ""),
            f"{label}.screen_duration_contribution_est",
            errors,
        )
        runtime_total += contribution
        if contribution > available_duration:
            errors.append(
                f"{label}: final contribution {contribution:g}s exceeds available footage "
                f"{available_duration:g}s"
            )
        row_shoot = number(row.get("shoot_time_est", ""), f"{label}.shoot_time_est", errors)
        shoot_total += row_shoot
        row_shoot_total += row_shoot

        start_text = row.get("schedule_start", "").strip()
        end_text = row.get("schedule_end", "").strip()
        if bool(start_text) != bool(end_text):
            errors.append(f"{label}: schedule_start and schedule_end must appear together")
        elif start_text and end_text:
            start = clock_minutes(start_text, f"{label}.schedule_start", errors)
            end = clock_minutes(end_text, f"{label}.schedule_end", errors)
            if start is not None and end is not None:
                if end <= start:
                    errors.append(f"{label}: schedule_end must be after schedule_start on the same day")
                else:
                    scheduled_intervals.append((start, end, shot_id or label))

    if scheduled_intervals:
        interval_total = sum(end - start for start, end, _ in scheduled_intervals)
        if abs(interval_total - row_shoot_total) > 0.01:
            errors.append(
                "schedule intervals do not reconcile with shot rows: "
                f"intervals {interval_total:g}min, shoot_time_est {row_shoot_total:g}min"
            )
        ordered = sorted(scheduled_intervals)
        for (_, previous_end, previous_id), (start, _, shot_id) in zip(ordered, ordered[1:]):
            if start < previous_end:
                errors.append(f"schedule overlap: {previous_id} and {shot_id}")

    for beat_id in args.required_beat:
        if beat_id not in must_beats:
            errors.append(f"required beat lacks must coverage: {beat_id}")

    if args.runtime_target_sec is not None:
        delta = abs(runtime_total - args.runtime_target_sec)
        allowed = args.runtime_target_sec * args.runtime_tolerance
        if delta > allowed:
            errors.append(
                "runtime reconciliation failed: "
                f"contribution total {runtime_total:g}s, target {args.runtime_target_sec:g}s "
                f"±{args.runtime_tolerance:.0%}"
            )
    if args.schedule_limit_min is not None and shoot_total > args.schedule_limit_min:
        errors.append(
            "schedule reconciliation failed: "
            f"shot/common work total {shoot_total:g}min exceeds {args.schedule_limit_min:g}min"
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        f"OK: {len(rows)} shots, runtime contribution {runtime_total:g}s, "
        f"scheduled work {shoot_total:g}min, must beats {len(must_beats)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
