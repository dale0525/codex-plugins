#!/usr/bin/env python3
"""Validate prompt IR, adapter references, and visible reference transport."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_video_common import die_on_errors, load_json, parse_list, require_fields


PROMPT_REQUIRED = [
    "prompt_id", "clip_id", "adapter_id", "prompt_ir", "rendered_prompt",
    "rendered_prompt_hash", "reference_inputs_expected",
    "reference_transport", "output_contract", "production_status",
]
IR_REQUIRED = ["subject", "action", "environment", "camera", "lighting", "temporal_constraints", "continuity_invariants"]
LOCKED = {"locked", "static", "fixed", "tripod locked"}
MOVING = {"pan", "tilt", "dolly", "orbit", "truck", "crane", "handheld", "push", "pull"}
PLACEHOLDER_RENDERED_PROMPTS = {
    "diagnostic",
    "pending",
    "pending adapter compilation",
    "todo",
    "tbd",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt_pack", type=Path)
    parser.add_argument("--adapters", type=Path, required=True)
    args = parser.parse_args()
    data = load_json(args.prompt_pack)
    adapters_data = load_json(args.adapters)
    adapter_by_id = {
        item.get("id"): item
        for item in adapters_data.get("adapters", [])
        if isinstance(item, dict) and item.get("id")
    }
    adapter_ids = set(adapter_by_id)
    prompts = data.get("prompts") if isinstance(data, dict) else None
    errors: list[str] = []
    if not isinstance(prompts, list) or not prompts:
        errors.append("root: prompts must be a non-empty list")
        return die_on_errors(errors)
    ids: set[str] = set()
    clips: set[str] = set()
    for index, prompt in enumerate(prompts):
        label = f"prompts[{index}]"
        if not isinstance(prompt, dict):
            errors.append(f"{label}: must be object")
            continue
        require_fields(
            prompt,
            PROMPT_REQUIRED,
            label,
            errors,
            empty_list_ok_fields={"reference_inputs_expected"},
        )
        if "reference_inputs_attached" not in prompt:
            errors.append(f"{label}: missing reference_inputs_attached")
        prompt_id = prompt.get("prompt_id")
        clip_id = prompt.get("clip_id")
        if prompt_id in ids:
            errors.append(f"{label}: duplicate prompt_id {prompt_id}")
        ids.add(prompt_id)
        if clip_id in clips:
            errors.append(f"{label}: duplicate clip_id {clip_id}")
        clips.add(clip_id)
        if prompt.get("adapter_id") not in adapter_ids:
            errors.append(f"{label}: unknown adapter_id {prompt.get('adapter_id')}")
        adapter = adapter_by_id.get(prompt.get("adapter_id")) or {}
        allowed_seconds = (adapter.get("limits") or {}).get("seconds")
        duration = (prompt.get("output_contract") or {}).get("duration_sec")
        if isinstance(allowed_seconds, list) and duration not in allowed_seconds:
            errors.append(
                f"{label}: duration_sec {duration!r} not allowed by adapter; "
                f"expected one of {allowed_seconds}"
            )
        if str(adapter.get("status", "")).startswith("deprecated"):
            if not prompt.get("deprecation_warning"):
                errors.append(f"{label}: deprecated adapter requires deprecation_warning")
            if not prompt.get("migration_fallback"):
                errors.append(f"{label}: deprecated adapter requires migration_fallback")
        rendered = str(prompt.get("rendered_prompt", "")).strip()
        if len(rendered) < 20 or rendered.lower() in PLACEHOLDER_RENDERED_PROMPTS:
            errors.append(f"{label}: rendered_prompt is placeholder or too short")
        ir = prompt.get("prompt_ir")
        if not isinstance(ir, dict):
            errors.append(f"{label}: prompt_ir must be object")
            continue
        require_fields(ir, IR_REQUIRED, f"{label}.prompt_ir", errors)
        expected = set(parse_list(prompt.get("reference_inputs_expected")))
        attached = set(parse_list(prompt.get("reference_inputs_attached")))
        missing = sorted(expected - attached)
        if missing and prompt.get("production_status") == "production_ready":
            errors.append(f"{label}: production_ready with missing references {missing}")
        camera = ir.get("camera")
        if not isinstance(camera, dict):
            errors.append(f"{label}.prompt_ir: camera must be object")
            camera_text = ""
        else:
            camera_text = " ".join(str(value) for value in camera.values()).lower()
        has_locked = any(term in camera_text for term in LOCKED)
        has_moving = any(term in camera_text for term in MOVING)
        if has_locked and has_moving:
            errors.append(f"{label}: camera contains locked/static and moving instructions")
        if prompt.get("production_status") not in {"draft", "diagnostic_preview", "production_ready", "superseded"}:
            errors.append(f"{label}: invalid production_status {prompt.get('production_status')}")
    result = die_on_errors(errors)
    if not result:
        print(f"OK: {len(prompts)} prompts with valid adapters and reference gates")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
