#!/usr/bin/env python3
"""Run deterministic cross-file gates for a canonical AI video package."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from ai_video_common import (
    die_on_errors,
    is_sha256,
    load_csv,
    load_json,
    load_yaml,
    parse_list,
    require_fields,
)


PACKAGE_FILES = {
    "brief": ("ai_video_brief.yaml", "ai-video-brief.yaml"),
    "story": ("story_and_scene_map.yaml", "story-and-scene-map.yaml"),
    "director": ("director_intent.yaml", "director-intent.yaml"),
    "visual": ("visual_bible.yaml", "visual-bible.yaml"),
    "references": ("reference_asset_manifest.yaml", "reference-asset-manifest.yaml"),
    "clips": ("clip_plan.csv", "clip-plan.csv"),
    "prompts": ("generation_prompt_pack.json", "generation-prompt-pack.json"),
    "continuity": ("continuity_state.json", "continuity-state.json"),
    "generation_log": ("generation_log.jsonl", "generation-log.jsonl"),
    "clip_qc": ("clip_qc_report.yaml", "clip-qc-report.yaml"),
    "edit": ("edit_plan.yaml", "edit-plan.yaml"),
    "sound": ("sound_cue_sheet.csv", "sound-cue-sheet.csv"),
    "final_qc": ("final_film_qc.yaml", "final-film-qc.yaml"),
}

ADAPTATION_FILES = ("adaptation_matrix.csv", "adaptation-matrix.csv")
PLACEHOLDER_TEXT = {
    "前一状态",
    "状态变化",
    "信息/动作触发",
    "按clip描述",
    "按 clip 描述",
    "前世或今生",
    "adult",
    "attention",
    "进入状态由clip plan定义",
    "完成该beat并保持退出状态",
    "按visual bible状态",
    "planned",
    "sequence-time",
    "neutral soft key",
}


def is_placeholder(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return not text or text in {item.lower() for item in PLACEHOLDER_TEXT}


def resolve_package_file(package: Path, key: str) -> Path | None:
    """Resolve the canonical underscore filename, with a legacy hyphen alias."""
    for name in PACKAGE_FILES[key]:
        candidate = package / name
        if candidate.is_file():
            return candidate
    return None


def load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"generation-log.jsonl:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"generation-log.jsonl:{line_number}: record must be object")
            continue
        records.append(value)
    return records


def item_ids(
    items: Any,
    id_field: str,
    label: str,
    errors: list[str],
) -> set[str]:
    if not isinstance(items, list):
        errors.append(f"{label}: must be a list")
        return set()
    result: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get(id_field):
            errors.append(f"{label}[{index}]: missing {id_field}")
            continue
        value = str(item[id_field])
        if value in result:
            errors.append(f"{label}[{index}]: duplicate {id_field} {value}")
        result.add(value)
    return result


def compare_sets(
    actual: set[str],
    expected: set[str],
    label: str,
    errors: list[str],
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"{label}: missing IDs {missing}")
    if extra:
        errors.append(f"{label}: unknown IDs {extra}")


def run(script_dir: Path, script: str, arguments: list[str], errors: list[str]) -> None:
    command = [sys.executable, str(script_dir / script), *arguments]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode:
        detail = result.stderr.strip() or f"exit {result.returncode}"
        errors.append(f"{script}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--adapters", type=Path, required=True)
    args = parser.parse_args()
    package = args.package_dir
    errors: list[str] = []
    paths: dict[str, Path] = {}
    for key, names in PACKAGE_FILES.items():
        resolved = resolve_package_file(package, key)
        if resolved is None:
            errors.append(f"missing required file {names[0]}")
        else:
            paths[key] = resolved
    if errors:
        return die_on_errors(errors)
    adapter_data = load_json(args.adapters)
    adapter_by_id = {
        str(item.get("id")): item
        for item in (adapter_data.get("adapters") or [])
        if isinstance(item, dict) and item.get("id")
    }
    adapter_ids = {
        str(item.get("id"))
        for item in (adapter_data.get("adapters") or [])
        if isinstance(item, dict) and item.get("id")
    }
    brief = load_yaml(paths["brief"])
    require_fields(brief, ["id", "version", "owner_role", "status", "rights_status", "constraints"], "brief", errors)
    runtime = (brief.get("constraints") or {}).get("runtime_target_sec")
    is_novel = brief.get("format") == "novel-adaptation"
    if not isinstance(runtime, (int, float)) or runtime <= 0:
        errors.append("brief: constraints.runtime_target_sec must be positive")
    story = load_yaml(paths["story"])
    require_fields(
        story,
        ["id", "version", "status", "scenes", "must_beat_ids", "runtime_reconciliation"],
        "story-and-scene-map",
        errors,
    )
    scene_ids: set[str] = set()
    scene_locations: dict[str, str] = {}
    scene_beat_ids: dict[str, set[str]] = {}
    scene_runtime_budgets: dict[str, float] = {}
    beat_ids: set[str] = set()
    for scene_index, scene in enumerate(story.get("scenes") or []):
        if not isinstance(scene, dict) or not scene.get("scene_id"):
            errors.append(f"story-and-scene-map.scenes[{scene_index}]: missing scene_id")
            continue
        scene_id = str(scene["scene_id"])
        if scene_id in scene_ids:
            errors.append(f"story-and-scene-map: duplicate scene_id {scene_id}")
        scene_ids.add(scene_id)
        scene_locations[scene_id] = str(scene.get("location_id", ""))
        scene_beat_ids[scene_id] = set()
        try:
            scene_runtime_budgets[scene_id] = float(scene.get("runtime_budget_sec", 0))
        except (TypeError, ValueError):
            scene_runtime_budgets[scene_id] = 0.0
        for beat_index, beat in enumerate(scene.get("beats") or []):
            if not isinstance(beat, dict) or not beat.get("beat_id"):
                errors.append(
                    f"story-and-scene-map.scenes[{scene_index}].beats[{beat_index}]: missing beat_id"
                )
                continue
            beat_id = str(beat["beat_id"])
            if beat_id in beat_ids:
                errors.append(f"story-and-scene-map: duplicate beat_id {beat_id}")
            beat_ids.add(beat_id)
            scene_beat_ids[scene_id].add(beat_id)
    must_beat_ids = set(parse_list(story.get("must_beat_ids")))
    unknown_must = sorted(must_beat_ids - beat_ids)
    if unknown_must:
        errors.append(f"story-and-scene-map: unknown must_beat_ids {unknown_must}")
    delivery_requirement_ids: set[str] = set()
    delivery_requirements_by_id: dict[str, dict[str, Any]] = {}
    beat_delivery_requirements: dict[str, set[str]] = {}
    if is_novel:
        requirements = story.get("delivery_requirements")
        if not isinstance(requirements, list) or not requirements:
            errors.append(
                "story-and-scene-map: novel adaptation requires delivery_requirements"
            )
            requirements = []
        for index, requirement in enumerate(requirements):
            label = f"story-and-scene-map.delivery_requirements[{index}]"
            if not isinstance(requirement, dict):
                errors.append(f"{label}: must be object")
                continue
            require_fields(
                requirement,
                [
                    "requirement_id",
                    "content",
                    "exactness",
                    "approved_carriers",
                    "required_character_ids",
                    "visible_character_ids",
                    "required_prop_ids",
                    "location_ids",
                ],
                label,
                errors,
                empty_list_ok_fields={
                    "required_character_ids",
                    "visible_character_ids",
                    "required_prop_ids",
                },
            )
            requirement_id = str(requirement.get("requirement_id", ""))
            if requirement_id in delivery_requirement_ids:
                errors.append(f"{label}: duplicate requirement_id {requirement_id}")
            delivery_requirement_ids.add(requirement_id)
            delivery_requirements_by_id[requirement_id] = requirement
        for scene in story.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for beat in scene.get("beats") or []:
                if not isinstance(beat, dict) or not beat.get("beat_id"):
                    continue
                beat_id = str(beat["beat_id"])
                requirement_ids = set(parse_list(beat.get("delivery_requirement_ids")))
                beat_delivery_requirements[beat_id] = requirement_ids
                unknown = sorted(requirement_ids - delivery_requirement_ids)
                if unknown:
                    errors.append(
                        f"story-and-scene-map beat {beat_id}: "
                        f"unknown delivery_requirement_ids {unknown}"
                    )
                if beat_id in must_beat_ids and not requirement_ids:
                    errors.append(
                        f"story-and-scene-map beat {beat_id}: "
                        "must beat requires delivery_requirement_ids"
                    )
                narrative_function = str(beat.get("narrative_function", "")).lower()
                if "hook" in narrative_function and len(requirement_ids) != 1:
                    errors.append(
                        f"story-and-scene-map beat {beat_id}: hook beat must own "
                        "exactly one independent delivery requirement"
                    )
        for scene_index, scene in enumerate(story.get("scenes") or []):
            if not isinstance(scene, dict):
                continue
            for field in ("entry_state", "objective", "obstacle", "turn", "exit_state"):
                if is_placeholder(scene.get(field)):
                    errors.append(
                        f"story-and-scene-map.scenes[{scene_index}].{field}: "
                        "must be a concrete state/action, not a placeholder"
                    )
            for beat_index, beat in enumerate(scene.get("beats") or []):
                if not isinstance(beat, dict):
                    continue
                for field in ("visible_start", "trigger", "character_tactic", "visible_end"):
                    if is_placeholder(beat.get(field)):
                        errors.append(
                            f"story-and-scene-map.scenes[{scene_index}].beats[{beat_index}].{field}: "
                            "must be concrete, not a placeholder"
                        )
        requirement_beat_counts: dict[str, int] = {}
        for requirement_ids in beat_delivery_requirements.values():
            for requirement_id in requirement_ids:
                requirement_beat_counts[requirement_id] = (
                    requirement_beat_counts.get(requirement_id, 0) + 1
                )
        for requirement_id, count in sorted(requirement_beat_counts.items()):
            if count > 3:
                errors.append(
                    f"story delivery requirement {requirement_id}: spans {count} beats; "
                    "split independent facts, choices, results, or hooks"
                )
    scene_budget_sum = 0.0
    for scene in story.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        try:
            scene_budget_sum += float(scene.get("runtime_budget_sec", 0))
        except (TypeError, ValueError):
            errors.append("story-and-scene-map: scene runtime_budget_sec must be numeric")
    reconciliation = story.get("runtime_reconciliation") or {}
    declared_scene_sum = reconciliation.get("scene_budget_sum_sec")
    try:
        declared_scene_sum = float(declared_scene_sum)
    except (TypeError, ValueError):
        errors.append("story-and-scene-map: runtime_reconciliation.scene_budget_sum_sec must be numeric")
        declared_scene_sum = None
    if declared_scene_sum is not None and abs(scene_budget_sum - declared_scene_sum) > 0.01:
        errors.append(
            "story-and-scene-map: scene runtime budget sum "
            f"{scene_budget_sum} != declared {declared_scene_sum}"
        )
    if isinstance(runtime, (int, float)) and abs(scene_budget_sum - float(runtime)) > 0.01:
        errors.append(
            f"story-and-scene-map: scene runtime budget sum {scene_budget_sum} != runtime {runtime}"
        )
    director = load_yaml(paths["director"])
    require_fields(director, ["id", "version", "status", "scenes"], "director-intent", errors)
    director_scene_ids = item_ids(director.get("scenes"), "scene_id", "director-intent.scenes", errors)
    compare_sets(director_scene_ids, scene_ids, "director-intent.scenes", errors)
    if is_novel:
        director_performance_beats: set[str] = set()
        director_blocking_beats: set[str] = set()
        for index, scene in enumerate(director.get("scenes") or []):
            if not isinstance(scene, dict):
                continue
            if not scene.get("performance_beats"):
                errors.append(
                    f"director-intent.scenes[{index}]: novel adaptation requires performance_beats"
                )
            if not scene.get("blocking_beats"):
                errors.append(
                    f"director-intent.scenes[{index}]: novel adaptation requires blocking_beats"
                )
            director_performance_beats.update(
                str(item.get("beat_id"))
                for item in (scene.get("performance_beats") or [])
                if isinstance(item, dict) and item.get("beat_id")
            )
            director_blocking_beats.update(
                str(item.get("beat_id"))
                for item in (scene.get("blocking_beats") or [])
                if isinstance(item, dict) and item.get("beat_id")
            )
            scene_id = str(scene.get("scene_id", ""))
            expected_scene_beats = scene_beat_ids.get(scene_id, set())
            actual_performance = {
                str(item.get("beat_id"))
                for item in (scene.get("performance_beats") or [])
                if isinstance(item, dict) and item.get("beat_id")
            }
            actual_blocking = {
                str(item.get("beat_id"))
                for item in (scene.get("blocking_beats") or [])
                if isinstance(item, dict) and item.get("beat_id")
            }
            if actual_performance != expected_scene_beats:
                errors.append(
                    f"director-intent scene {scene_id}: performance beats "
                    f"{sorted(actual_performance)} != story scene beats "
                    f"{sorted(expected_scene_beats)}"
                )
            if actual_blocking != expected_scene_beats:
                errors.append(
                    f"director-intent scene {scene_id}: blocking beats "
                    f"{sorted(actual_blocking)} != story scene beats "
                    f"{sorted(expected_scene_beats)}"
                )
        missing_performance = sorted(must_beat_ids - director_performance_beats)
        missing_blocking = sorted(must_beat_ids - director_blocking_beats)
        if missing_performance:
            errors.append(
                f"director-intent: must beats missing performance design {missing_performance}"
            )
        if missing_blocking:
            errors.append(
                f"director-intent: must beats missing blocking design {missing_blocking}"
            )
    visual = load_yaml(paths["visual"])
    require_fields(
        visual,
        ["id", "version", "status", "aspect_ratio", "camera_language", "lighting_rules", "characters", "locations", "props"],
        "visual-bible",
        errors,
        empty_list_ok_fields={"characters", "locations", "props"},
    )
    character_ids = item_ids(visual.get("characters"), "character_id", "visual-bible.characters", errors)
    location_ids = item_ids(visual.get("locations"), "location_id", "visual-bible.locations", errors)
    prop_ids = item_ids(visual.get("props"), "prop_id", "visual-bible.props", errors)
    if is_novel:
        for requirement_id, requirement in delivery_requirements_by_id.items():
            label = f"story delivery requirement {requirement_id}"
            unknown_characters = sorted(
                (
                    set(parse_list(requirement.get("required_character_ids")))
                    | set(parse_list(requirement.get("visible_character_ids")))
                )
                - character_ids
            )
            unknown_props = sorted(
                set(parse_list(requirement.get("required_prop_ids"))) - prop_ids
            )
            unknown_locations = sorted(
                set(parse_list(requirement.get("location_ids"))) - location_ids
            )
            if unknown_characters:
                errors.append(f"{label}: unknown character IDs {unknown_characters}")
            if unknown_props:
                errors.append(f"{label}: unknown prop IDs {unknown_props}")
            if unknown_locations:
                errors.append(f"{label}: unknown location IDs {unknown_locations}")
            if not parse_list(requirement.get("location_ids")):
                errors.append(f"{label}: location_ids must not be empty")
    protagonist_id = str((story.get("protagonist") or {}).get("character_id", ""))
    if protagonist_id and protagonist_id not in character_ids:
        errors.append(
            f"story-and-scene-map: protagonist character_id {protagonist_id} "
            "is missing from visual-bible.characters"
        )
    character_state_ids: dict[str, set[str]] = {}
    if is_novel:
        for index, character in enumerate(visual.get("characters") or []):
            if not isinstance(character, dict) or not character.get("character_id"):
                continue
            character_id = str(character["character_id"])
            versions = character.get("state_versions")
            if not isinstance(versions, list) or not versions:
                errors.append(
                    f"visual-bible.characters[{index}]: novel adaptation "
                    "requires non-empty state_versions"
                )
                continue
            state_ids = item_ids(
                versions,
                "state_id",
                f"visual-bible.characters[{index}].state_versions",
                errors,
            )
            character_state_ids[character_id] = state_ids
            for version_index, version in enumerate(versions):
                if not isinstance(version, dict):
                    continue
                version_label = (
                    f"visual-bible.characters[{index}].state_versions[{version_index}]"
                )
                require_fields(
                    version,
                    ["state_id", "age", "time_context", "visible_state", "wardrobe_version"],
                    version_label,
                    errors,
                )
                for field in ("age", "time_context", "visible_state"):
                    if is_placeholder(version.get(field)):
                        errors.append(
                            f"{version_label}.{field}: must be concrete, not a placeholder"
                        )
        temporal_changes = story.get("temporal_state_changes")
        story_text = json.dumps(story, ensure_ascii=False).lower()
        temporal_markers = ("重生", "前世", "今生", "flashback", "years later", "years earlier")
        if temporal_changes is None:
            errors.append(
                "story-and-scene-map: novel adaptation requires temporal_state_changes field"
            )
            temporal_changes = []
        if any(marker in story_text for marker in temporal_markers) and not temporal_changes:
            errors.append(
                "story-and-scene-map: temporal/rebirth language requires explicit temporal_state_changes"
            )
        if not isinstance(temporal_changes, list):
            errors.append("story-and-scene-map.temporal_state_changes: must be a list")
            temporal_changes = []
        for index, change in enumerate(temporal_changes):
            label = f"story-and-scene-map.temporal_state_changes[{index}]"
            if not isinstance(change, dict):
                errors.append(f"{label}: must be object")
                continue
            require_fields(
                change,
                ["character_id", "from_state_id", "transition_beat_id", "to_state_id"],
                label,
                errors,
            )
            character_id = str(change.get("character_id", ""))
            if character_id not in character_ids:
                errors.append(f"{label}: unknown character_id {character_id}")
            known_states = character_state_ids.get(character_id, set())
            for field in ("from_state_id", "to_state_id"):
                state_id = str(change.get(field, ""))
                if state_id and state_id not in known_states:
                    errors.append(f"{label}: unknown {field} {state_id}")
            transition_beat_id = str(change.get("transition_beat_id", ""))
            if transition_beat_id not in beat_ids:
                errors.append(f"{label}: unknown transition_beat_id {transition_beat_id}")
            versions_by_id = {
                str(version.get("state_id")): version
                for character in (visual.get("characters") or [])
                if isinstance(character, dict)
                and str(character.get("character_id")) == character_id
                for version in (character.get("state_versions") or [])
                if isinstance(version, dict) and version.get("state_id")
            }
            from_version = versions_by_id.get(str(change.get("from_state_id", ""))) or {}
            to_version = versions_by_id.get(str(change.get("to_state_id", ""))) or {}
            differentiators = ("age", "time_context", "visible_state", "wardrobe_version")
            if from_version and to_version and all(
                str(from_version.get(field, "")) == str(to_version.get(field, ""))
                for field in differentiators
            ):
                errors.append(
                    f"{label}: from/to state versions have no concrete visible difference"
                )
    clip_rows = load_csv(paths["clips"])
    clip_ids = {str(row.get("clip_id", "")) for row in clip_rows if row.get("clip_id")}
    clip_rows_by_id = {
        str(row.get("clip_id")): row for row in clip_rows if row.get("clip_id")
    }
    covered_beats: set[str] = set()
    clip_contribution_by_scene: dict[str, float] = {}
    for index, row in enumerate(clip_rows, start=2):
        label = f"clip-plan.csv row {index}"
        scene_id = str(row.get("scene_id", ""))
        if scene_id not in scene_ids:
            errors.append(f"{label}: unknown scene_id {scene_id}")
        row_beats = set(parse_list(row.get("beat_ids")))
        covered_beats.update(row_beats)
        try:
            clip_contribution_by_scene[scene_id] = (
                clip_contribution_by_scene.get(scene_id, 0.0)
                + float(row.get("edit_contribution_sec", 0))
            )
        except (TypeError, ValueError):
            errors.append(f"{label}: edit_contribution_sec must be numeric")
        unknown_beats = sorted(row_beats - beat_ids)
        if unknown_beats:
            errors.append(f"{label}: unknown beat_ids {unknown_beats}")
        unknown_subjects = sorted(set(parse_list(row.get("subject_ids"))) - character_ids)
        if unknown_subjects:
            errors.append(f"{label}: unknown subject_ids {unknown_subjects}")
        location_id = str(row.get("location_id", ""))
        if location_id not in location_ids:
            errors.append(f"{label}: unknown location_id {location_id}")
        scene_location_id = scene_locations.get(scene_id, "")
        if scene_location_id and location_id != scene_location_id:
            errors.append(
                f"{label}: clip location {location_id} != scene location {scene_location_id}; "
                "split location changes into separate scenes"
            )
        unknown_props = sorted(set(parse_list(row.get("prop_ids"))) - prop_ids)
        if unknown_props:
            errors.append(f"{label}: unknown prop_ids {unknown_props}")
        entry_state_ref = str(row.get("entry_state_ref", ""))
        exit_state_ref = str(row.get("exit_state_ref", ""))
        camera_behavior = str(row.get("camera_behavior", ""))
        if entry_state_ref == camera_behavior or exit_state_ref == camera_behavior:
            errors.append(
                f"{label}: entry/exit state ref cannot equal camera_behavior"
            )
        generation_method = str(row.get("generation_method", "")).lower()
        expected_references = parse_list(row.get("reference_inputs_expected"))
        if generation_method in {
            "image_first_frame",
            "first_frame_image_to_video",
            "first-frame-i2v",
            "i2v_first_frame",
        } and not expected_references:
            errors.append(f"{label}: {generation_method} requires a planned first-frame reference")
    uncovered_must = sorted(must_beat_ids - covered_beats)
    if uncovered_must:
        errors.append(f"clip-plan.csv: must beats not covered {uncovered_must}")
    if is_novel:
        for scene_id in sorted(scene_ids):
            planned = clip_contribution_by_scene.get(scene_id, 0.0)
            budget = scene_runtime_budgets.get(scene_id, 0.0)
            if abs(planned - budget) > 0.01:
                errors.append(
                    f"clip-plan.csv: scene {scene_id} edit contribution {planned} "
                    f"!= story scene runtime budget {budget}"
                )

    reference_manifest = load_yaml(paths["references"])
    require_fields(
        reference_manifest,
        ["id", "version", "status", "assets"],
        "reference-asset-manifest",
        errors,
        empty_list_ok_fields={"assets"},
    )
    asset_ids = item_ids(
        reference_manifest.get("assets"),
        "asset_id",
        "reference-asset-manifest.assets",
        errors,
    )
    for index, asset in enumerate(reference_manifest.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        require_fields(
            asset,
            ["asset_id", "role", "status", "reference_transport"],
            f"reference-asset-manifest.assets[{index}]",
            errors,
        )
        digest = str(asset.get("sha256", ""))
        if digest and not is_sha256(digest):
            errors.append(f"reference-asset-manifest.assets[{index}]: invalid sha256")
        unattached_transports = {
            "none",
            "not_attached",
            "not_attached_descriptor_only",
            "planned",
            "expected_only",
        }
        requires_hash = asset.get("status") in {"approved", "attached", "production_ready"} or (
            asset.get("reference_transport") not in unattached_transports
        )
        if requires_hash and not digest:
            errors.append(
                f"reference-asset-manifest.assets[{index}]: attached/approved asset missing sha256"
            )

    prompt_pack = load_json(paths["prompts"])
    prompts = prompt_pack.get("prompts") if isinstance(prompt_pack, dict) else []
    prompt_clip_ids = item_ids(prompts, "clip_id", "generation-prompt-pack.prompts", errors)
    compare_sets(prompt_clip_ids, clip_ids, "generation-prompt-pack.prompts", errors)
    prompt_specs: dict[str, tuple[str, str]] = {}
    prompt_delivery_coverage: set[str] = set()
    for index, prompt in enumerate(prompts or []):
        if not isinstance(prompt, dict):
            continue
        rendered = str(prompt.get("rendered_prompt", ""))
        expected_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        if prompt.get("rendered_prompt_hash") != expected_hash:
            errors.append(
                f"generation-prompt-pack.prompts[{index}]: rendered_prompt_hash mismatch"
            )
        prompt_specs[str(prompt.get("clip_id", ""))] = (
            str(prompt.get("adapter_id", "")),
            str(prompt.get("rendered_prompt_hash", "")),
        )
        referenced_assets = set(parse_list(prompt.get("reference_inputs_expected"))) | set(
            parse_list(prompt.get("reference_inputs_attached"))
        )
        unknown_assets = sorted(referenced_assets - asset_ids)
        if unknown_assets:
            errors.append(
                f"generation-prompt-pack.prompts[{index}]: unknown reference assets {unknown_assets}"
            )
        adapter = adapter_by_id.get(str(prompt.get("adapter_id", ""))) or {}
        allowed_seconds = (adapter.get("limits") or {}).get("seconds")
        duration = (prompt.get("output_contract") or {}).get("duration_sec")
        if isinstance(allowed_seconds, list) and duration not in allowed_seconds:
            errors.append(
                f"generation-prompt-pack.prompts[{index}]: duration_sec {duration!r} "
                f"not allowed by adapter; expected one of {allowed_seconds}"
            )
        clip_row = clip_rows_by_id.get(str(prompt.get("clip_id", ""))) or {}
        try:
            clip_target = float(clip_row.get("target_duration_sec", ""))
            prompt_duration = float(duration)
            if abs(clip_target - prompt_duration) > 0.01:
                errors.append(
                    f"generation-prompt-pack.prompts[{index}]: output duration "
                    f"{prompt_duration} != clip target {clip_target}"
                )
        except (TypeError, ValueError):
            errors.append(
                f"generation-prompt-pack.prompts[{index}]: duration comparison requires numeric values"
            )
        if is_novel:
            label = f"generation-prompt-pack.prompts[{index}]"
            prompt_ir = prompt.get("prompt_ir") or {}
            if not isinstance(prompt_ir, dict):
                errors.append(f"{label}.prompt_ir: must be object")
                prompt_ir = {}
            visible_character_ids = set(
                parse_list(prompt_ir.get("visible_character_ids"))
            )
            narrative_character_ids = set(
                parse_list(prompt_ir.get("narrative_character_ids"))
            )
            narrative_prop_ids = set(
                parse_list(prompt_ir.get("narrative_prop_ids"))
            )
            if "visible_prop_ids" in prompt_ir:
                visible_prop_ids = set(
                    parse_list(prompt_ir.get("visible_prop_ids"))
                )
            else:
                visible_prop_ids = set(narrative_prop_ids)
            clip_subject_ids = set(parse_list(clip_row.get("subject_ids")))
            clip_prop_ids = set(parse_list(clip_row.get("prop_ids")))
            if visible_character_ids != clip_subject_ids:
                errors.append(
                    f"{label}: visible_character_ids {sorted(visible_character_ids)} "
                    f"!= clip subject_ids {sorted(clip_subject_ids)}"
                )
            if visible_prop_ids != clip_prop_ids:
                errors.append(
                    f"{label}: visible_prop_ids {sorted(visible_prop_ids)} "
                    f"!= clip prop_ids {sorted(clip_prop_ids)}"
                )
            unknown_visible = sorted(visible_character_ids - character_ids)
            if unknown_visible:
                errors.append(f"{label}: unknown visible_character_ids {unknown_visible}")
            unknown_narrative_characters = sorted(
                narrative_character_ids - character_ids
            )
            unknown_narrative_props = sorted(narrative_prop_ids - prop_ids)
            if unknown_narrative_characters:
                errors.append(
                    f"{label}: unknown narrative_character_ids "
                    f"{unknown_narrative_characters}"
                )
            if unknown_narrative_props:
                errors.append(
                    f"{label}: unknown narrative_prop_ids {unknown_narrative_props}"
                )
            if not visible_character_ids.issubset(narrative_character_ids):
                errors.append(
                    f"{label}: visible_character_ids must be included in "
                    "narrative_character_ids"
                )
            if not visible_prop_ids.issubset(narrative_prop_ids):
                errors.append(
                    f"{label}: visible_prop_ids must be included in narrative_prop_ids"
                )
            environment = prompt_ir.get("environment")
            if not isinstance(environment, dict) or not environment.get("location_id"):
                errors.append(
                    f"{label}.prompt_ir.environment: novel adaptation requires location_id"
                )
                prompt_location_id = ""
            else:
                prompt_location_id = str(environment.get("location_id"))
                if prompt_location_id not in location_ids:
                    errors.append(
                        f"{label}.prompt_ir.environment: unknown location_id "
                        f"{prompt_location_id}"
                    )
            clip_location_id = str(clip_row.get("location_id", ""))
            if prompt_location_id and prompt_location_id != clip_location_id:
                errors.append(
                    f"{label}: prompt location {prompt_location_id} "
                    f"!= clip location {clip_location_id}"
                )
            action_steps = prompt_ir.get("action_steps")
            if not isinstance(action_steps, list) or not action_steps:
                errors.append(f"{label}.prompt_ir: novel adaptation requires action_steps")
                action_steps = []
            if isinstance(duration, (int, float)) and float(duration) <= 8 and len(action_steps) > 3:
                errors.append(
                    f"{label}.prompt_ir: {duration}s clip has {len(action_steps)} "
                    "action_steps; split clips or provide a justified montage design"
                )
            step_ids: set[str] = set()
            for step_index, step in enumerate(action_steps):
                step_label = f"{label}.prompt_ir.action_steps[{step_index}]"
                if not isinstance(step, dict):
                    errors.append(f"{step_label}: must be object")
                    continue
                require_fields(
                    step,
                    ["step_id", "action", "end_state"],
                    step_label,
                    errors,
                )
                step_id = str(step.get("step_id", ""))
                if step_id in step_ids:
                    errors.append(f"{step_label}: duplicate step_id {step_id}")
                step_ids.add(step_id)
            action = prompt_ir.get("action")
            if not isinstance(action, dict):
                errors.append(
                    f"{label}.prompt_ir.action: novel adaptation requires start/motion/end object"
                )
            else:
                for field in ("start", "motion", "end"):
                    if is_placeholder(action.get(field)):
                        errors.append(
                            f"{label}.prompt_ir.action.{field}: "
                            "must be concrete, not a placeholder"
                        )
            prompt_requirement_ids = set(
                parse_list(prompt.get("delivery_requirement_ids"))
            )
            prompt_delivery_coverage.update(prompt_requirement_ids)
            unknown_requirements = sorted(
                prompt_requirement_ids - delivery_requirement_ids
            )
            if unknown_requirements:
                errors.append(
                    f"{label}: unknown delivery_requirement_ids {unknown_requirements}"
                )
            clip_requirement_ids = {
                requirement_id
                for beat_id in parse_list(clip_row.get("beat_ids"))
                for requirement_id in beat_delivery_requirements.get(beat_id, set())
            }
            unrelated_requirements = sorted(
                prompt_requirement_ids - clip_requirement_ids
            )
            if unrelated_requirements:
                errors.append(
                    f"{label}: delivery requirements not owned by clip beats "
                    f"{unrelated_requirements}"
                )
            if clip_requirement_ids and not prompt_requirement_ids:
                errors.append(
                    f"{label}: clip with delivery requirements requires "
                    "delivery_requirement_ids"
                )
            carriers = prompt.get("information_carriers")
            if not isinstance(carriers, list) or not carriers:
                errors.append(f"{label}: novel adaptation requires information_carriers")
                carriers = []
            carrier_requirement_ids: set[str] = set()
            for carrier_index, carrier in enumerate(carriers):
                carrier_label = f"{label}.information_carriers[{carrier_index}]"
                if not isinstance(carrier, dict):
                    errors.append(f"{carrier_label}: must be object")
                    continue
                require_fields(
                    carrier,
                    ["requirement_id", "carrier", "content", "fallback"],
                    carrier_label,
                    errors,
                    empty_list_ok_fields={"fallback"},
                )
                requirement_id = str(carrier.get("requirement_id", ""))
                if requirement_id in carrier_requirement_ids:
                    errors.append(
                        f"{carrier_label}: duplicate requirement_id {requirement_id}"
                    )
                carrier_requirement_ids.add(requirement_id)
                requirement = delivery_requirements_by_id.get(requirement_id) or {}
                approved_carriers = set(parse_list(requirement.get("approved_carriers")))
                carrier_name = str(carrier.get("carrier", ""))
                if approved_carriers and carrier_name not in approved_carriers:
                    errors.append(
                        f"{carrier_label}: carrier {carrier_name!r} not approved; "
                        f"expected one of {sorted(approved_carriers)}"
                    )
                required_characters = set(
                    parse_list(requirement.get("required_character_ids"))
                )
                required_visible = set(
                    parse_list(requirement.get("visible_character_ids"))
                )
                required_props = set(
                    parse_list(requirement.get("required_prop_ids"))
                )
                allowed_locations = set(
                    parse_list(requirement.get("location_ids"))
                )
                missing_narrative_characters = sorted(
                    required_characters - narrative_character_ids
                )
                missing_visible = sorted(required_visible - visible_character_ids)
                missing_props = sorted(required_props - narrative_prop_ids)
                if missing_narrative_characters:
                    errors.append(
                        f"{carrier_label}: required narrative characters missing "
                        f"{missing_narrative_characters}"
                    )
                if missing_visible:
                    errors.append(
                        f"{carrier_label}: required visible characters missing "
                        f"{missing_visible}"
                    )
                if missing_props:
                    errors.append(
                        f"{carrier_label}: required narrative props missing "
                        f"{missing_props}"
                    )
                if prompt_location_id and allowed_locations and prompt_location_id not in allowed_locations:
                    errors.append(
                        f"{carrier_label}: prompt location {prompt_location_id} "
                        f"not allowed by requirement {sorted(allowed_locations)}"
                    )
            if carrier_requirement_ids != prompt_requirement_ids:
                errors.append(
                    f"{label}: information carrier requirements "
                    f"{sorted(carrier_requirement_ids)} != prompt requirements "
                    f"{sorted(prompt_requirement_ids)}"
                )
        if str(adapter.get("status", "")).startswith("deprecated"):
            if not prompt.get("deprecation_warning"):
                errors.append(
                    f"generation-prompt-pack.prompts[{index}]: deprecated adapter requires deprecation_warning"
                )
            if not prompt.get("migration_fallback"):
                errors.append(
                    f"generation-prompt-pack.prompts[{index}]: deprecated adapter requires migration_fallback"
                )
    if is_novel:
        missing_prompt_requirements = sorted(
            delivery_requirement_ids - prompt_delivery_coverage
        )
        if missing_prompt_requirements:
            errors.append(
                "generation-prompt-pack: delivery requirements not carried by prompts "
                f"{missing_prompt_requirements}"
            )

    continuity = load_json(paths["continuity"])
    continuity_clip_ids = item_ids(
        continuity.get("clips") if isinstance(continuity, dict) else None,
        "clip_id",
        "continuity-state.clips",
        errors,
    )
    compare_sets(continuity_clip_ids, clip_ids, "continuity-state.clips", errors)
    for index, continuity_clip in enumerate(continuity.get("clips") or []):
        if not isinstance(continuity_clip, dict):
            continue
        continuity_clip_id = str(continuity_clip.get("clip_id", ""))
        continuity_clip_row = clip_rows_by_id.get(continuity_clip_id) or {}
        expected_clip_characters = set(
            parse_list(continuity_clip_row.get("subject_ids"))
        )
        expected_clip_props = set(parse_list(continuity_clip_row.get("prop_ids")))
        for state_name in ("entry", "exit"):
            state = continuity_clip.get(state_name) or {}
            state_characters = set((state.get("characters") or {}).keys())
            state_props = set((state.get("props") or {}).keys())
            unknown_characters = sorted(state_characters - character_ids)
            unknown_state_props = sorted(state_props - prop_ids)
            environment = state.get("environment") or {}
            state_location = environment.get("location") or environment.get("location_id")
            if unknown_characters:
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: "
                    f"unknown characters {unknown_characters}"
                )
            if unknown_state_props:
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: "
                    f"unknown props {unknown_state_props}"
                )
            if is_novel and not expected_clip_characters.issubset(state_characters):
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: characters "
                    f"{sorted(state_characters)} do not include clip subject_ids "
                    f"{sorted(expected_clip_characters)}"
                )
            if is_novel and not expected_clip_props.issubset(state_props):
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: props "
                    f"{sorted(state_props)} do not include clip prop_ids "
                    f"{sorted(expected_clip_props)}"
                )
            if is_novel:
                for character_id, character_state in (state.get("characters") or {}).items():
                    if not isinstance(character_state, dict):
                        continue
                    state_id = character_state.get("state_id")
                    if state_id and str(state_id) not in character_state_ids.get(
                        str(character_id), set()
                    ):
                        errors.append(
                            f"continuity-state.clips[{index}].{state_name}.{character_id}: "
                            f"unknown state_id {state_id}"
                        )
                for prop_id, prop_state in (state.get("props") or {}).items():
                    if isinstance(prop_state, dict) and "state" in prop_state and is_placeholder(
                        prop_state.get("state")
                    ):
                        errors.append(
                            f"continuity-state.clips[{index}].{state_name}.{prop_id}: "
                            "prop state must be concrete, not a placeholder"
                        )
                for field in ("time", "light"):
                    if field in environment and is_placeholder(environment.get(field)):
                        errors.append(
                            f"continuity-state.clips[{index}].{state_name}.environment.{field}: "
                            "must be concrete, not a placeholder"
                        )
            if state_location and str(state_location) not in location_ids:
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: "
                    f"unknown location {state_location}"
                )
            planned_location = str(
                (clip_rows_by_id.get(str(continuity_clip.get("clip_id"))) or {}).get(
                    "location_id", ""
                )
            )
            if state_location and planned_location and str(state_location) != planned_location:
                errors.append(
                    f"continuity-state.clips[{index}].{state_name}: location "
                    f"{state_location} != clip-plan location {planned_location}"
                )

    generation_records = load_jsonl(paths["generation_log"], errors)
    logged_clip_ids: set[str] = set()
    run_ids: set[str] = set()
    current_prompt_linked_clip_ids: set[str] = set()
    for index, record in enumerate(generation_records):
        label = f"generation-log.jsonl record {index + 1}"
        require_fields(
            record,
            ["run_id", "clip_id", "attempt", "primary_variable", "adapter_id", "status"],
            label,
            errors,
        )
        run_id = str(record.get("run_id", ""))
        clip_id = str(record.get("clip_id", ""))
        if run_id in run_ids:
            errors.append(f"{label}: duplicate run_id {run_id}")
        run_ids.add(run_id)
        logged_clip_ids.add(clip_id)
        if clip_id not in clip_ids:
            errors.append(f"{label}: unknown clip_id {clip_id}")
        adapter_id = str(record.get("adapter_id", ""))
        prompt_hash = str(record.get("prompt_hash", ""))
        if adapter_id not in adapter_ids:
            errors.append(f"{label}: unknown adapter_id {adapter_id}")
        if not is_sha256(prompt_hash):
            errors.append(f"{label}: prompt_hash must be SHA-256")
        if prompt_specs.get(clip_id) == (adapter_id, prompt_hash):
            current_prompt_linked_clip_ids.add(clip_id)
        attempt = record.get("attempt")
        if not isinstance(attempt, int) or attempt < 1:
            errors.append(f"{label}: attempt must be a positive integer")
        baseline = record.get("baseline_run_id")
        if attempt == 1 and baseline not in (None, ""):
            errors.append(f"{label}: first attempt must not have baseline_run_id")
        if isinstance(attempt, int) and attempt > 1 and not baseline:
            errors.append(f"{label}: later attempt requires baseline_run_id")
        if record.get("status") in {"planned", "pending", "diagnostic_preview"}:
            if record.get("output_uri") or record.get("output_hash") or record.get("actual"):
                errors.append(
                    f"{label}: non-generated status cannot claim output_uri, "
                    "output_hash, or actual output measurements"
                )
    compare_sets(logged_clip_ids, clip_ids, "generation-log.jsonl", errors)
    compare_sets(
        current_prompt_linked_clip_ids,
        clip_ids,
        "generation-log current prompt linkage",
        errors,
    )

    clip_qc = load_yaml(paths["clip_qc"])
    qc_clip_ids = item_ids(clip_qc.get("clips"), "clip_id", "clip-qc-report.clips", errors)
    compare_sets(qc_clip_ids, clip_ids, "clip-qc-report.clips", errors)
    for index, item in enumerate(clip_qc.get("clips") or []):
        if not isinstance(item, dict):
            continue
        require_fields(
            item,
            ["clip_id", "status"],
            f"clip-qc-report.clips[{index}]",
            errors,
        )

    edit_plan = load_yaml(paths["edit"])
    timeline = edit_plan.get("timeline") if isinstance(edit_plan, dict) else None
    if not isinstance(timeline, list) or not timeline:
        errors.append("edit-plan.timeline: must be a non-empty list")
    else:
        edit_total = 0.0
        edit_clip_ids: set[str] = set()
        edit_delivery_coverage: set[str] = set()
        for index, item in enumerate(timeline):
            label = f"edit-plan.timeline[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label}: must be object")
                continue
            require_fields(
                item,
                ["clip_id", "contribution_sec", "must_beat_ids"],
                label,
                errors,
                empty_list_ok_fields={"must_beat_ids"},
            )
            clip_id = str(item.get("clip_id", ""))
            edit_clip_ids.add(clip_id)
            if clip_id not in clip_ids:
                errors.append(f"{label}: unknown clip_id {clip_id}")
            if is_novel:
                item_requirement_ids = set(
                    parse_list(item.get("delivery_requirement_ids"))
                )
                edit_delivery_coverage.update(item_requirement_ids)
                unknown_requirements = sorted(
                    item_requirement_ids - delivery_requirement_ids
                )
                if unknown_requirements:
                    errors.append(
                        f"{label}: unknown delivery_requirement_ids {unknown_requirements}"
                    )
                clip_row = clip_rows_by_id.get(clip_id) or {}
                clip_requirement_ids = {
                    requirement_id
                    for beat_id in parse_list(clip_row.get("beat_ids"))
                    for requirement_id in beat_delivery_requirements.get(beat_id, set())
                }
                unrelated = sorted(item_requirement_ids - clip_requirement_ids)
                if unrelated:
                    errors.append(
                        f"{label}: delivery requirements not owned by clip beats {unrelated}"
                    )
            try:
                edit_total += float(item.get("contribution_sec", 0))
            except (TypeError, ValueError):
                errors.append(f"{label}: contribution_sec must be numeric")
        if isinstance(runtime, (int, float)) and abs(edit_total - float(runtime)) > 0.01:
            errors.append(f"edit-plan.timeline contribution {edit_total} != runtime {runtime}")
        missing_edit_must = sorted(must_beat_ids - {
            beat
            for item in timeline if isinstance(item, dict)
            for beat in parse_list(item.get("must_beat_ids"))
        })
        if missing_edit_must:
            errors.append(f"edit-plan.timeline: must beats not used {missing_edit_must}")
        if is_novel:
            missing_edit_requirements = sorted(
                delivery_requirement_ids - edit_delivery_coverage
            )
            if missing_edit_requirements:
                errors.append(
                    "edit-plan.timeline: delivery requirements not used "
                    f"{missing_edit_requirements}"
                )

    sound_rows = load_csv(paths["sound"])
    for index, row in enumerate(sound_rows, start=2):
        if None in row:
            errors.append(f"sound-cue-sheet.csv row {index}: extra unescaped CSV fields")
        if any(value is None for key, value in row.items() if key is not None):
            errors.append(f"sound-cue-sheet.csv row {index}: missing trailing CSV fields")
        if not str(row.get("status") or "").strip():
            errors.append(f"sound-cue-sheet.csv row {index}: missing status")
        clip_id = str(row.get("clip_id", ""))
        if clip_id and clip_id not in clip_ids:
            errors.append(f"sound-cue-sheet.csv row {index}: unknown clip_id {clip_id}")
    scripts = Path(__file__).resolve().parent
    adaptation_path = next(
        (package / name for name in ADAPTATION_FILES if (package / name).is_file()),
        None,
    )
    if is_novel and adaptation_path is None:
        errors.append("missing required file adaptation_matrix.csv for novel-adaptation")
    if adaptation_path is not None:
        run(
            scripts,
            "validate_adaptation_matrix.py",
            [str(adaptation_path), "--story-map", str(paths["story"])],
            errors,
        )
    run(scripts, "validate_model_adapters.py", [str(args.adapters)], errors)
    if isinstance(runtime, (int, float)):
        run(
            scripts,
            "validate_clip_plan.py",
            [str(paths["clips"]), "--runtime-target", str(runtime)],
            errors,
        )
    run(
        scripts,
        "validate_prompt_pack.py",
        [str(paths["prompts"]), "--adapters", str(args.adapters)],
        errors,
    )
    run(scripts, "validate_continuity_state.py", [str(paths["continuity"])], errors)
    final_qc = load_yaml(paths["final_qc"])
    if final_qc.get("final_result") == "pass":
        for field in ["runtime", "must_beat_coverage", "continuity", "dialogue_lipsync", "sound", "rights_and_provenance"]:
            if final_qc.get(field) != "pass":
                errors.append(f"final-film-qc: final_result pass while {field} != pass")
        if final_qc.get("blocking_conflicts"):
            errors.append("final-film-qc: final_result pass with blocking_conflicts")
        approved_qc = {
            str(item.get("clip_id"))
            for item in (clip_qc.get("clips") or [])
            if isinstance(item, dict) and item.get("status") == "approved"
        }
        compare_sets(approved_qc, clip_ids, "final-film-qc approved clips", errors)
        output_logged = {
            str(item.get("clip_id"))
            for item in generation_records
            if item.get("status") == "approved" and is_sha256(str(item.get("output_hash", "")))
        }
        compare_sets(output_logged, clip_ids, "final-film-qc generation provenance", errors)
    result = die_on_errors(errors)
    if not result:
        print(f"OK: AI video package {package}")
    return result


if __name__ == "__main__":
    try:
        exit_code = main()
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR: invalid package data: {exc}")
        exit_code = 1
    raise SystemExit(exit_code)
