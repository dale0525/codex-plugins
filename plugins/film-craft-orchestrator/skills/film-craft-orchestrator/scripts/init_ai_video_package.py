#!/usr/bin/env python3
"""Initialize staged AI-video inputs or the legacy full template package."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


UPSTREAM_FILES = (
    "ai_video_brief.yaml",
    "story_and_scene_map.yaml",
    "director_intent.yaml",
    "visual_bible.yaml",
    "reference_asset_manifest.yaml",
    "semantic_reviews.yaml",
)

DERIVED_FILES = (
    "clip_plan.csv",
    "generation_prompt_pack.json",
    "continuity_state.json",
    "generation_log.jsonl",
    "clip_qc_report.yaml",
    "edit_plan.yaml",
    "sound_cue_sheet.csv",
    "final_film_qc.yaml",
    "generation_probe_plan.yaml",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--with-adaptation",
        action="store_true",
        help="also initialize adaptation_matrix.csv for source-based projects",
    )
    parser.add_argument(
        "--full-templates",
        action="store_true",
        help=(
            "also copy derived-file templates for legacy/manual workflows; "
            "new projects should let compile_ai_video_package.py create them"
        ),
    )
    args = parser.parse_args()
    output = args.output_directory.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).resolve().parents[1] / "assets" / "templates"
    names = list(UPSTREAM_FILES)
    if args.full_templates:
        names.extend(DERIVED_FILES)
    for name in names:
        source = templates / name
        if not source.is_file():
            parser.error(f"missing bundled template: {source}")
        shutil.copy2(source, output / name)
    initialized = len(names)
    if args.with_adaptation:
        shutil.copy2(templates / "adaptation_matrix.csv", output / "adaptation_matrix.csv")
        initialized += 1
    mode = "full templates" if args.full_templates else "staged upstream inputs"
    print(f"OK: initialized {initialized} {mode} in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
