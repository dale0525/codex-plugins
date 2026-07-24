#!/usr/bin/env python3
"""Exercise stage gates and deterministic package compilation."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "compiler-fixtures" / "valid-upstream"
SCRIPTS = SKILL_ROOT / "scripts"
ADAPTERS = SKILL_ROOT / "references" / "model-adapters.json"


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def save_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_review_hashes(package: Path, stages: set[str] | None = None) -> None:
    path = package / "semantic_reviews.yaml"
    data = load_yaml(path)
    for review in data.get("reviews") or []:
        if stages is not None and review.get("stage") not in stages:
            continue
        for artifact in review.get("artifacts") or []:
            artifact["sha256"] = digest(package / artifact["path"])
    save_yaml(path, data)


def run_script(name: str, package: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), str(package), *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def expect_pass(
    failures: list[str], label: str, result: subprocess.CompletedProcess[str]
) -> None:
    if result.returncode:
        failures.append(f"{label} unexpectedly failed:\n{result.stdout}{result.stderr}")
    else:
        print(f"PASS {label}")


def expect_reject(
    failures: list[str],
    label: str,
    result: subprocess.CompletedProcess[str],
    expected: str,
) -> None:
    combined = result.stdout + result.stderr
    if result.returncode == 0:
        failures.append(f"{label} unexpectedly passed")
    elif expected not in combined:
        failures.append(f"{label} rejected for wrong reason; missing {expected!r}:\n{combined}")
    else:
        print(f"PASS {label}: rejected as expected")


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="film-craft-staged-") as temporary:
        root = Path(temporary)
        package = root / "valid"
        shutil.copytree(FIXTURE, package)
        refresh_review_hashes(package)

        expect_pass(failures, "story_stage", run_script("validate_story_stage.py", package))
        expect_pass(failures, "director_stage", run_script("validate_director_stage.py", package))
        compile_result = run_script(
            "compile_ai_video_package.py",
            package,
            "--adapters",
            str(ADAPTERS),
        )
        expect_pass(failures, "compile_valid", compile_result)

        expected_derived = {
            "clip_plan.csv",
            "generation_prompt_pack.json",
            "continuity_state.json",
            "generation_log.jsonl",
            "clip_qc_report.yaml",
            "edit_plan.yaml",
            "sound_cue_sheet.csv",
            "final_film_qc.yaml",
            "generation_probe_plan.yaml",
        }
        missing = sorted(name for name in expected_derived if not (package / name).is_file())
        if missing:
            failures.append(f"compiler did not create derived files {missing}")
        probe = load_yaml(package / "generation_probe_plan.yaml")
        if probe.get("producibility_status") != "hypothesis":
            failures.append("compiler falsely promoted producibility beyond hypothesis")
        prompt_pack = json.loads((package / "generation_prompt_pack.json").read_text(encoding="utf-8"))
        if len(prompt_pack.get("prompts") or []) != 2:
            failures.append("compiler did not create exactly one prompt per clip")
        for prompt in prompt_pack.get("prompts") or []:
            rendered = prompt.get("rendered_prompt", "")
            if prompt.get("rendered_prompt_hash") != hashlib.sha256(rendered.encode("utf-8")).hexdigest():
                failures.append(f"compiler prompt hash mismatch for {prompt.get('clip_id')}")

        full_validation = run_script(
            "validate_ai_video_package.py",
            package,
            "--adapters",
            str(ADAPTERS),
        )
        expect_pass(failures, "compiled_package_full_validation", full_validation)
        overwrite_result = run_script(
            "compile_ai_video_package.py",
            package,
            "--adapters",
            str(ADAPTERS),
        )
        expect_reject(
            failures,
            "compiler_refuses_implicit_overwrite",
            overwrite_result,
            "derived files already exist",
        )

        stale = root / "stale-review"
        shutil.copytree(FIXTURE, stale)
        refresh_review_hashes(stale)
        story = load_yaml(stale / "story_and_scene_map.yaml")
        story["ending_choice"] = "The courier secretly keeps the key."
        save_yaml(stale / "story_and_scene_map.yaml", story)
        expect_reject(
            failures,
            "stale_semantic_review_hash",
            run_script("validate_story_stage.py", stale),
            "review hash for story_and_scene_map.yaml is stale or missing",
        )

        duplicate = root / "duplicate-clip"
        shutil.copytree(FIXTURE, duplicate)
        director = load_yaml(duplicate / "director_intent.yaml")
        director["scenes"][0]["clip_specs"][1]["clip_id"] = "C-S01-01"
        save_yaml(duplicate / "director_intent.yaml", director)
        refresh_review_hashes(duplicate)
        expect_reject(
            failures,
            "duplicate_clip_id",
            run_script("validate_director_stage.py", duplicate),
            "duplicate clip_id C-S01-01",
        )

        regressed_state = root / "regressed-state"
        shutil.copytree(FIXTURE, regressed_state)
        regressed_director = load_yaml(regressed_state / "director_intent.yaml")
        regressed_director["scenes"][0]["clip_specs"][1]["entry_state"]["props"]["PROP-KEY"]["state"] = "floor_beside_badge"
        save_yaml(regressed_state / "director_intent.yaml", regressed_director)
        refresh_review_hashes(regressed_state)
        expect_reject(
            failures,
            "adjacent_clip_state_regression",
            run_script("validate_director_stage.py", regressed_state),
            "adjacent clip state mismatch C-S01-01 -> C-S01-02",
        )

        conflicting_camera = root / "conflicting-camera"
        shutil.copytree(FIXTURE, conflicting_camera)
        camera_director = load_yaml(conflicting_camera / "director_intent.yaml")
        camera_director["scenes"][0]["clip_specs"][0]["camera"]["behavior"] = "locked slow push"
        save_yaml(conflicting_camera / "director_intent.yaml", camera_director)
        refresh_review_hashes(conflicting_camera)
        expect_reject(
            failures,
            "director_camera_conflict",
            run_script("validate_director_stage.py", conflicting_camera),
            "camera: contains locked/static and moving instructions",
        )

        novel = root / "valid-novel"
        shutil.copytree(FIXTURE, novel)
        novel_brief = load_yaml(novel / "ai_video_brief.yaml")
        novel_brief["format"] = "novel-adaptation"
        save_yaml(novel / "ai_video_brief.yaml", novel_brief)
        refresh_review_hashes(novel)
        expect_pass(
            failures,
            "adaptation_stage",
            run_script("validate_adaptation_stage.py", novel),
        )
        expect_pass(
            failures,
            "novel_story_stage",
            run_script("validate_story_stage.py", novel),
        )
        expect_pass(
            failures,
            "novel_director_stage",
            run_script("validate_director_stage.py", novel),
        )
        novel_compile = run_script(
            "compile_ai_video_package.py",
            novel,
            "--adapters",
            str(ADAPTERS),
        )
        expect_pass(failures, "compile_valid_novel", novel_compile)
        expect_pass(
            failures,
            "compiled_novel_full_validation",
            run_script(
                "validate_ai_video_package.py",
                novel,
                "--adapters",
                str(ADAPTERS),
            ),
        )

        staged_init = root / "init-staged"
        init_result = subprocess.run(
            [sys.executable, str(SCRIPTS / "init_ai_video_package.py"), str(staged_init)],
            text=True,
            capture_output=True,
            check=False,
        )
        expect_pass(failures, "init_defaults_to_upstream_only", init_result)
        if (staged_init / "clip_plan.csv").exists() or not (staged_init / "semantic_reviews.yaml").exists():
            failures.append("staged init created derived files or omitted semantic review")

        full_init = root / "init-full"
        full_init_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "init_ai_video_package.py"),
                str(full_init),
                "--full-templates",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        expect_pass(failures, "init_full_templates_compatibility", full_init_result)
        if not all((full_init / name).is_file() for name in expected_derived):
            failures.append("full template compatibility mode omitted derived templates")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("OK: staged gates, compiler, overwrite safety, and negative fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
