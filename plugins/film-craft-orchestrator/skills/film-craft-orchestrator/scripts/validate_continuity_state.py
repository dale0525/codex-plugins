#!/usr/bin/env python3
"""Validate ordered AI video continuity state records."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ai_video_common import die_on_errors, load_json, require_fields


REQUIRED = ["clip_id", "entry", "exit", "expected_next", "conflicts", "status"]


def read_path(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def has_state_payload(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    state_groups = [value.get(name) for name in ("characters", "props", "environment")]
    if any(isinstance(group, dict) and group for group in state_groups):
        return True
    return any(item not in (None, "", [], {}) for item in value.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("continuity_state", type=Path)
    args = parser.parse_args()
    data = load_json(args.continuity_state)
    clips = data.get("clips") if isinstance(data, dict) else None
    errors: list[str] = []
    if not isinstance(clips, list) or not clips:
        errors.append("root: clips must be a non-empty ordered list")
        return die_on_errors(errors)
    ids: set[str] = set()
    prior: dict[str, Any] | None = None
    for index, clip in enumerate(clips):
        label = f"clips[{index}]"
        if not isinstance(clip, dict):
            errors.append(f"{label}: must be object")
            continue
        require_fields(
            clip,
            REQUIRED,
            label,
            errors,
            empty_list_ok_fields={"conflicts"},
        )
        clip_id = clip.get("clip_id")
        if clip_id in ids:
            errors.append(f"{label}: duplicate clip_id {clip_id}")
        ids.add(clip_id)
        conflicts = clip.get("conflicts")
        if not isinstance(conflicts, list):
            errors.append(f"{label}: conflicts must be list")
        else:
            blocking = [item for item in conflicts if isinstance(item, dict) and item.get("severity") == "blocking" and not item.get("resolved")]
            if blocking and clip.get("status") == "approved":
                errors.append(f"{label}: approved with unresolved blocking conflicts")
        if not has_state_payload(clip.get("entry")):
            errors.append(f"{label}: entry must contain a meaningful state payload")
        if not has_state_payload(clip.get("exit")):
            errors.append(f"{label}: exit must contain a meaningful state payload")
        if prior is not None:
            expected_prior = clip.get("prior_clip_id")
            if expected_prior and expected_prior != prior.get("clip_id"):
                errors.append(f"{label}: prior_clip_id {expected_prior} != {prior.get('clip_id')}")
            must_preserve = (prior.get("expected_next") or {}).get("must_preserve", [])
            if not isinstance(must_preserve, list) or not must_preserve:
                errors.append(
                    f"{label}: prior expected_next.must_preserve must be a non-empty list"
                )
                must_preserve = []
            for dotted in must_preserve:
                expected = read_path(prior.get("exit"), str(dotted))
                observed = read_path(clip.get("entry"), str(dotted))
                if expected is None:
                    errors.append(f"{label}: prior exit lacks must_preserve path {dotted}")
                elif observed is None:
                    errors.append(f"{label}: entry lacks must_preserve path {dotted}")
                elif expected != observed:
                    errors.append(f"{label}: continuity conflict at {dotted}: {expected!r} != {observed!r}")
        prior = clip
    result = die_on_errors(errors)
    if not result:
        print(f"OK: {len(clips)} ordered continuity states")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
