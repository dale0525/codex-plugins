#!/usr/bin/env python3
"""Exercise valid and intentionally invalid AI-video production packages."""

from __future__ import annotations

import json
import hashlib
import csv
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
BASE_PACKAGE = SKILL_ROOT / "assets" / "examples" / "red-envelope-production-package"
NOVEL_PACKAGE = SKILL_ROOT / "tests" / "capabilities" / "novel_adaptation" / "retest-output"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_ai_video_package.py"
ADAPTERS = SKILL_ROOT / "references" / "model-adapters.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def save_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def production_ready_without_references(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["production_status"] = "production_ready"
    data["prompts"][0]["reference_inputs_attached"] = []
    save_json(path, data)


def contradictory_camera_instruction(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["prompt_ir"]["camera"]["behavior"] = (
        "locked static camera with a slow dolly move"
    )
    save_json(path, data)


def incorrect_edit_runtime(package: Path) -> None:
    path = package / "edit_plan.yaml"
    data = load_yaml(path)
    data["timeline"][0]["contribution_sec"] += 1
    save_yaml(path, data)


def adjacent_continuity_conflict(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][1]["entry"]["environment"]["screen_axis"] = "door_right-printer_left"
    save_json(path, data)


def generation_log_missing_clip(package: Path) -> None:
    path = package / "generation_log.jsonl"
    records = load_jsonl(path)
    save_jsonl(path, records[:-1])


def generation_log_duplicate_run(package: Path) -> None:
    path = package / "generation_log.jsonl"
    records = load_jsonl(path)
    records[1]["run_id"] = records[0]["run_id"]
    save_jsonl(path, records)


def final_pass_without_approved_outputs(package: Path) -> None:
    path = package / "final_film_qc.yaml"
    data = load_yaml(path)
    for field in (
        "runtime",
        "must_beat_coverage",
        "continuity",
        "dialogue_lipsync",
        "sound",
        "rights_and_provenance",
    ):
        data[field] = "pass"
    data["blocking_conflicts"] = []
    data["final_result"] = "pass"
    save_yaml(path, data)


def empty_prompt_action(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["prompt_ir"]["action"] = {}
    save_json(path, data)


def placeholder_rendered_prompt(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["rendered_prompt"] = "pending"
    data["prompts"][0]["rendered_prompt_hash"] = hashlib.sha256(b"pending").hexdigest()
    save_json(path, data)


def hollow_continuity_state(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][0]["entry"] = {}
    data["clips"][0]["exit"] = {}
    save_json(path, data)


def generation_log_without_prompt_link(package: Path) -> None:
    path = package / "generation_log.jsonl"
    records = load_jsonl(path)
    records[0]["prompt_hash"] = ""
    save_jsonl(path, records)


def planned_generation_claims_actual(package: Path) -> None:
    path = package / "generation_log.jsonl"
    records = load_jsonl(path)
    records[0]["actual"] = {"duration_sec": 8}
    save_jsonl(path, records)


def approved_asset_without_hash(package: Path) -> None:
    path = package / "reference_asset_manifest.yaml"
    data = load_yaml(path)
    data["assets"][0]["status"] = "approved"
    data["assets"][0]["reference_transport"] = "attached"
    data["assets"][0]["sha256"] = ""
    save_yaml(path, data)


def story_runtime_reconciliation_mismatch(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["runtime_reconciliation"]["scene_budget_sum_sec"] += 1
    save_yaml(path, data)


def unknown_continuity_prop(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][0]["entry"].setdefault("props", {})["PROP-UNKNOWN"] = {
        "condition": "invented"
    }
    save_json(path, data)


def first_frame_without_reference(package: Path) -> None:
    path = package / "clip_plan.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    rows[0]["reference_inputs_expected"] = ""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sound_row_missing_status(package: Path) -> None:
    path = package / "sound_cue_sheet.csv"
    rows = list(csv.reader(path.open(encoding="utf-8", newline="")))
    rows[1] = rows[1][:-1]
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)


def clip_qc_missing_status(package: Path) -> None:
    path = package / "clip_qc_report.yaml"
    data = load_yaml(path)
    data["clips"][0].pop("status", None)
    save_yaml(path, data)


def unsupported_adapter_duration(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["adapter_id"] = "openai-sora-2-videos-api-2026-07"
    data["prompts"][0]["output_contract"]["duration_sec"] = 7
    save_json(path, data)


def prompt_clip_duration_mismatch(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["output_contract"]["duration_sec"] += 1
    save_json(path, data)


def deprecated_adapter_without_migration(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["adapter_id"] = "openai-sora-2-videos-api-2026-07"
    data["prompts"][0]["output_contract"]["duration_sec"] = 8
    data["prompts"][0].pop("deprecation_warning", None)
    data["prompts"][0].pop("migration_fallback", None)
    save_json(path, data)


def unknown_protagonist(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["protagonist"]["character_id"] = "CHAR-NOT-IN-VISUAL-BIBLE"
    save_yaml(path, data)


def continuity_location_differs_from_clip(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][0]["entry"]["environment"]["location"] = "LOC-UNKNOWN-TRANSITION"
    save_json(path, data)


def novel_character_without_state_versions(package: Path) -> None:
    path = package / "visual_bible.yaml"
    data = load_yaml(path)
    data["characters"][0].pop("state_versions", None)
    save_yaml(path, data)


def novel_temporal_shift_without_state_change(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["logline"] += "，她重生后再次来到这里。"
    data["temporal_state_changes"] = []
    save_yaml(path, data)


def novel_prompt_subject_mismatch(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["prompt_ir"]["visible_character_ids"] = []
    save_json(path, data)


def novel_overloaded_short_clip(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    prompt = next(item for item in data["prompts"] if item["clip_id"] == "C-S01-06")
    prompt["prompt_ir"]["action_steps"] = [
        {"step_id": f"A0{index}", "action": f"action {index}", "end_state": f"state {index}"}
        for index in range(1, 5)
    ]
    save_json(path, data)


def novel_prompt_missing_delivery(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["delivery_requirement_ids"] = []
    data["prompts"][0]["information_carriers"] = []
    save_json(path, data)


def novel_edit_missing_delivery(package: Path) -> None:
    path = package / "edit_plan.yaml"
    data = load_yaml(path)
    data["timeline"][0]["delivery_requirement_ids"] = []
    save_yaml(path, data)


def novel_requirement_visible_entity_missing(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["delivery_requirements"][0]["required_character_ids"].append("CHAR-SHENYU")
    data["delivery_requirements"][0]["visible_character_ids"].append("CHAR-SHENYU")
    save_yaml(path, data)


def novel_prompt_location_mismatch(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["prompt_ir"]["environment"]["location_id"] = "LOC-NOT-CLIP"
    save_json(path, data)


def novel_placeholder_story_state(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["scenes"][0]["beats"][0]["visible_start"] = "前一状态"
    save_yaml(path, data)


def novel_temporal_states_without_difference(package: Path) -> None:
    story_path = package / "story_and_scene_map.yaml"
    story = load_yaml(story_path)
    story["logline"] += "，重生后再来一次。"
    story["temporal_state_changes"] = [
        {
            "character_id": "CHAR-SHENLAN",
            "from_state_id": "CHAR-SHENLAN.STATE-STORM",
            "transition_beat_id": "S01-B01",
            "to_state_id": "CHAR-SHENLAN.STATE-STORM",
        }
    ]
    save_yaml(story_path, story)


def novel_scene_clip_location_mismatch(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["scenes"][0]["location_id"] = "LOC-DIFFERENT-SCENE"
    save_yaml(path, data)


def novel_state_ref_is_camera_behavior(package: Path) -> None:
    path = package / "clip_plan.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    rows[0]["exit_state_ref"] = rows[0]["camera_behavior"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def novel_requirement_spans_four_beats(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    for beat in data["scenes"][0]["beats"][1:4]:
        beat["delivery_requirement_ids"].append("DR-B01")
    save_yaml(path, data)


def novel_prompt_action_placeholder(package: Path) -> None:
    path = package / "generation_prompt_pack.json"
    data = load_json(path)
    data["prompts"][0]["prompt_ir"]["action"]["start"] = "进入状态由clip plan定义"
    save_json(path, data)


def novel_continuity_unknown_state_version(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][0]["entry"]["characters"]["CHAR-SHENLAN"]["state_id"] = "planned"
    save_json(path, data)


def novel_continuity_missing_clip_prop(package: Path) -> None:
    path = package / "continuity_state.json"
    data = load_json(path)
    data["clips"][1]["entry"]["props"].pop("PROP-PLAYER", None)
    save_json(path, data)


def novel_scene_clip_runtime_mismatch(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    data["scenes"][0]["runtime_budget_sec"] = 89
    data["runtime_reconciliation"]["scene_budget_sum_sec"] = 89
    save_yaml(path, data)


def novel_hook_owns_multiple_requirements(package: Path) -> None:
    path = package / "story_and_scene_map.yaml"
    data = load_yaml(path)
    beat = data["scenes"][0]["beats"][-1]
    beat["narrative_function"] = "hook"
    beat["delivery_requirement_ids"].append("DR-B01")
    save_yaml(path, data)


FixtureMutation = Callable[[Path], None]


INVALID_CASES: list[tuple[str, FixtureMutation, tuple[str, ...]]] = [
    (
        "production_ready_missing_references",
        production_ready_without_references,
        ("production_ready with missing references",),
    ),
    (
        "prompt_locked_and_moving",
        contradictory_camera_instruction,
        ("camera contains locked/static and moving instructions",),
    ),
    (
        "edit_runtime_mismatch",
        incorrect_edit_runtime,
        ("edit-plan.timeline contribution",),
    ),
    (
        "adjacent_continuity_conflict",
        adjacent_continuity_conflict,
        ("continuity conflict at environment.screen_axis",),
    ),
    (
        "generation_log_missing_clip",
        generation_log_missing_clip,
        ("generation-log.jsonl: missing IDs",),
    ),
    (
        "generation_log_duplicate_run",
        generation_log_duplicate_run,
        ("duplicate run_id",),
    ),
    (
        "final_pass_without_approved_outputs",
        final_pass_without_approved_outputs,
        (
            "final-film-qc approved clips: missing IDs",
            "final-film-qc generation provenance: missing IDs",
        ),
    ),
    (
        "empty_prompt_action",
        empty_prompt_action,
        ("prompt_ir: missing action",),
    ),
    (
        "placeholder_rendered_prompt",
        placeholder_rendered_prompt,
        ("rendered_prompt is placeholder or too short",),
    ),
    (
        "hollow_continuity_state",
        hollow_continuity_state,
        ("entry must contain a meaningful state payload",),
    ),
    (
        "generation_log_without_prompt_link",
        generation_log_without_prompt_link,
        ("prompt_hash must be SHA-256", "generation-log current prompt linkage"),
    ),
    (
        "planned_generation_claims_actual",
        planned_generation_claims_actual,
        ("non-generated status cannot claim",),
    ),
    (
        "approved_asset_without_hash",
        approved_asset_without_hash,
        ("attached/approved asset missing sha256",),
    ),
    (
        "story_runtime_reconciliation_mismatch",
        story_runtime_reconciliation_mismatch,
        ("scene runtime budget sum",),
    ),
    (
        "unknown_continuity_prop",
        unknown_continuity_prop,
        ("unknown props ['PROP-UNKNOWN']",),
    ),
    (
        "first_frame_without_reference",
        first_frame_without_reference,
        ("requires a planned first-frame reference",),
    ),
    (
        "sound_row_missing_status",
        sound_row_missing_status,
        ("missing trailing CSV fields", "missing status"),
    ),
    (
        "clip_qc_missing_status",
        clip_qc_missing_status,
        ("clip-qc-report.clips[0]: missing status",),
    ),
    (
        "unsupported_adapter_duration",
        unsupported_adapter_duration,
        ("duration_sec 7 not allowed by adapter",),
    ),
    (
        "prompt_clip_duration_mismatch",
        prompt_clip_duration_mismatch,
        ("output duration", "!= clip target"),
    ),
    (
        "deprecated_adapter_without_migration",
        deprecated_adapter_without_migration,
        ("deprecated adapter requires deprecation_warning", "deprecated adapter requires migration_fallback"),
    ),
    (
        "unknown_protagonist",
        unknown_protagonist,
        ("protagonist character_id CHAR-NOT-IN-VISUAL-BIBLE",),
    ),
    (
        "continuity_location_differs_from_clip",
        continuity_location_differs_from_clip,
        ("!= clip-plan location",),
    ),
]


NOVEL_INVALID_CASES: list[tuple[str, FixtureMutation, tuple[str, ...]]] = [
    (
        "novel_character_without_state_versions",
        novel_character_without_state_versions,
        ("requires non-empty state_versions",),
    ),
    (
        "novel_temporal_shift_without_state_change",
        novel_temporal_shift_without_state_change,
        ("temporal/rebirth language requires explicit temporal_state_changes",),
    ),
    (
        "novel_prompt_subject_mismatch",
        novel_prompt_subject_mismatch,
        ("visible_character_ids", "!= clip subject_ids"),
    ),
    (
        "novel_overloaded_short_clip",
        novel_overloaded_short_clip,
        ("8s clip has 4 action_steps",),
    ),
    (
        "novel_prompt_missing_delivery",
        novel_prompt_missing_delivery,
        ("delivery requirements not carried by prompts",),
    ),
    (
        "novel_edit_missing_delivery",
        novel_edit_missing_delivery,
        ("delivery requirements not used",),
    ),
    (
        "novel_requirement_visible_entity_missing",
        novel_requirement_visible_entity_missing,
        ("required narrative characters missing", "required visible characters missing"),
    ),
    (
        "novel_prompt_location_mismatch",
        novel_prompt_location_mismatch,
        ("prompt location LOC-NOT-CLIP != clip location",),
    ),
    (
        "novel_placeholder_story_state",
        novel_placeholder_story_state,
        ("must be concrete, not a placeholder",),
    ),
    (
        "novel_temporal_states_without_difference",
        novel_temporal_states_without_difference,
        ("from/to state versions have no concrete visible difference",),
    ),
    (
        "novel_scene_clip_location_mismatch",
        novel_scene_clip_location_mismatch,
        ("!= scene location LOC-DIFFERENT-SCENE",),
    ),
    (
        "novel_state_ref_is_camera_behavior",
        novel_state_ref_is_camera_behavior,
        ("entry/exit state ref cannot equal camera_behavior",),
    ),
    (
        "novel_requirement_spans_four_beats",
        novel_requirement_spans_four_beats,
        ("spans 4 beats",),
    ),
    (
        "novel_prompt_action_placeholder",
        novel_prompt_action_placeholder,
        ("prompt_ir.action.start", "must be concrete, not a placeholder"),
    ),
    (
        "novel_continuity_unknown_state_version",
        novel_continuity_unknown_state_version,
        ("unknown state_id planned",),
    ),
    (
        "novel_continuity_missing_clip_prop",
        novel_continuity_missing_clip_prop,
        ("do not include clip prop_ids",),
    ),
    (
        "novel_scene_clip_runtime_mismatch",
        novel_scene_clip_runtime_mismatch,
        ("scene S01 edit contribution 90.0 != story scene runtime budget 89.0",),
    ),
    (
        "novel_hook_owns_multiple_requirements",
        novel_hook_owns_multiple_requirements,
        ("hook beat must own exactly one independent delivery requirement",),
    ),
]


def validate(package: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            str(package),
            "--adapters",
            str(ADAPTERS),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="film-craft-fixtures-") as temp_dir:
        temp_root = Path(temp_dir)
        valid_package = temp_root / "valid_pending_diagnostic"
        shutil.copytree(BASE_PACKAGE, valid_package)
        valid_result = validate(valid_package)
        if valid_result.returncode != 0:
            failures.append(
                "valid_pending_diagnostic unexpectedly failed:\n"
                + valid_result.stdout
                + valid_result.stderr
            )
        else:
            print("PASS valid_pending_diagnostic")

        for name, mutate, expected_messages in INVALID_CASES:
            package = temp_root / name
            shutil.copytree(BASE_PACKAGE, package)
            mutate(package)
            result = validate(package)
            combined = result.stdout + result.stderr
            missing_messages = [message for message in expected_messages if message not in combined]
            if result.returncode == 0:
                failures.append(f"{name} unexpectedly passed")
            elif missing_messages:
                failures.append(
                    f"{name} failed for the wrong reason; missing {missing_messages}:\n{combined}"
                )
            else:
                print(f"PASS {name}: rejected as expected")

        valid_novel = temp_root / "valid_novel_pending_diagnostic"
        shutil.copytree(NOVEL_PACKAGE, valid_novel)
        valid_novel_result = validate(valid_novel)
        if valid_novel_result.returncode != 0:
            failures.append(
                "valid_novel_pending_diagnostic unexpectedly failed:\n"
                + valid_novel_result.stdout
                + valid_novel_result.stderr
            )
        else:
            print("PASS valid_novel_pending_diagnostic")

        for name, mutate, expected_messages in NOVEL_INVALID_CASES:
            package = temp_root / name
            shutil.copytree(NOVEL_PACKAGE, package)
            mutate(package)
            result = validate(package)
            combined = result.stdout + result.stderr
            missing_messages = [message for message in expected_messages if message not in combined]
            if result.returncode == 0:
                failures.append(f"{name} unexpectedly passed")
            elif missing_messages:
                failures.append(
                    f"{name} failed for the wrong reason; missing {missing_messages}:\n{combined}"
                )
            else:
                print(f"PASS {name}: rejected as expected")

        malformed_adaptation = temp_root / "malformed_adaptation_csv"
        shutil.copytree(NOVEL_PACKAGE, malformed_adaptation)
        matrix_path = malformed_adaptation / "adaptation_matrix.csv"
        matrix_lines = matrix_path.read_text(encoding="utf-8").splitlines()
        matrix_lines[1] = matrix_lines[1] + ",unescaped-extra"
        matrix_path.write_text("\n".join(matrix_lines) + "\n", encoding="utf-8")
        adaptation_result = validate(malformed_adaptation)
        adaptation_output = adaptation_result.stdout + adaptation_result.stderr
        if adaptation_result.returncode == 0:
            failures.append("malformed_adaptation_csv unexpectedly passed")
        elif "extra unescaped CSV fields" not in adaptation_output:
            failures.append(
                "malformed_adaptation_csv failed for the wrong reason:\n" + adaptation_output
            )
        else:
            print("PASS malformed_adaptation_csv: rejected as expected")

        missing_anchor = temp_root / "missing_adaptation_anchor"
        shutil.copytree(NOVEL_PACKAGE, missing_anchor)
        anchor_path = missing_anchor / "adaptation_matrix.csv"
        with anchor_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            anchor_rows = list(reader)
        anchor_rows[0]["source_anchor"] = ""
        with anchor_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(anchor_rows)
        anchor_result = validate(missing_anchor)
        anchor_output = anchor_result.stdout + anchor_result.stderr
        if anchor_result.returncode == 0:
            failures.append("missing_adaptation_anchor unexpectedly passed")
        elif "missing source_anchor" not in anchor_output:
            failures.append(
                "missing_adaptation_anchor failed for the wrong reason:\n" + anchor_output
            )
        else:
            print("PASS missing_adaptation_anchor: rejected as expected")

        empty_director = temp_root / "empty_novel_director_beats"
        shutil.copytree(NOVEL_PACKAGE, empty_director)
        director_path = empty_director / "director_intent.yaml"
        director_data = load_yaml(director_path)
        director_data["scenes"][0]["performance_beats"] = []
        director_data["scenes"][0]["blocking_beats"] = []
        save_yaml(director_path, director_data)
        director_result = validate(empty_director)
        director_output = director_result.stdout + director_result.stderr
        if director_result.returncode == 0:
            failures.append("empty_novel_director_beats unexpectedly passed")
        elif "novel adaptation requires performance_beats" not in director_output:
            failures.append(
                "empty_novel_director_beats failed for the wrong reason:\n" + director_output
            )
        else:
            print("PASS empty_novel_director_beats: rejected as expected")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print(
        f"OK: 2 valid and "
        f"{len(INVALID_CASES) + len(NOVEL_INVALID_CASES) + 3} "
        "invalid AI-video fixtures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
