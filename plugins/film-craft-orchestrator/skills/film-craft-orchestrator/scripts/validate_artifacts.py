#!/usr/bin/env python3
"""Validate JSON evidence bundles and scene/shot artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def require(mapping: dict[str, Any], fields: list[str], label: str, errors: list[str]) -> None:
    for field in fields:
        if field not in mapping or mapping[field] in (None, ""):
            errors.append(f"{label}: missing {field}")


def validate_evidence(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    require(data, ["video", "artifacts", "segments", "claims", "provenance"], "bundle", errors)
    video = data.get("video")
    if not isinstance(video, dict):
        return ["video: expected an object"]
    require(video, ["id", "url", "title", "channel"], "video", errors)
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("artifacts: expected an object")
    segments = data.get("segments", [])
    segment_ids: set[str] = set()
    if not isinstance(segments, list):
        errors.append("segments: expected an array")
        segments = []
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        if not isinstance(segment, dict):
            errors.append(f"{label}: expected an object")
            continue
        require(segment, ["id", "start_sec", "end_sec", "text"], label, errors)
        if "id" in segment:
            segment_ids.add(str(segment["id"]))
        try:
            if float(segment.get("end_sec", 0)) < float(segment.get("start_sec", 0)):
                errors.append(f"{label}: end_sec before start_sec")
        except (TypeError, ValueError):
            errors.append(f"{label}: timestamps must be numbers")
    claims = data.get("claims", [])
    if not isinstance(claims, list):
        errors.append("claims: expected an array")
        claims = []
    for index, claim in enumerate(claims):
        label = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{label}: expected an object")
            continue
        require(claim, ["id", "statement", "kind", "confidence", "evidence_level"], label, errors)
        if claim.get("evidence_level") not in {"E1", "E2", "E3", "E4", "E5"}:
            errors.append(f"{label}: evidence_level must be E1-E5")
        if not claim.get("segment_ids"):
            errors.append(f"{label}: at least one segment_id is required")
        for segment_id in claim.get("segment_ids", []):
            if str(segment_id) not in segment_ids:
                errors.append(f"{label}: unknown segment_id {segment_id}")
        if not claim.get("evidence_refs"):
            errors.append(f"{label}: evidence_refs required")
    return errors


def validate_scenes(data: Any) -> list[str]:
    scenes = data if isinstance(data, list) else data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list):
        return ["scenes: expected an array or an object with scenes"]
    errors: list[str] = []
    scene_ids: set[str] = set()
    for index, scene in enumerate(scenes):
        label = f"scenes[{index}]"
        if not isinstance(scene, dict):
            errors.append(f"{label}: expected an object")
            continue
        require(scene, ["scene_id", "objective", "obstacle", "turn", "exit_state"], label, errors)
        scene_id = str(scene.get("scene_id", ""))
        if scene_id in scene_ids:
            errors.append(f"{label}: duplicate scene_id {scene_id}")
        scene_ids.add(scene_id)
        if not scene.get("visible_actions"):
            errors.append(f"{label}: visible_actions should contain at least one action")
    return errors


def validate_procedures(data: Any) -> list[str]:
    procedures = data.get("procedures") if isinstance(data, dict) else None
    if not isinstance(procedures, list):
        return ["procedures: expected an object with a procedures array"]
    errors: list[str] = []
    procedure_ids: set[str] = set()
    required = [
        "id",
        "domain",
        "title",
        "claim_refs",
        "applicability",
        "inputs",
        "steps",
        "outputs",
        "failure_signals",
        "acceptance_tests",
        "worked_example",
    ]
    for index, procedure in enumerate(procedures):
        label = f"procedures[{index}]"
        if not isinstance(procedure, dict):
            errors.append(f"{label}: expected an object")
            continue
        require(procedure, required, label, errors)
        procedure_id = str(procedure.get("id", ""))
        if procedure_id in procedure_ids:
            errors.append(f"{label}: duplicate id {procedure_id}")
        procedure_ids.add(procedure_id)

        claim_refs = procedure.get("claim_refs", [])
        if not isinstance(claim_refs, list) or not claim_refs:
            errors.append(f"{label}: at least one claim_ref is required")
        else:
            for ref_index, claim_ref in enumerate(claim_refs):
                ref_label = f"{label}.claim_refs[{ref_index}]"
                if not isinstance(claim_ref, dict):
                    errors.append(f"{ref_label}: expected an object")
                    continue
                require(claim_ref, ["start_sec", "end_sec", "kind", "paraphrase"], ref_label, errors)
                try:
                    if float(claim_ref.get("end_sec", 0)) <= float(claim_ref.get("start_sec", 0)):
                        errors.append(f"{ref_label}: end_sec must be after start_sec")
                except (TypeError, ValueError):
                    errors.append(f"{ref_label}: timestamps must be numbers")

        applicability = procedure.get("applicability", {})
        if not isinstance(applicability, dict):
            errors.append(f"{label}.applicability: expected an object")
        else:
            require(applicability, ["use_when", "do_not_use_when"], f"{label}.applicability", errors)

        steps = procedure.get("steps", [])
        if not isinstance(steps, list) or not steps:
            errors.append(f"{label}: at least one step is required")
        else:
            for step_index, step in enumerate(steps):
                step_label = f"{label}.steps[{step_index}]"
                if not isinstance(step, dict):
                    errors.append(f"{step_label}: expected an object")
                    continue
                require(step, ["step", "action", "check"], step_label, errors)

        tests = procedure.get("acceptance_tests", [])
        if not isinstance(tests, list) or not tests:
            errors.append(f"{label}: at least one acceptance_test is required")
        else:
            for test_index, test in enumerate(tests):
                test_label = f"{label}.acceptance_tests[{test_index}]"
                if not isinstance(test, dict):
                    errors.append(f"{test_label}: expected an object")
                    continue
                require(test, ["test", "pass_criteria"], test_label, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--kind", choices=["evidence", "scenes", "procedures"], required=True)
    args = parser.parse_args()
    try:
        data = load_json(args.path)
        if args.kind == "evidence":
            errors = validate_evidence(data)
        elif args.kind == "scenes":
            errors = validate_scenes(data)
        else:
            errors = validate_procedures(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
