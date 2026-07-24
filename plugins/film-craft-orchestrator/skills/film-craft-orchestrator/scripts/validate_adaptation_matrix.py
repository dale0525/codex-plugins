#!/usr/bin/env python3
"""Validate a canonical source-to-screen adaptation matrix."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from ai_video_common import die_on_errors, load_yaml


REQUIRED_COLUMNS = [
    "source_unit", "source_anchor", "source_function", "screen_unit",
    "delivery_requirement_ids", "invent_ids", "preservation",
    "new_visual_or_sound_device", "character_changes",
    "structural_change", "reason", "risks", "verification", "rights_status",
]
REQUIRED_VALUES = {
    "source_unit", "source_anchor", "source_function", "screen_unit",
    "delivery_requirement_ids", "preservation", "reason", "risks",
    "verification", "rights_status",
}
PRESERVATION_VALUES = {
    "must_preserve", "preserve", "translate", "compress", "merge", "omit", "invent",
}
RIGHTS_VALUES = {
    "user-owned", "licensed", "public-domain", "unknown", "not-applicable",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def split_ids(value: str) -> list[str]:
    return [item for item in re.split(r"[|,;/\s]+", value.strip()) if item]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("adaptation_matrix", type=Path)
    parser.add_argument("--story-map", type=Path)
    args = parser.parse_args()
    errors: list[str] = []

    with args.adaptation_matrix.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if fieldnames != REQUIRED_COLUMNS:
        errors.append(
            "adaptation matrix columns must exactly match canonical order; "
            f"got {fieldnames}"
        )
    if not rows:
        errors.append("adaptation matrix has no rows")

    represented_screen_units: set[str] = set()
    represented_requirements: set[str] = set()
    row_mappings: list[tuple[set[str], set[str]]] = []
    invent_ids: set[str] = set()
    source_anchors: set[str] = set()
    for index, row in enumerate(rows, start=2):
        label = f"adaptation-matrix.csv row {index}"
        if None in row:
            errors.append(
                f"{label}: extra unescaped CSV fields {row.get(None)}; "
                "quote values containing commas"
            )
        for field in REQUIRED_VALUES:
            if not str(row.get(field) or "").strip():
                errors.append(f"{label}: missing {field}")
        preservation = str(row.get("preservation") or "")
        if preservation not in PRESERVATION_VALUES:
            errors.append(f"{label}: invalid preservation {preservation!r}")
        rights = str(row.get("rights_status") or "")
        if rights not in RIGHTS_VALUES:
            errors.append(f"{label}: invalid rights_status {rights!r}")
        source_anchor = str(row.get("source_anchor") or "").strip()
        if len(source_anchor) < 4:
            errors.append(f"{label}: source_anchor must contain an exact source phrase")
        elif source_anchor in source_anchors:
            errors.append(f"{label}: duplicate source_anchor {source_anchor!r}")
        source_anchors.add(source_anchor)
        row_screen_units = split_ids(str(row.get("screen_unit") or ""))
        row_requirements = split_ids(str(row.get("delivery_requirement_ids") or ""))
        represented_screen_units.update(row_screen_units)
        for requirement_id in row_requirements:
            if not ID_PATTERN.fullmatch(requirement_id):
                errors.append(f"{label}: invalid delivery_requirement_id {requirement_id!r}")
            represented_requirements.add(requirement_id)
        row_mappings.append((set(row_screen_units), set(row_requirements)))
        for invent_id in split_ids(str(row.get("invent_ids") or "")):
            if not ID_PATTERN.fullmatch(invent_id):
                errors.append(f"{label}: invalid invent_id {invent_id!r}")
            elif invent_id in invent_ids:
                errors.append(f"{label}: duplicate invent_id {invent_id}")
            invent_ids.add(invent_id)

    if args.story_map:
        story = load_yaml(args.story_map)
        beat_ids = {
            str(beat.get("beat_id"))
            for scene in (story.get("scenes") or [])
            if isinstance(scene, dict)
            for beat in (scene.get("beats") or [])
            if isinstance(beat, dict) and beat.get("beat_id")
        }
        must_beat_ids = {str(item) for item in (story.get("must_beat_ids") or [])}
        delivery_requirements = story.get("delivery_requirements") or []
        story_requirement_ids: set[str] = set()
        for index, requirement in enumerate(delivery_requirements):
            if not isinstance(requirement, dict) or not requirement.get("requirement_id"):
                errors.append(
                    f"story delivery_requirements[{index}]: missing requirement_id"
                )
                continue
            requirement_id = str(requirement["requirement_id"])
            if requirement_id in story_requirement_ids:
                errors.append(
                    f"story delivery_requirements[{index}]: duplicate requirement_id {requirement_id}"
                )
            story_requirement_ids.add(requirement_id)
            if not str(requirement.get("content") or "").strip():
                errors.append(
                    f"story delivery_requirements[{index}]: missing content"
                )
            if not requirement.get("approved_carriers"):
                errors.append(
                    f"story delivery_requirements[{index}]: missing approved_carriers"
                )
        beat_requirements: dict[str, set[str]] = {}
        for scene in story.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for beat in scene.get("beats") or []:
                if not isinstance(beat, dict) or not beat.get("beat_id"):
                    continue
                beat_id = str(beat["beat_id"])
                beat_requirements[beat_id] = set(
                    split_ids(str(beat.get("delivery_requirement_ids") or ""))
                    if isinstance(beat.get("delivery_requirement_ids"), str)
                    else [str(item) for item in (beat.get("delivery_requirement_ids") or [])]
                )
                if beat_id in must_beat_ids and not beat_requirements[beat_id]:
                    errors.append(f"story beat {beat_id}: missing delivery_requirement_ids")
        unknown = sorted(represented_screen_units - beat_ids)
        missing = sorted(must_beat_ids - represented_screen_units)
        if unknown:
            errors.append(f"adaptation matrix has unknown screen_unit IDs {unknown}")
        if missing:
            errors.append(f"adaptation matrix does not trace must beats {missing}")
        missing_requirements = sorted(story_requirement_ids - represented_requirements)
        unknown_requirements = sorted(represented_requirements - story_requirement_ids)
        if missing_requirements:
            errors.append(
                f"adaptation matrix does not trace delivery requirements {missing_requirements}"
            )
        if unknown_requirements:
            errors.append(
                f"adaptation matrix has unknown delivery requirements {unknown_requirements}"
            )
        for row_index, (screen_units, row_requirements) in enumerate(row_mappings, start=2):
            allowed = {
                requirement_id
                for beat_id in screen_units
                for requirement_id in beat_requirements.get(beat_id, set())
            }
            unrelated = sorted(row_requirements - allowed)
            if unrelated:
                errors.append(
                    f"adaptation-matrix.csv row {row_index}: delivery requirements "
                    f"not owned by mapped screen units {unrelated}"
                )
        for beat_id in sorted(must_beat_ids & beat_ids):
            mapped = {
                requirement_id
                for screen_units, row_requirements in row_mappings
                if beat_id in screen_units
                for requirement_id in row_requirements
                if requirement_id in beat_requirements.get(beat_id, set())
            }
            expected = beat_requirements.get(beat_id, set())
            if mapped != expected:
                errors.append(
                    f"adaptation matrix delivery requirements for {beat_id} "
                    f"{sorted(mapped)} != story beat {sorted(expected)}"
                )

    result = die_on_errors(errors)
    if not result:
        print(
            f"OK: {len(rows)} adaptation rows; "
            f"screen_units={len(represented_screen_units)} "
            f"delivery_requirements={len(represented_requirements)} "
            f"invent_ids={len(invent_ids)}"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
