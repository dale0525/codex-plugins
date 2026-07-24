#!/usr/bin/env python3
"""Validate canonical AI video clip plans."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_video_common import die_on_errors, load_csv, parse_list


REQUIRED_COLUMNS = [
    "clip_id", "scene_id", "beat_ids", "priority", "narrative_purpose",
    "target_duration_sec", "edit_contribution_sec", "handle_in_sec", "handle_out_sec",
    "subject_ids", "prop_ids", "location_id", "entry_state_ref", "primary_action", "attention_change",
    "camera_behavior", "exit_state_ref", "generation_method", "visual_bible_ref",
    "reference_inputs_expected", "prompt_pack_ref", "continuity_risks", "fallback", "status",
]

# These columns are part of the schema but may legitimately be empty. A location-only
# establishing clip can have neither a character nor a prop; a prop insert can have no
# character; and a clip may require no reference asset or have no known continuity risk.
EMPTY_VALUE_OK_COLUMNS = {
    "subject_ids",
    "prop_ids",
    "reference_inputs_expected",
    "continuity_risks",
}


def number(row: dict[str, str], field: str, label: str, errors: list[str]) -> float:
    try:
        value = float(row.get(field, ""))
    except ValueError:
        errors.append(f"{label}: {field} must be numeric")
        return 0.0
    if value < 0:
        errors.append(f"{label}: {field} cannot be negative")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip_plan", type=Path)
    parser.add_argument("--runtime-target", type=float)
    parser.add_argument("--runtime-tolerance", type=float, default=0.01)
    args = parser.parse_args()
    rows = load_csv(args.clip_plan)
    errors: list[str] = []
    if not rows:
        errors.append("clip plan has no rows")
        return die_on_errors(errors)
    missing_columns = [field for field in REQUIRED_COLUMNS if field not in rows[0]]
    if missing_columns:
        errors.append("missing columns: " + ", ".join(missing_columns))
        return die_on_errors(errors)
    clip_ids: set[str] = set()
    edit_total = 0.0
    for index, row in enumerate(rows, start=2):
        label = f"row {index}"
        if None in row:
            errors.append(f"{label}: extra unescaped CSV fields; quote values containing commas")
        if any(value is None for key, value in row.items() if key is not None):
            errors.append(f"{label}: missing trailing CSV fields")
        for field in REQUIRED_COLUMNS:
            if field in EMPTY_VALUE_OK_COLUMNS:
                continue
            if not str(row.get(field, "")).strip():
                errors.append(f"{label}: missing {field}")
        clip_id = row.get("clip_id", "")
        if clip_id in clip_ids:
            errors.append(f"{label}: duplicate clip_id {clip_id}")
        clip_ids.add(clip_id)
        if not parse_list(row.get("beat_ids")):
            errors.append(f"{label}: beat_ids must contain at least one ID")
        target = number(row, "target_duration_sec", label, errors)
        contribution = number(row, "edit_contribution_sec", label, errors)
        handle_in = number(row, "handle_in_sec", label, errors)
        handle_out = number(row, "handle_out_sec", label, errors)
        edit_total += contribution
        if target + 1e-9 < contribution + handle_in + handle_out:
            errors.append(
                f"{label}: target_duration_sec {target} < contribution+handles "
                f"{contribution + handle_in + handle_out}"
            )
        if row.get("priority") not in {"must", "should", "optional"}:
            errors.append(f"{label}: invalid priority {row.get('priority')}")
        if row.get("status") not in {
            "draft", "planned", "queued", "generated", "qc_pending", "approved", "rejected", "superseded"
        }:
            errors.append(f"{label}: invalid status {row.get('status')}")
        method = str(row.get("generation_method", "")).lower()
        if method in {
            "image_first_frame",
            "first_frame_image_to_video",
            "first-frame-i2v",
            "i2v_first_frame",
        } and not parse_list(row.get("reference_inputs_expected")):
            errors.append(f"{label}: {method} requires a planned first-frame reference")
    if args.runtime_target is not None and abs(edit_total - args.runtime_target) > args.runtime_tolerance:
        errors.append(f"edit contribution total {edit_total} != runtime target {args.runtime_target}")
    result = die_on_errors(errors)
    if not result:
        print(f"OK: {len(rows)} clips, edit contribution {edit_total:g}s")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
