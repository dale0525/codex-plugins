#!/usr/bin/env python3
"""Shared stage gates for the staged AI-video package workflow."""

from __future__ import annotations

import csv
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from ai_video_common import load_json, load_yaml, parse_list, require_fields


FROZEN = {"frozen", "approved"}
REVIEW_ENTAILMENTS = {"preserved", "approved_change", "contradicted", "unsupported"}
PASS_ENTAILMENTS = {"preserved", "approved_change"}
REVIEW_STAGES = {"adaptation", "story", "director"}
LOCKED_CAMERA_TERMS = {"locked", "static", "fixed", "tripod locked"}
MOVING_CAMERA_TERMS = {"pan", "tilt", "dolly", "orbit", "truck", "crane", "handheld", "push", "pull"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_ref(data: dict[str, Any]) -> str:
    return f"{data.get('id', '')}@{data.get('version', '')}"


def flatten_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    paths: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.update(flatten_paths(child, child_prefix))
    elif isinstance(value, list):
        paths[prefix] = value
    elif prefix:
        paths[prefix] = value
    return paths


def require_frozen_header(data: Any, label: str, errors: list[str]) -> None:
    if not isinstance(data, dict):
        errors.append(f"{label}: must be an object")
        return
    require_fields(
        data,
        ["id", "version", "owner_role", "source_inputs", "status"],
        label,
        errors,
        empty_list_ok_fields={"source_inputs"},
    )
    if data.get("status") not in FROZEN:
        errors.append(f"{label}: status must be frozen or approved before the stage can pass")


def require_source_ref(
    child: dict[str, Any], parent: dict[str, Any], label: str, errors: list[str]
) -> None:
    expected = artifact_ref(parent)
    if expected not in parse_list(child.get("source_inputs")):
        errors.append(f"{label}: source_inputs must include exact frozen ref {expected}")


def validate_semantic_review(
    package: Path,
    stage: str,
    required_artifacts: list[str],
    required_coverage_ids: set[str],
    errors: list[str],
) -> None:
    path = package / "semantic_reviews.yaml"
    if not path.is_file():
        errors.append("semantic_reviews.yaml: missing independent semantic review artifact")
        return
    data = load_yaml(path)
    if not isinstance(data, dict):
        errors.append("semantic_reviews.yaml: must be an object")
        return
    require_fields(
        data,
        ["id", "version", "owner_role", "status", "reviews"],
        "semantic-reviews",
        errors,
    )
    if data.get("owner_role") != "semantic_reviewer":
        errors.append("semantic-reviews: owner_role must be semantic_reviewer")
    if data.get("status") not in FROZEN:
        errors.append("semantic-reviews: status must be frozen or approved")
    reviews = [
        review
        for review in (data.get("reviews") or [])
        if isinstance(review, dict) and review.get("stage") == stage
    ]
    if len(reviews) != 1:
        errors.append(f"semantic-reviews: expected exactly one {stage!r} review, got {len(reviews)}")
        return
    review = reviews[0]
    label = f"semantic-reviews.{stage}"
    require_fields(
        review,
        [
            "review_id",
            "reviewer_id",
            "independent_from_authors",
            "expected_answer_visible",
            "review_scope",
            "artifacts",
            "verdict",
            "claims",
        ],
        label,
        errors,
    )
    if review.get("independent_from_authors") is not True:
        errors.append(f"{label}: independent_from_authors must be true")
    if review.get("expected_answer_visible") is not False:
        errors.append(f"{label}: expected_answer_visible must be false")
    if review.get("review_scope") != "source_and_frozen_artifacts_only":
        errors.append(f"{label}: review_scope must be source_and_frozen_artifacts_only")
    if review.get("verdict") != "pass":
        errors.append(f"{label}: verdict must be pass")

    reviewed_hashes: dict[str, str] = {}
    for index, artifact in enumerate(review.get("artifacts") or []):
        artifact_label = f"{label}.artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{artifact_label}: must be an object")
            continue
        require_fields(artifact, ["path", "sha256"], artifact_label, errors)
        relative = str(artifact.get("path", ""))
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            errors.append(f"{artifact_label}: path must stay inside the package")
            continue
        reviewed_hashes[relative] = str(artifact.get("sha256", ""))
    for relative in required_artifacts:
        target = package / relative
        if not target.is_file():
            errors.append(f"{label}: reviewed artifact is missing: {relative}")
            continue
        expected_hash = file_sha256(target)
        if reviewed_hashes.get(relative) != expected_hash:
            errors.append(
                f"{label}: review hash for {relative} is stale or missing; "
                f"expected {expected_hash}"
            )

    claims = review.get("claims") or []
    if not isinstance(claims, list) or not claims:
        errors.append(f"{label}: claims must be a non-empty list")
        return
    seen_claims: set[str] = set()
    covered_ids: set[str] = set()
    for index, claim in enumerate(claims):
        claim_label = f"{label}.claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{claim_label}: must be an object")
            continue
        require_fields(
            claim,
            [
                "claim_id",
                "source_unit_ids",
                "screen_unit_ids",
                "delivery_requirement_ids",
                "source_claim",
                "proposed_screen_claim",
                "entailment",
                "source_location",
                "screen_location",
                "temporal_relation",
                "responsible_character_ids",
                "choice_result_polarity",
                "evidence",
                "verdict",
            ],
            claim_label,
            errors,
            empty_list_ok_fields={
                "source_unit_ids",
                "screen_unit_ids",
                "delivery_requirement_ids",
                "responsible_character_ids",
            },
        )
        claim_id = str(claim.get("claim_id", ""))
        if claim_id in seen_claims:
            errors.append(f"{claim_label}: duplicate claim_id {claim_id}")
        seen_claims.add(claim_id)
        entailment = str(claim.get("entailment", ""))
        if entailment not in REVIEW_ENTAILMENTS:
            errors.append(f"{claim_label}: invalid entailment {entailment!r}")
        if entailment not in PASS_ENTAILMENTS or claim.get("verdict") != "pass":
            errors.append(
                f"{claim_label}: contradicted, unsupported, or failed claims block freezing"
            )
        for field in ("source_unit_ids", "screen_unit_ids", "delivery_requirement_ids"):
            covered_ids.update(parse_list(claim.get(field)))
    missing_coverage = sorted(required_coverage_ids - covered_ids)
    if missing_coverage:
        errors.append(f"{label}: semantic claims do not cover IDs {missing_coverage}")


def validate_adaptation(package: Path) -> list[str]:
    errors: list[str] = []
    brief_path = package / "ai_video_brief.yaml"
    matrix_path = package / "adaptation_matrix.csv"
    if not brief_path.is_file():
        return ["ai_video_brief.yaml: missing"]
    brief = load_yaml(brief_path)
    require_frozen_header(brief, "brief", errors)
    if not matrix_path.is_file():
        errors.append("adaptation_matrix.csv: missing for adaptation stage")
    else:
        with matrix_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        required_ids = {
            item
            for row in rows
            for field in ("source_unit", "screen_unit", "delivery_requirement_ids")
            for item in parse_list(row.get(field))
        }
        validator = Path(__file__).with_name("validate_adaptation_matrix.py")
        result = subprocess.run(
            [sys.executable, str(validator), str(matrix_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            errors.extend(line.removeprefix("ERROR: ") for line in result.stdout.splitlines() if line)
        validate_semantic_review(
            package,
            "adaptation",
            ["ai_video_brief.yaml", "adaptation_matrix.csv"],
            required_ids,
            errors,
        )
    return errors


def collect_story(story: dict[str, Any], errors: list[str]) -> tuple[set[str], dict[str, set[str]]]:
    require_frozen_header(story, "story-and-scene-map", errors)
    require_fields(
        story,
        ["logline", "dramatic_question", "theme_question", "protagonist", "scenes", "must_beat_ids"],
        "story-and-scene-map",
        errors,
    )
    scene_ids: set[str] = set()
    scene_beats: dict[str, set[str]] = {}
    all_beats: set[str] = set()
    total = 0.0
    for scene_index, scene in enumerate(story.get("scenes") or []):
        label = f"story-and-scene-map.scenes[{scene_index}]"
        if not isinstance(scene, dict):
            errors.append(f"{label}: must be an object")
            continue
        require_fields(
            scene,
            ["scene_id", "runtime_budget_sec", "location_id", "time", "entry_state", "objective", "obstacle", "turn", "exit_state", "beats"],
            label,
            errors,
        )
        scene_id = str(scene.get("scene_id", ""))
        if scene_id in scene_ids:
            errors.append(f"{label}: duplicate scene_id {scene_id}")
        scene_ids.add(scene_id)
        scene_beats[scene_id] = set()
        try:
            total += float(scene.get("runtime_budget_sec", 0))
        except (TypeError, ValueError):
            errors.append(f"{label}: runtime_budget_sec must be numeric")
        if scene.get("entry_state") == scene.get("exit_state"):
            errors.append(f"{label}: entry_state and exit_state must differ")
        for beat_index, beat in enumerate(scene.get("beats") or []):
            beat_label = f"{label}.beats[{beat_index}]"
            if not isinstance(beat, dict):
                errors.append(f"{beat_label}: must be an object")
                continue
            require_fields(
                beat,
                ["beat_id", "narrative_function", "visible_start", "trigger", "character_tactic", "visible_end", "runtime_budget_sec", "fallback"],
                beat_label,
                errors,
            )
            beat_id = str(beat.get("beat_id", ""))
            if beat_id in all_beats:
                errors.append(f"{beat_label}: duplicate beat_id {beat_id}")
            all_beats.add(beat_id)
            scene_beats[scene_id].add(beat_id)
    runtime = (story.get("constraints") or {}).get("runtime_target_sec")
    try:
        runtime_value = float(runtime)
    except (TypeError, ValueError):
        errors.append("story-and-scene-map.constraints.runtime_target_sec must be numeric")
        runtime_value = total
    if abs(total - runtime_value) > 0.01:
        errors.append(f"story-and-scene-map: scene runtime total {total} != target {runtime_value}")
    must_beats = set(parse_list(story.get("must_beat_ids")))
    unknown = sorted(must_beats - all_beats)
    if unknown:
        errors.append(f"story-and-scene-map: unknown must_beat_ids {unknown}")
    return scene_ids, scene_beats


def validate_story(package: Path) -> list[str]:
    errors: list[str] = []
    brief_path = package / "ai_video_brief.yaml"
    story_path = package / "story_and_scene_map.yaml"
    if not brief_path.is_file() or not story_path.is_file():
        return ["story stage requires ai_video_brief.yaml and story_and_scene_map.yaml"]
    brief = load_yaml(brief_path)
    story = load_yaml(story_path)
    require_frozen_header(brief, "brief", errors)
    collect_story(story, errors)
    require_source_ref(story, brief, "story-and-scene-map", errors)
    brief_runtime = (brief.get("constraints") or {}).get("runtime_target_sec")
    story_runtime = (story.get("constraints") or {}).get("runtime_target_sec")
    if brief_runtime != story_runtime:
        errors.append("story-and-scene-map: runtime target must equal frozen brief")
    if brief.get("format") == "novel-adaptation":
        matrix = package / "adaptation_matrix.csv"
        if not matrix.is_file():
            errors.append("story stage: novel-adaptation requires adaptation_matrix.csv")
        else:
            validator = Path(__file__).with_name("validate_adaptation_matrix.py")
            result = subprocess.run(
                [sys.executable, str(validator), str(matrix), "--story-map", str(story_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                errors.extend(line.removeprefix("ERROR: ") for line in result.stdout.splitlines() if line)
        known_beats = {
            str(beat.get("beat_id"))
            for scene in (story.get("scenes") or [])
            if isinstance(scene, dict)
            for beat in (scene.get("beats") or [])
            if isinstance(beat, dict) and beat.get("beat_id")
        }
        temporal_changes = story.get("temporal_state_changes")
        if not isinstance(temporal_changes, list):
            errors.append("story-and-scene-map.temporal_state_changes: must be a list")
            temporal_changes = []
        for index, change in enumerate(temporal_changes):
            label = f"story-and-scene-map.temporal_state_changes[{index}]"
            if not isinstance(change, dict):
                errors.append(f"{label}: must be an object")
                continue
            require_fields(
                change,
                ["character_id", "from_state_id", "transition_beat_id", "to_state_id"],
                label,
                errors,
            )
            if str(change.get("transition_beat_id", "")) not in known_beats:
                errors.append(f"{label}: transition_beat_id must reference a story beat")
    required_review_ids = set(parse_list(story.get("must_beat_ids")))
    required_review_ids.update(
        str(item.get("requirement_id"))
        for item in (story.get("delivery_requirements") or [])
        if isinstance(item, dict) and item.get("requirement_id")
    )
    review_artifacts = ["ai_video_brief.yaml", "story_and_scene_map.yaml"]
    if brief.get("format") == "novel-adaptation":
        review_artifacts.insert(1, "adaptation_matrix.csv")
    validate_semantic_review(
        package,
        "story",
        review_artifacts,
        required_review_ids,
        errors,
    )
    return errors


def validate_clip_specs(
    story: dict[str, Any], director: dict[str, Any], visual: dict[str, Any], errors: list[str]
) -> list[dict[str, Any]]:
    scene_ids, story_scene_beats = collect_story(story, errors)
    characters = {
        str(item.get("character_id"))
        for item in (visual.get("characters") or [])
        if isinstance(item, dict) and item.get("character_id")
    }
    props = {
        str(item.get("prop_id"))
        for item in (visual.get("props") or [])
        if isinstance(item, dict) and item.get("prop_id")
    }
    locations = {
        str(item.get("location_id"))
        for item in (visual.get("locations") or [])
        if isinstance(item, dict) and item.get("location_id")
    }
    director_scenes = director.get("scenes") or []
    director_scene_ids = {
        str(item.get("scene_id"))
        for item in director_scenes
        if isinstance(item, dict) and item.get("scene_id")
    }
    if director_scene_ids != scene_ids:
        errors.append(
            f"director-intent: scene IDs {sorted(director_scene_ids)} != story {sorted(scene_ids)}"
        )
    all_specs: list[dict[str, Any]] = []
    seen_clips: set[str] = set()
    contribution_by_scene: dict[str, float] = {scene_id: 0.0 for scene_id in scene_ids}
    covered_beats: set[str] = set()
    for scene_index, scene in enumerate(director_scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id", ""))
        label = f"director-intent.scenes[{scene_index}]"
        require_fields(
            scene,
            ["audience_knowledge", "attention_path", "performance_beats", "blocking_beats", "coverage_strategy", "sound_intent", "clip_specs"],
            label,
            errors,
        )
        expected_beats = story_scene_beats.get(scene_id, set())
        for field in ("performance_beats", "blocking_beats"):
            actual = {
                str(item.get("beat_id"))
                for item in (scene.get(field) or [])
                if isinstance(item, dict) and item.get("beat_id")
            }
            if actual != expected_beats:
                errors.append(
                    f"{label}.{field}: beat IDs {sorted(actual)} != story scene {sorted(expected_beats)}"
                )
        for spec_index, spec in enumerate(scene.get("clip_specs") or []):
            spec_label = f"{label}.clip_specs[{spec_index}]"
            if not isinstance(spec, dict):
                errors.append(f"{spec_label}: must be an object")
                continue
            require_fields(
                spec,
                [
                    "clip_id", "beat_ids", "priority", "narrative_purpose",
                    "target_duration_sec", "edit_contribution_sec", "handle_in_sec",
                    "handle_out_sec", "visible_character_ids", "narrative_character_ids",
                    "visible_prop_ids", "narrative_prop_ids", "location_id", "entry_state",
                    "exit_state", "action", "attention_change", "camera", "lighting",
                    "temporal_constraints", "continuity_invariants", "negative_constraints",
                    "generation_method", "adapter_id", "reference_inputs_expected",
                    "generation_risks", "fallback", "sound",
                ],
                spec_label,
                errors,
                empty_list_ok_fields={
                    "visible_character_ids", "narrative_character_ids", "visible_prop_ids",
                    "narrative_prop_ids", "reference_inputs_expected", "negative_constraints",
                    "generation_risks",
                },
            )
            clip_id = str(spec.get("clip_id", ""))
            if clip_id in seen_clips:
                errors.append(f"{spec_label}: duplicate clip_id {clip_id}")
            seen_clips.add(clip_id)
            spec["scene_id"] = scene_id
            all_specs.append(spec)
            beat_ids = set(parse_list(spec.get("beat_ids")))
            if not beat_ids or not beat_ids.issubset(expected_beats):
                errors.append(f"{spec_label}: beat_ids must belong to scene {scene_id}")
            covered_beats.update(beat_ids)
            try:
                target = float(spec.get("target_duration_sec"))
                contribution = float(spec.get("edit_contribution_sec"))
                handles = float(spec.get("handle_in_sec")) + float(spec.get("handle_out_sec"))
                if abs(target - contribution - handles) > 0.01:
                    errors.append(
                        f"{spec_label}: target must equal edit contribution plus both handles"
                    )
                contribution_by_scene[scene_id] += contribution
            except (TypeError, ValueError):
                errors.append(f"{spec_label}: durations and handles must be numeric")
            visible_characters = set(parse_list(spec.get("visible_character_ids")))
            narrative_characters = set(parse_list(spec.get("narrative_character_ids")))
            visible_props = set(parse_list(spec.get("visible_prop_ids")))
            narrative_props = set(parse_list(spec.get("narrative_prop_ids")))
            if not visible_characters.issubset(narrative_characters):
                errors.append(f"{spec_label}: visible characters must be narrative characters")
            if not visible_props.issubset(narrative_props):
                errors.append(f"{spec_label}: visible props must be narrative props")
            if (narrative_characters - characters) or (narrative_props - props):
                errors.append(f"{spec_label}: references unknown narrative character or prop IDs")
            if str(spec.get("location_id", "")) not in locations:
                errors.append(f"{spec_label}: unknown location_id {spec.get('location_id')}")
            for state_name in ("entry_state", "exit_state"):
                state = spec.get(state_name) or {}
                if not isinstance(state, dict) or not state:
                    errors.append(f"{spec_label}.{state_name}: must be a meaningful state object")
                    continue
                state_characters = set((state.get("characters") or {}).keys())
                state_props = set((state.get("props") or {}).keys())
                if not visible_characters.issubset(state_characters):
                    errors.append(f"{spec_label}.{state_name}: missing visible characters")
                if not visible_props.issubset(state_props):
                    errors.append(f"{spec_label}.{state_name}: missing visible props")
                environment = state.get("environment") or {}
                state_location = environment.get("location_id") or environment.get("location")
                if str(state_location or "") != str(spec.get("location_id", "")):
                    errors.append(f"{spec_label}.{state_name}: location must equal clip location")
            action = spec.get("action") or {}
            require_fields(action, ["start", "motion", "end", "steps"], f"{spec_label}.action", errors)
            steps = action.get("steps") or []
            try:
                short_duration = float(spec.get("target_duration_sec") or 0) <= 8
            except (TypeError, ValueError):
                short_duration = False
            if short_duration and len(steps) > 3:
                errors.append(f"{spec_label}: clips of 8 seconds or less allow at most 3 action steps")
            camera = spec.get("camera") or {}
            require_fields(camera, ["framing", "behavior"], f"{spec_label}.camera", errors)
            camera_text = " ".join(str(value) for value in camera.values()).lower()
            if (
                any(term in camera_text for term in LOCKED_CAMERA_TERMS)
                and any(term in camera_text for term in MOVING_CAMERA_TERMS)
            ):
                errors.append(
                    f"{spec_label}.camera: contains locked/static and moving instructions"
                )

    story_scenes = {
        str(scene.get("scene_id")): float(scene.get("runtime_budget_sec", 0))
        for scene in (story.get("scenes") or [])
        if isinstance(scene, dict) and scene.get("scene_id")
    }
    for scene_id, expected in story_scenes.items():
        actual = contribution_by_scene.get(scene_id, 0.0)
        if abs(expected - actual) > 0.01:
            errors.append(
                f"director-intent: clip contribution for scene {scene_id} {actual} != story budget {expected}"
            )
    all_story_beats = set().union(*story_scene_beats.values()) if story_scene_beats else set()
    missing_beats = sorted(all_story_beats - covered_beats)
    if missing_beats:
        errors.append(f"director-intent: story beats without clip coverage {missing_beats}")
    for index in range(1, len(all_specs)):
        previous = all_specs[index - 1]
        current = all_specs[index]
        previous_exit = flatten_paths(previous.get("exit_state") or {})
        current_entry = flatten_paths(current.get("entry_state") or {})
        preserved_paths = {
            path
            for path, value in previous_exit.items()
            if path in current_entry and current_entry[path] == value
        }
        if not preserved_paths:
            errors.append(
                f"director-intent: adjacent clips {previous.get('clip_id')} -> "
                f"{current.get('clip_id')} share no concrete preserved state path"
            )
        if previous.get("scene_id") == current.get("scene_id"):
            mismatches = sorted(
                path
                for path, value in previous_exit.items()
                if path in current_entry
                and current_entry[path] != value
                and path.startswith(("characters.", "props.", "environment.location"))
            )
            if mismatches:
                errors.append(
                    f"director-intent: adjacent clip state mismatch "
                    f"{previous.get('clip_id')} -> {current.get('clip_id')} at {mismatches}"
                )
    return all_specs


def validate_director(package: Path) -> list[str]:
    errors = validate_story(package)
    story_path = package / "story_and_scene_map.yaml"
    director_path = package / "director_intent.yaml"
    visual_path = package / "visual_bible.yaml"
    manifest_path = package / "reference_asset_manifest.yaml"
    required = [story_path, director_path, visual_path, manifest_path]
    if any(not path.is_file() for path in required):
        errors.append("director stage requires story, director intent, visual bible, and reference manifest")
        return errors
    story = load_yaml(story_path)
    director = load_yaml(director_path)
    visual = load_yaml(visual_path)
    manifest = load_yaml(manifest_path)
    require_frozen_header(director, "director-intent", errors)
    require_frozen_header(visual, "visual-bible", errors)
    require_frozen_header(manifest, "reference-asset-manifest", errors)
    require_source_ref(director, story, "director-intent", errors)
    require_source_ref(visual, director, "visual-bible", errors)
    require_source_ref(manifest, visual, "reference-asset-manifest", errors)
    require_fields(
        director,
        ["audience_experience", "theme_form_answer", "viewpoint_rule", "scenes"],
        "director-intent",
        errors,
    )
    require_fields(
        visual,
        ["aspect_ratio", "visual_medium", "camera_language", "lighting_rules", "characters", "locations", "props"],
        "visual-bible",
        errors,
        empty_list_ok_fields={"characters", "props"},
    )
    brief = load_yaml(package / "ai_video_brief.yaml")
    if brief.get("format") == "novel-adaptation":
        state_ids_by_character: dict[str, set[str]] = {}
        for index, character in enumerate(visual.get("characters") or []):
            label = f"visual-bible.characters[{index}]"
            if not isinstance(character, dict) or not character.get("character_id"):
                errors.append(f"{label}: missing character_id")
                continue
            versions = character.get("state_versions")
            if not isinstance(versions, list) or not versions:
                errors.append(f"{label}: novel adaptation requires state_versions")
                continue
            state_ids_by_character[str(character["character_id"])] = {
                str(version.get("state_id"))
                for version in versions
                if isinstance(version, dict) and version.get("state_id")
            }
        for index, change in enumerate(story.get("temporal_state_changes") or []):
            if not isinstance(change, dict):
                continue
            label = f"story-and-scene-map.temporal_state_changes[{index}]"
            character_id = str(change.get("character_id", ""))
            if character_id not in state_ids_by_character:
                errors.append(f"{label}: unknown character_id {character_id}")
                continue
            known_states = state_ids_by_character[character_id]
            for field in ("from_state_id", "to_state_id"):
                state_id = str(change.get(field, ""))
                if state_id not in known_states:
                    errors.append(f"{label}: unknown {field} {state_id}")
    specs = validate_clip_specs(story, director, visual, errors)
    required_review_ids = set(parse_list(story.get("must_beat_ids")))
    required_review_ids.update(str(spec.get("clip_id")) for spec in specs if spec.get("clip_id"))
    required_review_ids.update(
        str(item.get("requirement_id"))
        for item in (story.get("delivery_requirements") or [])
        if isinstance(item, dict) and item.get("requirement_id")
    )
    validate_semantic_review(
        package,
        "director",
        [
            "story_and_scene_map.yaml",
            "director_intent.yaml",
            "visual_bible.yaml",
            "reference_asset_manifest.yaml",
        ],
        required_review_ids,
        errors,
    )
    return errors


def print_result(stage: str, errors: list[str]) -> int:
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {stage} stage is frozen and independently reviewed")
    return 0
