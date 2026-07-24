#!/usr/bin/env python3
"""Static continuity checks for JSON scene cards and shot lists.

Expected input is either {"scenes": [...], "shots": [...]} or a scene array.
The linter reports suspicious state transitions; it does not rewrite creative
choices and cannot infer continuity from prose that has no structured fields.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return {"scenes": value, "shots": []}
    if not isinstance(value, dict):
        raise ValueError("input must be an object or scene array")
    return value


def as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {part.strip() for part in value.split("|") if part.strip()}
    if isinstance(value, list):
        return {str(item) for item in value}
    if isinstance(value, dict):
        return {str(key) for key, present in value.items() if present}
    return set()


def check_scenes(scenes: list[Any]) -> tuple[list[str], set[str], set[str]]:
    errors: list[str] = []
    scene_ids: set[str] = set()
    beat_ids: set[str] = set()
    active_props: set[str] = set()
    active_characters: set[str] = set()
    previous_location: str | None = None
    for index, scene in enumerate(scenes):
        label = f"scene[{index}]"
        if not isinstance(scene, dict):
            errors.append(f"{label}: expected an object")
            continue
        scene_id = str(scene.get("scene_id", ""))
        if not scene_id:
            errors.append(f"{label}: missing scene_id")
        elif scene_id in scene_ids:
            errors.append(f"{label}: duplicate scene_id {scene_id}")
        scene_ids.add(scene_id)
        location = str(scene.get("location_time", "")).strip()
        if not location:
            errors.append(f"{label}: missing location_time")
        if previous_location and scene.get("continuity_expected_location") and scene["continuity_expected_location"] != previous_location:
            errors.append(f"{label}: expected location {scene['continuity_expected_location']!r}, previous was {previous_location!r}")
        previous_location = location or previous_location
        characters = as_set(scene.get("characters"))
        if not characters and not scene.get("cast"):
            errors.append(f"{label}: characters/cast is empty")
        introduced_characters = as_set(scene.get("character_events", {}).get("introduced") if isinstance(scene.get("character_events"), dict) else None)
        removed_characters = as_set(scene.get("character_events", {}).get("removed") if isinstance(scene.get("character_events"), dict) else None)
        active_characters |= introduced_characters
        active_characters -= removed_characters
        for name in characters:
            if name in removed_characters:
                errors.append(f"{label}: character {name!r} is both present and removed")
        prop_events = scene.get("prop_events", {})
        if not isinstance(prop_events, dict):
            prop_events = {}
        active_props |= as_set(scene.get("props"))
        introduced = as_set(prop_events.get("introduced"))
        removed = as_set(prop_events.get("removed"))
        required = as_set(prop_events.get("required"))
        for prop in required:
            if prop not in active_props and prop not in introduced:
                errors.append(f"{label}: required prop {prop!r} has not been introduced")
        if introduced & removed:
            errors.append(f"{label}: prop introduced and removed in same scene: {sorted(introduced & removed)}")
        active_props |= introduced
        active_props -= removed
        beats = scene.get("beats", [])
        if isinstance(beats, list):
            for beat_index, beat in enumerate(beats):
                if not isinstance(beat, dict):
                    errors.append(f"{label}.beats[{beat_index}]: expected an object")
                    continue
                beat_id = str(beat.get("id", ""))
                if not beat_id:
                    errors.append(f"{label}.beats[{beat_index}]: missing id")
                elif beat_id in beat_ids:
                    errors.append(f"{label}: duplicate beat id {beat_id}")
                beat_ids.add(beat_id)
    return errors, scene_ids, beat_ids


def check_shots(
    shots: list[Any],
    scene_ids: set[str],
    beat_ids: set[str],
    require_all_beats_covered: bool,
) -> list[str]:
    errors: list[str] = []
    shot_ids: set[str] = set()
    covered_beat_ids: set[str] = set()
    for index, shot in enumerate(shots):
        label = f"shot[{index}]"
        if not isinstance(shot, dict):
            errors.append(f"{label}: expected an object")
            continue
        shot_id = str(shot.get("shot_id", ""))
        if not shot_id:
            errors.append(f"{label}: missing shot_id")
        elif shot_id in shot_ids:
            errors.append(f"{label}: duplicate shot_id {shot_id}")
        shot_ids.add(shot_id)
        scene_id = str(shot.get("scene_id", ""))
        if scene_id not in scene_ids:
            errors.append(f"{label}: unknown scene_id {scene_id!r}")
        shot_beats = as_set(shot.get("beat_id"))
        covered_beat_ids |= shot_beats
        for beat_id in shot_beats:
            if beat_id not in beat_ids:
                errors.append(f"{label}: unknown beat_id {beat_id!r}")
        for field in ("purpose", "framing", "fallback"):
            if not str(shot.get(field, "")).strip():
                errors.append(f"{label}: missing {field}")
    if require_all_beats_covered:
        for beat_id in sorted(beat_ids - covered_beat_ids):
            errors.append(f"beat has no shot coverage: {beat_id}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--require-all-beats-covered", action="store_true")
    args = parser.parse_args()
    try:
        data = load(args.path)
        scenes = data.get("scenes", [])
        shots = data.get("shots", [])
        if not isinstance(scenes, list) or not isinstance(shots, list):
            raise ValueError("scenes and shots must be arrays")
        errors, scene_ids, beat_ids = check_scenes(scenes)
        errors.extend(check_shots(shots, scene_ids, beat_ids, args.require_all_beats_covered))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path} ({len(scene_ids)} scenes, {len(beat_ids)} beats)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
