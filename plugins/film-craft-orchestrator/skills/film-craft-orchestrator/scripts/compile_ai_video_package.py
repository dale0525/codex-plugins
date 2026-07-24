#!/usr/bin/env python3
"""Compile frozen creative decisions into deterministic AI-video package skeletons."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from ai_video_common import load_json, load_yaml, parse_list
from stage_validation_common import validate_adaptation, validate_director


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

CLIP_COLUMNS = [
    "clip_id", "scene_id", "beat_ids", "priority", "narrative_purpose",
    "target_duration_sec", "edit_contribution_sec", "handle_in_sec", "handle_out_sec",
    "subject_ids", "prop_ids", "location_id", "entry_state_ref", "primary_action",
    "attention_change", "camera_behavior", "exit_state_ref", "generation_method",
    "visual_bible_ref", "reference_inputs_expected", "prompt_pack_ref",
    "continuity_risks", "fallback", "status",
]

SOUND_COLUMNS = [
    "cue_id", "scene_id", "clip_id", "timeline_start_sec", "timeline_end_sec",
    "category", "diegetic", "source", "rights_status", "audio_hash", "sync_target",
    "entry", "exit", "status",
]

QC_GATES = (
    "technical", "semantic", "continuity", "editability", "sound_lipsync", "provenance"
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def artifact_ref(data: dict[str, Any]) -> str:
    return f"{data['id']}@{data['version']}"


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_yaml(path: Path, value: Any) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


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


def clip_specs(director: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for scene in director.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for spec in scene.get("clip_specs") or []:
            if isinstance(spec, dict):
                item = deepcopy(spec)
                item["scene_id"] = str(scene.get("scene_id", ""))
                item["scene_sound_intent"] = str(scene.get("sound_intent", ""))
                specs.append(item)
    return specs


def build_story_indexes(story: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    beats: dict[str, dict[str, Any]] = {}
    for scene in story.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for beat in scene.get("beats") or []:
            if isinstance(beat, dict) and beat.get("beat_id"):
                beats[str(beat["beat_id"])] = beat
    requirements = {
        str(item.get("requirement_id")): item
        for item in (story.get("delivery_requirements") or [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    return beats, requirements


def state_ref(clip_id: str, state_name: str) -> str:
    return f"STATE-{clip_id}-{state_name.upper()}"


def join_ids(value: Any) -> str:
    return "|".join(parse_list(value))


def ordered_union(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in parse_list(value):
            if item not in result:
                result.append(item)
    return result


def normalize_action(spec: dict[str, Any]) -> dict[str, Any]:
    action = deepcopy(spec.get("action") or {})
    steps = action.pop("steps", [])
    return {
        "start": str(action.get("start", "")),
        "motion": str(action.get("motion", "")),
        "end": str(action.get("end", "")),
        "steps": deepcopy(steps),
    }


def visible_traits(
    visual: dict[str, Any], character_ids: list[str]
) -> list[str]:
    characters = {
        str(item.get("character_id")): item
        for item in (visual.get("characters") or [])
        if isinstance(item, dict) and item.get("character_id")
    }
    traits: list[str] = []
    for character_id in character_ids:
        character = characters.get(character_id) or {}
        candidates = (
            character.get("visible_anchors")
            or character.get("identity_anchors")
            or character.get("identity_anchor")
            or []
        )
        traits.extend(parse_list(candidates))
    return traits


def requirement_ids_for_spec(
    spec: dict[str, Any], beats: dict[str, dict[str, Any]]
) -> list[str]:
    result: list[str] = []
    for beat_id in parse_list(spec.get("beat_ids")):
        for requirement_id in parse_list((beats.get(beat_id) or {}).get("delivery_requirement_ids")):
            if requirement_id not in result:
                result.append(requirement_id)
    return result


def build_carriers(
    spec: dict[str, Any],
    requirement_ids: list[str],
    requirements: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    overrides = {
        str(item.get("requirement_id")): item
        for item in (spec.get("information_carrier_overrides") or [])
        if isinstance(item, dict) and item.get("requirement_id")
    }
    carriers: list[dict[str, Any]] = []
    for requirement_id in requirement_ids:
        requirement = requirements.get(requirement_id) or {}
        override = overrides.get(requirement_id) or {}
        approved = parse_list(requirement.get("approved_carriers"))
        carrier = str(override.get("carrier") or (approved[0] if approved else "visual_action"))
        carriers.append(
            {
                "requirement_id": requirement_id,
                "carrier": carrier,
                "content": str(override.get("content") or requirement.get("content") or ""),
                "fallback": str(override.get("fallback") or spec.get("fallback") or ""),
            }
        )
    return carriers


def render_prompt(spec: dict[str, Any], prompt_ir: dict[str, Any]) -> str:
    subject = prompt_ir["subject"]
    action = prompt_ir["action"]
    environment = prompt_ir["environment"]
    camera = prompt_ir["camera"]
    steps = "; ".join(
        f"{item.get('step_id')}: {item.get('action')} -> {item.get('end_state')}"
        for item in prompt_ir["action_steps"]
    )
    invariants = "; ".join(prompt_ir["continuity_invariants"])
    negatives = "; ".join(prompt_ir["negative_constraints"])
    parts = [
        f"Subjects: {', '.join(subject['ids']) or 'environment only'}.",
        f"Visible traits: {', '.join(subject['visible_traits']) or 'not applicable'}.",
        f"Location: {environment['location_id']}; time: {environment['time']}.",
        f"Start: {action['start']}. Action: {action['motion']}. End: {action['end']}.",
        f"Ordered action steps: {steps}.",
        f"Camera: {camera['framing']}; {camera['behavior']}.",
        f"Attention moves: {spec.get('attention_change')}.",
        f"Lighting: {prompt_ir['lighting']}.",
        f"Continuity invariants: {invariants or 'none'}.",
        f"Do not: {negatives or 'introduce unplanned subjects, props, or camera moves'}.",
    ]
    return " ".join(parts)


def attached_reference_ids(
    expected: list[str], manifest: dict[str, Any]
) -> list[str]:
    assets = {
        str(item.get("asset_id")): item
        for item in (manifest.get("assets") or [])
        if isinstance(item, dict) and item.get("asset_id")
    }
    attached: list[str] = []
    for asset_id in expected:
        asset = assets.get(asset_id) or {}
        if (
            asset.get("status") in {"approved", "attached"}
            and asset.get("path_or_uri")
            and asset.get("sha256")
            and asset.get("reference_transport") == "attached"
        ):
            attached.append(asset_id)
    return attached


def build_clip_plan(
    specs: list[dict[str, Any]], visual: dict[str, Any]
) -> list[dict[str, Any]]:
    visual_ref = artifact_ref(visual)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        clip_id = str(spec["clip_id"])
        action = normalize_action(spec)
        rows.append(
            {
                "clip_id": clip_id,
                "scene_id": spec["scene_id"],
                "beat_ids": join_ids(spec["beat_ids"]),
                "priority": spec["priority"],
                "narrative_purpose": spec["narrative_purpose"],
                "target_duration_sec": spec["target_duration_sec"],
                "edit_contribution_sec": spec["edit_contribution_sec"],
                "handle_in_sec": spec["handle_in_sec"],
                "handle_out_sec": spec["handle_out_sec"],
                "subject_ids": join_ids(spec["visible_character_ids"]),
                "prop_ids": join_ids(spec["visible_prop_ids"]),
                "location_id": spec["location_id"],
                "entry_state_ref": state_ref(clip_id, "entry"),
                "primary_action": action["motion"],
                "attention_change": spec["attention_change"],
                "camera_behavior": (spec.get("camera") or {}).get("behavior", ""),
                "exit_state_ref": state_ref(clip_id, "exit"),
                "generation_method": spec["generation_method"],
                "visual_bible_ref": visual_ref,
                "reference_inputs_expected": join_ids(spec["reference_inputs_expected"]),
                "prompt_pack_ref": f"PROMPT-{clip_id}",
                "continuity_risks": join_ids(spec["generation_risks"]),
                "fallback": spec["fallback"],
                "status": "planned",
            }
        )
    return rows


def build_prompt_pack(
    brief: dict[str, Any],
    story: dict[str, Any],
    director: dict[str, Any],
    visual: dict[str, Any],
    manifest: dict[str, Any],
    specs: list[dict[str, Any]],
    adapters: dict[str, Any],
) -> dict[str, Any]:
    beats, requirements = build_story_indexes(story)
    adapter_map = {
        str(item.get("id")): item
        for item in (adapters.get("adapters") or [])
        if isinstance(item, dict) and item.get("id")
    }
    prompts: list[dict[str, Any]] = []
    for spec in specs:
        clip_id = str(spec["clip_id"])
        visible_characters = parse_list(spec["visible_character_ids"])
        visible_props = parse_list(spec["visible_prop_ids"])
        requirement_ids = requirement_ids_for_spec(spec, beats)
        required_characters = [
            character_id
            for requirement_id in requirement_ids
            for character_id in parse_list((requirements.get(requirement_id) or {}).get("required_character_ids"))
        ]
        required_props = [
            prop_id
            for requirement_id in requirement_ids
            for prop_id in parse_list((requirements.get(requirement_id) or {}).get("required_prop_ids"))
        ]
        narrative_characters = ordered_union(
            spec["narrative_character_ids"], required_characters
        )
        narrative_props = ordered_union(spec["narrative_prop_ids"], required_props)
        action = normalize_action(spec)
        temporal = deepcopy(spec.get("temporal_constraints") or {})
        temporal["duration_sec"] = spec["target_duration_sec"]
        action_steps = action["steps"]
        temporal.setdefault(
            "event_order", [str(item.get("step_id")) for item in action_steps if item.get("step_id")]
        )
        prompt_ir = {
            "subject": {
                "ids": visible_characters,
                "visible_traits": visible_traits(visual, visible_characters),
            },
            "narrative_character_ids": narrative_characters,
            "narrative_prop_ids": narrative_props,
            "visible_character_ids": visible_characters,
            "visible_prop_ids": visible_props,
            "action": {"start": action["start"], "motion": action["motion"], "end": action["end"]},
            "action_steps": action_steps,
            "environment": {
                "location_id": spec["location_id"],
                "time": str((spec.get("entry_state") or {}).get("environment", {}).get("time", "")),
            },
            "camera": deepcopy(spec["camera"]),
            "lighting": spec["lighting"],
            "temporal_constraints": temporal,
            "continuity_invariants": parse_list(spec["continuity_invariants"]),
            "negative_constraints": parse_list(spec["negative_constraints"]),
        }
        rendered = render_prompt(spec, prompt_ir)
        expected = parse_list(spec["reference_inputs_expected"])
        attached = attached_reference_ids(expected, manifest)
        if not expected:
            transport = "none"
        elif len(attached) == len(expected):
            transport = "attached"
        elif attached:
            transport = "partial"
        else:
            transport = "not_attached"
        adapter_id = str(spec["adapter_id"])
        adapter = adapter_map.get(adapter_id) or {}
        prompt: dict[str, Any] = {
            "prompt_id": f"PROMPT-{clip_id}",
            "clip_id": clip_id,
            "adapter_id": adapter_id,
            "prompt_ir": prompt_ir,
            "delivery_requirement_ids": requirement_ids,
            "information_carriers": build_carriers(spec, requirement_ids, requirements),
            "rendered_prompt": rendered,
            "rendered_prompt_hash": sha256_text(rendered),
            "reference_inputs_expected": expected,
            "reference_inputs_attached": attached,
            "reference_transport": transport,
            "output_contract": {
                "duration_sec": spec["target_duration_sec"],
                "aspect_ratio": (brief.get("constraints") or {}).get("aspect_ratio"),
                "resolution": str(spec.get("resolution") or "unspecified"),
                "entry_state_ref": state_ref(clip_id, "entry"),
                "exit_state_ref": state_ref(clip_id, "exit"),
            },
            "production_status": "diagnostic_preview",
        }
        if str(adapter.get("status", "")).startswith("deprecated"):
            prompt["deprecation_warning"] = str(
                spec.get("deprecation_warning") or "Selected adapter is deprecated or scheduled for shutdown."
            )
            prompt["migration_fallback"] = str(
                spec.get("migration_fallback") or "Route this unchanged prompt IR to an active compatible adapter."
            )
        prompts.append(prompt)
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.prompt-pack",
        "version": "1.0.0",
        "owner_role": "ai_video_supervisor",
        "source_inputs": [artifact_ref(director), artifact_ref(visual), artifact_ref(manifest)],
        "assumptions": ["Compiled skeleton; no generation success is implied."],
        "constraints": {},
        "open_questions": [],
        "status": "draft",
        "change_log": [],
        "prompts": prompts,
    }


def build_continuity(
    brief: dict[str, Any], director: dict[str, Any], specs: list[dict[str, Any]]
) -> dict[str, Any]:
    clips: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        clip_id = str(spec["clip_id"])
        entry = deepcopy(spec["entry_state"])
        exit_state = deepcopy(spec["exit_state"])
        for state in (entry, exit_state):
            state.setdefault("environment", {})["location_id"] = spec["location_id"]
        item: dict[str, Any] = {
            "clip_id": clip_id,
            "entry_state_ref": state_ref(clip_id, "entry"),
            "exit_state_ref": state_ref(clip_id, "exit"),
            "entry": entry,
            "exit": exit_state,
            "expected_next": {"must_preserve": []},
            "conflicts": [],
            "status": "planned",
        }
        if index:
            item["prior_clip_id"] = str(specs[index - 1]["clip_id"])
        if index + 1 < len(specs):
            next_entry = specs[index + 1]["entry_state"]
            exit_paths = flatten_paths(exit_state)
            next_paths = flatten_paths(next_entry)
            preserved = [
                path for path, value in exit_paths.items()
                if path in next_paths and next_paths[path] == value
            ]
            item["expected_next"]["must_preserve"] = preserved
        clips.append(item)
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.continuity-state",
        "version": "1.0.0",
        "owner_role": "ai_video_supervisor",
        "source_inputs": [artifact_ref(director)],
        "assumptions": ["Planned entry and exit states; actual outputs have not been approved."],
        "constraints": {},
        "open_questions": [],
        "status": "planned",
        "change_log": [],
        "clips": clips,
    }


def build_generation_log(prompt_pack: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": f"RUN-{prompt['clip_id']}-001",
            "clip_id": prompt["clip_id"],
            "attempt": 1,
            "baseline_run_id": None,
            "primary_variable": "baseline",
            "provider": "",
            "model": "",
            "model_version": "",
            "adapter_id": prompt["adapter_id"],
            "prompt_hash": prompt["rendered_prompt_hash"],
            "reference_hashes": [],
            "seed": None,
            "output_uri": "",
            "output_hash": "",
            "actual": {},
            "status": "planned",
            "error": None,
        }
        for prompt in prompt_pack["prompts"]
    ]


def build_clip_qc(brief: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, Any]:
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.clip-qc",
        "version": "1.0.0",
        "owner_role": "ai_video_supervisor",
        "source_inputs": [f"{project_id}.continuity-state@1.0.0"],
        "assumptions": [],
        "constraints": {},
        "open_questions": [],
        "status": "qc_pending",
        "change_log": [],
        "clips": [
            {
                "clip_id": spec["clip_id"],
                "gates": {
                    gate: {"result": "pending", "evidence": [], "repair": ""}
                    for gate in QC_GATES
                },
                "status": "pending",
            }
            for spec in specs
        ],
    }


def build_edit_plan(
    brief: dict[str, Any], story: dict[str, Any], specs: list[dict[str, Any]]
) -> dict[str, Any]:
    beats, _ = build_story_indexes(story)
    must_beats = set(parse_list(story.get("must_beat_ids")))
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for spec in specs:
        beat_ids = parse_list(spec["beat_ids"])
        contribution = float(spec["edit_contribution_sec"])
        timeline.append(
            {
                "clip_id": spec["clip_id"],
                "source_in_sec": float(spec["handle_in_sec"]),
                "source_out_sec": float(spec["handle_in_sec"]) + contribution,
                "timeline_start_sec": cursor,
                "timeline_end_sec": cursor + contribution,
                "contribution_sec": contribution,
                "must_beat_ids": [beat_id for beat_id in beat_ids if beat_id in must_beats],
                "delivery_requirement_ids": requirement_ids_for_spec(spec, beats),
                "cut_purpose": spec["narrative_purpose"],
                "transition": "cut",
                "sound_bridge": spec.get("scene_sound_intent", ""),
                "bypassed_failures": [],
                "status": "planned",
            }
        )
        cursor += contribution
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.edit-plan",
        "version": "1.0.0",
        "owner_role": "editor",
        "source_inputs": [f"{project_id}.clip-qc@1.0.0"],
        "assumptions": [],
        "constraints": {"runtime_target_sec": (brief.get("constraints") or {}).get("runtime_target_sec")},
        "open_questions": [],
        "status": "draft",
        "change_log": [],
        "timeline": timeline,
        "runtime_reconciliation": "pass",
    }


def build_sound_rows(brief: dict[str, Any], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0.0
    for index, spec in enumerate(specs, start=1):
        contribution = float(spec["edit_contribution_sec"])
        sound = spec.get("sound") or {}
        rows.append(
            {
                "cue_id": f"SND-{index:03d}",
                "scene_id": spec["scene_id"],
                "clip_id": spec["clip_id"],
                "timeline_start_sec": cursor,
                "timeline_end_sec": cursor + contribution,
                "category": sound.get("category", "ambience"),
                "diegetic": str(bool(sound.get("diegetic", True))).lower(),
                "source": sound.get("source", "generated"),
                "rights_status": brief.get("rights_status", "unknown"),
                "audio_hash": "",
                "sync_target": sound.get("sync_target") or spec["location_id"],
                "entry": sound.get("entry", "cut_in"),
                "exit": sound.get("exit", "cut_out"),
                "status": "planned",
            }
        )
        cursor += contribution
    return rows


def classify_probe_risks(
    story: dict[str, Any], specs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    temporal_beats = {
        str(item.get("transition_beat_id"))
        for item in (story.get("temporal_state_changes") or [])
        if isinstance(item, dict) and item.get("transition_beat_id")
    }
    candidates: dict[str, list[tuple[int, dict[str, Any], list[str]]]] = {
        "multi_character_or_child": [],
        "precision_hand_ui_text": [],
        "temporal_or_age_transition": [],
        "prop_document_or_food_continuity": [],
        "dialogue_or_lipsync": [],
    }
    keywords = {
        "multi_character_or_child": ("child", "多人", "双人", "multi", "two_character"),
        "precision_hand_ui_text": ("hand", "finger", "ui", "text", "button", "contact", "typing", "stamp", "手", "文字"),
        "temporal_or_age_transition": ("age", "temporal", "transition", "flashback", "time", "年龄", "时空"),
        "prop_document_or_food_continuity": ("food", "prop", "continuity", "paper", "document", "wardrobe", "道具", "食物"),
        "dialogue_or_lipsync": ("dialogue", "lip", "speaker", "voice", "audio", "对话", "口型"),
    }
    for spec in specs:
        risk_text = " ".join(parse_list(spec.get("generation_risks"))).lower()
        characters = parse_list(spec.get("visible_character_ids"))
        props = parse_list(spec.get("visible_prop_ids"))
        beat_ids = set(parse_list(spec.get("beat_ids")))
        categories: list[tuple[str, bool]] = [
            (
                "multi_character_or_child",
                len(characters) >= 2 or any(word in risk_text for word in ("child", "多人", "双人", "multi", "two_character")),
            ),
            (
                "precision_hand_ui_text",
                any(word in risk_text for word in ("hand", "finger", "ui", "text", "button", "contact", "手", "文字")),
            ),
            (
                "temporal_or_age_transition",
                bool(beat_ids & temporal_beats) or any(word in risk_text for word in ("age", "temporal", "transition", "flashback", "年龄", "时空")),
            ),
            (
                "prop_document_or_food_continuity",
                bool(props) or any(word in risk_text for word in ("food", "prop", "continuity", "道具", "食物")),
            ),
            (
                "dialogue_or_lipsync",
                any(word in risk_text for word in ("dialogue", "lip", "speaker", "对话", "口型")),
            ),
        ]
        for category, matched in categories:
            if matched:
                category_matches = sum(
                    1 for word in keywords[category] if word in risk_text
                )
                structural_bonus = {
                    "multi_character_or_child": len(characters) * 2,
                    "precision_hand_ui_text": len((spec.get("action") or {}).get("steps") or []),
                    "temporal_or_age_transition": 4 if beat_ids & temporal_beats else 0,
                    "prop_document_or_food_continuity": len(props),
                    "dialogue_or_lipsync": 2 if (spec.get("sound") or {}).get("category") == "dialogue" else 0,
                }[category]
                score = category_matches * 4 + structural_bonus
                candidates[category].append((score, spec, parse_list(spec.get("generation_risks"))))
    probes: list[dict[str, Any]] = []
    uncovered: list[dict[str, str]] = []
    used_clip_ids: set[str] = set()
    for category, options in candidates.items():
        if not options:
            uncovered.append(
                {"category": category, "reason": "No frozen clip currently exercises this risk category."}
            )
            continue
        ordered = sorted(options, key=lambda item: (-item[0], str(item[1]["clip_id"])))
        unused = [item for item in ordered if str(item[1]["clip_id"]) not in used_clip_ids]
        _, spec, risks = (unused or ordered)[0]
        used_clip_ids.add(str(spec["clip_id"]))
        probes.append(
            {
                "probe_id": f"PROBE-{len(probes) + 1:02d}",
                "category": category,
                "clip_id": spec["clip_id"],
                "why_selected": risks or ["structural risk inferred from frozen clip spec"],
                "generation_run_id": f"RUN-{spec['clip_id']}-001",
                "required_evidence_frames": ["entry", "midpoint", "exit"],
                "required_qc_gates": ["semantic", "continuity", "editability"],
                "status": "planned",
            }
        )
    return probes, uncovered


def build_probe_plan(
    brief: dict[str, Any], director: dict[str, Any], story: dict[str, Any], specs: list[dict[str, Any]]
) -> dict[str, Any]:
    probes, uncovered = classify_probe_risks(story, specs)
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.generation-probe-plan",
        "version": "1.0.0",
        "owner_role": "ai_video_supervisor",
        "source_inputs": [artifact_ref(director)],
        "assumptions": ["Risk selection is deterministic; producibility remains unverified."],
        "constraints": {"minimum_risk_categories": 4},
        "open_questions": [],
        "status": "planned",
        "change_log": [],
        "producibility_status": "hypothesis",
        "probes": probes,
        "uncovered_categories": uncovered,
        "promotion_gate": {
            "required_status": "verified_for_sampled_clips",
            "requirements": [
                "actual generated output with SHA-256",
                "entry midpoint and exit evidence frames",
                "semantic continuity and editability QC pass",
                "failure and repair history retained",
            ],
        },
    }


def final_qc(brief: dict[str, Any]) -> dict[str, Any]:
    project_id = str(brief["id"]).removesuffix(".ai-video-brief")
    return {
        "id": f"{project_id}.final-film-qc",
        "version": "1.0.0",
        "owner_role": "ai_video_supervisor",
        "source_inputs": [f"{project_id}.clip-qc@1.0.0", f"{project_id}.edit-plan@1.0.0"],
        "assumptions": [],
        "constraints": {"runtime_target_sec": (brief.get("constraints") or {}).get("runtime_target_sec")},
        "open_questions": ["Generation probes and all clip outputs remain pending."],
        "status": "qc_pending",
        "change_log": [],
        "runtime": "pending",
        "must_beat_coverage": "pending",
        "continuity": "pending",
        "dialogue_lipsync": "pending",
        "sound": "pending",
        "rights_and_provenance": "pending",
        "blocking_conflicts": [],
        "final_result": "pending",
    }


def compile_package(package: Path, adapters_path: Path, replace: bool) -> list[str]:
    errors = validate_director(package)
    brief = load_yaml(package / "ai_video_brief.yaml")
    if brief.get("format") == "novel-adaptation":
        errors.extend(validate_adaptation(package))
    if errors:
        return errors
    existing = [name for name in DERIVED_FILES if (package / name).exists()]
    if existing and not replace:
        return [
            "derived files already exist; compile into a staged package or pass "
            f"--replace-derived explicitly: {existing}"
        ]
    story = load_yaml(package / "story_and_scene_map.yaml")
    director = load_yaml(package / "director_intent.yaml")
    visual = load_yaml(package / "visual_bible.yaml")
    manifest = load_yaml(package / "reference_asset_manifest.yaml")
    adapters = load_json(adapters_path)
    specs = clip_specs(director)
    known_adapters = {
        str(item.get("id"))
        for item in (adapters.get("adapters") or [])
        if isinstance(item, dict) and item.get("id")
    }
    unknown = sorted({str(spec.get("adapter_id")) for spec in specs} - known_adapters)
    if unknown:
        return [f"director clip_specs reference unknown adapters {unknown}"]

    clip_rows = build_clip_plan(specs, visual)
    prompt_pack = build_prompt_pack(brief, story, director, visual, manifest, specs, adapters)
    continuity = build_continuity(brief, director, specs)
    generation_records = build_generation_log(prompt_pack)
    clip_qc = build_clip_qc(brief, specs)
    edit_plan = build_edit_plan(brief, story, specs)
    sound_rows = build_sound_rows(brief, specs)
    probe_plan = build_probe_plan(brief, director, story, specs)

    with (package / "clip_plan.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLIP_COLUMNS)
        writer.writeheader()
        writer.writerows(clip_rows)
    save_json(package / "generation_prompt_pack.json", prompt_pack)
    save_json(package / "continuity_state.json", continuity)
    (package / "generation_log.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in generation_records),
        encoding="utf-8",
    )
    save_yaml(package / "clip_qc_report.yaml", clip_qc)
    save_yaml(package / "edit_plan.yaml", edit_plan)
    with (package / "sound_cue_sheet.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOUND_COLUMNS)
        writer.writeheader()
        writer.writerows(sound_rows)
    save_yaml(package / "final_film_qc.yaml", final_qc(brief))
    save_yaml(package / "generation_probe_plan.yaml", probe_plan)
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    parser.add_argument(
        "--adapters",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "references" / "model-adapters.json",
    )
    parser.add_argument("--replace-derived", action="store_true")
    args = parser.parse_args()
    package = args.package_dir.resolve()
    errors = compile_package(package, args.adapters.resolve(), args.replace_derived)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: compiled {len(DERIVED_FILES)} deterministic derived files in {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
