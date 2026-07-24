#!/usr/bin/env python3
"""Validate cross-file consistency for the distilled video corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROCEDURE_GLOB = "distilled-*-procedures.json"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path)
    args = parser.parse_args()

    references = args.skill_dir / "references"
    kb_paths = [references / "video-knowledge-base.json"]
    for optional_name in (
        "foundation-video-knowledge-base.json",
        "targeted-foundation-video-knowledge-base.json",
        "script-screen-video-knowledge-base.json",
    ):
        optional_kb = references / optional_name
        if optional_kb.exists():
            kb_paths.append(optional_kb)
    ai_kb = references / "ai-video-source-knowledge-base.json"
    if ai_kb.exists():
        kb_paths.append(ai_kb)
    asr_paths = [references / "video-asr-evidence.json"]
    for optional_name in (
        "foundation-asr-evidence.json",
        "targeted-foundation-asr-evidence.json",
        "script-screen-asr-evidence.json",
    ):
        optional_asr = references / optional_name
        if optional_asr.exists():
            asr_paths.append(optional_asr)
    ai_asr = references / "ai-video-asr-evidence.json"
    if ai_asr.exists():
        asr_paths.append(ai_asr)
    frame_paths = [references / "video-frame-evidence.json"]
    targeted_frames = references / "targeted-foundation-frame-evidence.json"
    if targeted_frames.exists():
        frame_paths.append(targeted_frames)
    script_screen_frames = references / "script-screen-frame-evidence.json"
    if script_screen_frames.exists():
        frame_paths.append(script_screen_frames)
    ai_frames = references / "ai-video-frame-evidence.json"
    if ai_frames.exists():
        frame_paths.append(ai_frames)
    errors: list[str] = []

    try:
        knowledge_bases = [(path, load_json(path)) for path in kb_paths]
        asr_bundles = [(path, load_json(path)) for path in asr_paths]
        frame_bundles = [(path, load_json(path)) for path in frame_paths]
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    procedure_sources: dict[str, Path] = {}
    procedure_records: dict[str, dict[str, Any]] = {}
    duplicate_procedures: set[str] = set()
    for path in sorted(references.glob(PROCEDURE_GLOB)):
        try:
            data = load_json(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        procedures = data.get("procedures", []) if isinstance(data, dict) else []
        if not isinstance(procedures, list):
            errors.append(f"{path.name}: procedures must be an array")
            continue
        for procedure in procedures:
            if not isinstance(procedure, dict) or not procedure.get("id"):
                errors.append(f"{path.name}: procedure missing id")
                continue
            procedure_id = str(procedure["id"])
            if procedure_id in procedure_sources:
                duplicate_procedures.add(procedure_id)
            else:
                procedure_sources[procedure_id] = path
                procedure_records[procedure_id] = procedure
            if path.name in {
                "distilled-targeted-foundation-procedures.json",
                "distilled-script-screen-procedures.json",
                "distilled-ai-video-procedures.json",
            }:
                required = (
                    "source_refs", "claim_refs", "source_type", "stability",
                    "applicability", "non_applicability", "inputs", "steps",
                    "outputs", "failure_signals", "repair_actions",
                    "acceptance_tests", "worked_example",
                    "counterexample_or_limit", "last_verified_at",
                )
                for field in required:
                    if not procedure.get(field):
                        errors.append(f"{path.name}:{procedure_id}: missing {field}")
                if procedure.get("stability") not in {"stable", "model_specific", "version_specific"}:
                    errors.append(f"{path.name}:{procedure_id}: invalid stability")
                steps = procedure.get("steps", [])
                if isinstance(steps, list):
                    for step_index, step in enumerate(steps):
                        if not isinstance(step, dict) or not step.get("action") or not step.get("check"):
                            errors.append(
                                f"{path.name}:{procedure_id}.steps[{step_index}] requires action and check"
                            )
                tests = procedure.get("acceptance_tests", [])
                if isinstance(tests, list):
                    for test_index, test in enumerate(tests):
                        if not isinstance(test, dict) or not test.get("test") or not test.get("pass_criteria"):
                            errors.append(
                                f"{path.name}:{procedure_id}.acceptance_tests[{test_index}] "
                                "requires test and pass_criteria"
                            )

    for procedure_id in sorted(duplicate_procedures):
        errors.append(f"duplicate procedure id across files: {procedure_id}")

    referenced: list[str] = []
    deep_video_ids: set[str] = set()
    kb_by_video: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    kb_counts: dict[str, tuple[list[dict[str, Any]], Counter[str], Counter[str], set[str]]] = {}
    for kb_path, kb in knowledge_bases:
        local_entries = kb.get("entries", []) if isinstance(kb, dict) else []
        if not isinstance(local_entries, list):
            errors.append(f"{kb_path.name}: entries must be an array")
            continue
        status_counts: Counter[str] = Counter()
        evidence_counts: Counter[str] = Counter()
        local_refs: set[str] = set()
        typed_entries: list[dict[str, Any]] = []
        for index, entry in enumerate(local_entries):
            label = f"{kb_path.name}.entries[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{label} must be an object")
                continue
            typed_entries.append(entry)
            entries.append(entry)
            status = str(entry.get("distillation_status", ""))
            evidence_status = str(entry.get("evidence_status", ""))
            video_id = str(entry.get("video_id", ""))
            if not video_id:
                errors.append(f"{label}: missing video_id")
            elif video_id in kb_by_video:
                errors.append(f"{label}: duplicate video_id across knowledge bases {video_id}")
            else:
                kb_by_video[video_id] = entry
            status_counts[status] += 1
            evidence_counts[evidence_status] += 1
            refs = entry.get("procedure_refs", [])
            if status == "deep_distilled":
                deep_video_ids.add(video_id)
                if evidence_status not in {
                    "asr_reviewed", "full_asr_read", "transcript_verified", "frame_reviewed"
                }:
                    errors.append(f"{video_id}: deep_distilled has unsupported evidence_status {evidence_status}")
                if not isinstance(refs, list) or not refs:
                    errors.append(f"{video_id}: deep_distilled requires procedure_refs")
                    continue
                referenced.extend(str(ref) for ref in refs)
                local_refs.update(str(ref) for ref in refs)
            elif refs:
                errors.append(f"{video_id}: non-deep entry must not have procedure_refs")
        kb_counts[kb_path.name] = (typed_entries, status_counts, evidence_counts, local_refs)

    referenced_counts = Counter(referenced)
    for procedure_id, count in sorted(referenced_counts.items()):
        if procedure_id not in procedure_sources:
            errors.append(f"knowledge base references unknown procedure: {procedure_id}")
    for procedure_id in sorted(set(procedure_sources) - set(referenced_counts)):
        errors.append(f"unreferenced procedure: {procedure_id}")

    asr_video_ids: set[str] = set()
    asr_claim_counts: Counter[str] = Counter()
    claim_to_video: dict[str, str] = {}
    for asr_path, asr in asr_bundles:
        asr_items = asr.get("items", []) if isinstance(asr, dict) else []
        for index, item in enumerate(asr_items):
            label = f"{asr_path.name}.items[{index}]"
            if not isinstance(item, dict) or not item.get("video_id"):
                errors.append(f"{label}: missing video_id")
                continue
            video_id = str(item["video_id"])
            if video_id in asr_video_ids:
                errors.append(f"{label}: duplicate video_id across ASR bundles {video_id}")
            asr_video_ids.add(video_id)
            claims = item.get("claims", [])
            if isinstance(claims, list):
                asr_claim_counts[video_id] = len(claims)
                if asr_path.name in {
                    "foundation-asr-evidence.json",
                    "targeted-foundation-asr-evidence.json",
                    "script-screen-asr-evidence.json",
                    "ai-video-asr-evidence.json",
                } and len(claims) < 3:
                    errors.append(f"{label}: deep evidence requires at least 3 claims")
                for claim_index, claim in enumerate(claims):
                    claim_label = f"{label}.claims[{claim_index}]"
                    if not isinstance(claim, dict) or not claim.get("id"):
                        errors.append(f"{claim_label}: missing id")
                        continue
                    claim_id = str(claim["id"])
                    if claim_id in claim_to_video:
                        errors.append(f"{claim_label}: duplicate claim id {claim_id}")
                    else:
                        claim_to_video[claim_id] = video_id
                    if not isinstance(claim.get("start_sec"), (int, float)):
                        errors.append(f"{claim_label}: start_sec must be numeric")
                    if not isinstance(claim.get("end_sec"), (int, float)):
                        errors.append(f"{claim_label}: end_sec must be numeric")
                    if not claim.get("paraphrase"):
                        errors.append(f"{claim_label}: paraphrase is required")
            kb_entry = kb_by_video.get(video_id)
            if kb_entry is None:
                errors.append(f"{label}: video_id not found in knowledge base")
            else:
                for field in ("title", "channel", "url"):
                    if item.get(field) != kb_entry.get(field):
                        errors.append(f"{label}: {field} differs from knowledge base")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("evidence_status") in {
            "asr_reviewed",
            "full_asr_read",
        }:
            video_id = str(entry.get("video_id", ""))
            if video_id not in asr_video_ids:
                errors.append(f"{video_id}: asr_reviewed but missing from video-asr-evidence.json")

    for procedure_id, procedure in procedure_records.items():
        if procedure_sources[procedure_id].name not in {
            "distilled-targeted-foundation-procedures.json",
            "distilled-script-screen-procedures.json",
            "distilled-ai-video-procedures.json",
        }:
            continue
        source_refs = procedure.get("source_refs", [])
        claim_refs = procedure.get("claim_refs", [])
        if isinstance(source_refs, list):
            for source_ref in source_refs:
                if str(source_ref) not in kb_by_video:
                    errors.append(f"{procedure_id}: unknown source_ref {source_ref}")
        if isinstance(claim_refs, list):
            for claim_ref in claim_refs:
                claim_id = str(claim_ref)
                claim_video = claim_to_video.get(claim_id)
                if claim_video is None:
                    errors.append(f"{procedure_id}: unknown claim_ref {claim_id}")
                elif isinstance(source_refs, list) and claim_video not in source_refs:
                    errors.append(
                        f"{procedure_id}: claim_ref {claim_id} belongs to unlisted source {claim_video}"
                    )

    frame_video_ids: set[str] = set()
    for frame_path, frame_evidence in frame_bundles:
        frame_items = frame_evidence.get("items", []) if isinstance(frame_evidence, dict) else []
        for index, item in enumerate(frame_items):
            label = f"{frame_path.name}.items[{index}]"
            if not isinstance(item, dict) or not item.get("video_id"):
                errors.append(f"{label}: missing video_id")
                continue
            video_id = str(item["video_id"])
            if video_id in frame_video_ids:
                errors.append(f"{label}: duplicate video_id {video_id}")
            frame_video_ids.add(video_id)
            kb_entry = kb_by_video.get(video_id)
            if kb_entry is None:
                errors.append(f"{label}: video_id not found in knowledge base")
            else:
                for field in ("title", "channel", "url"):
                    if item.get(field) != kb_entry.get(field):
                        errors.append(f"{label}: {field} differs from knowledge base")
                if frame_path.name in {
                    "ai-video-frame-evidence.json",
                    "targeted-foundation-frame-evidence.json",
                    "script-screen-frame-evidence.json",
                }:
                    if kb_entry.get("visual_verification_status") != item.get("frame_status"):
                        errors.append(f"{label}: frame_status differs from knowledge base")
            frames = item.get("frames", [])
            if not isinstance(frames, list) or not frames:
                errors.append(f"{label}: frames must be a non-empty array")
                continue
            for frame_index, frame in enumerate(frames):
                frame_label = f"{label}.frames[{frame_index}]"
                if not isinstance(frame, dict):
                    errors.append(f"{frame_label}: expected an object")
                    continue
                if not isinstance(frame.get("timestamp_sec"), (int, float)):
                    errors.append(f"{frame_label}: timestamp_sec must be numeric")
                digest = str(frame.get("frame_sha256", ""))
                if len(digest) != 64:
                    errors.append(f"{frame_label}: frame_sha256 must be 64 hex characters")
                if not frame.get("observation"):
                    errors.append(f"{frame_label}: observation is required")
                if frame_path.name in {
                    "ai-video-frame-evidence.json",
                    "targeted-foundation-frame-evidence.json",
                    "script-screen-frame-evidence.json",
                }:
                    local_path = references / str(frame.get("path", ""))
                    if not local_path.is_file():
                        errors.append(f"{frame_label}: frame file not found {local_path}")
                    else:
                        actual_digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
                        if actual_digest != digest:
                            errors.append(f"{frame_label}: frame hash mismatch")
                    supports = frame.get("supports_claims", [])
                    if not isinstance(supports, list) or not supports:
                        errors.append(f"{frame_label}: supports_claims must be non-empty")
                    else:
                        for claim_id in supports:
                            claim_video = claim_to_video.get(str(claim_id))
                            if claim_video is None:
                                errors.append(f"{frame_label}: unknown claim {claim_id}")
                            elif claim_video != video_id:
                                errors.append(
                                    f"{frame_label}: claim {claim_id} belongs to {claim_video}"
                                )
    for entry in entries:
        if isinstance(entry, dict) and entry.get("frame_artifact"):
            video_id = str(entry.get("video_id", ""))
            if video_id not in frame_video_ids:
                errors.append(f"{video_id}: frame_artifact has no matching frame-evidence item")

    for kb_path, kb in knowledge_bases:
        local_entries, status_counts, evidence_counts, local_refs = kb_counts.get(
            kb_path.name, ([], Counter(), Counter(), set())
        )
        stats = kb.get("corpus_stats", {}) if isinstance(kb, dict) else {}
        expected_stats = {
            "video_entries": len(local_entries),
            "deep_distilled": status_counts["deep_distilled"],
            "claim_evidence_only": status_counts["claim_evidence_only"],
        }
        if kb_path.name == "video-knowledge-base.json":
            expected_stats.update({
                "chapter_hypothesis_only": status_counts["chapter_hypothesis_only"],
                "candidate": status_counts["candidate"],
                "asr_reviewed": evidence_counts["asr_reviewed"],
                "transcript_verified": evidence_counts["transcript_verified"],
                "chapter_verified": evidence_counts["chapter_verified"],
                "metadata_only": evidence_counts["metadata_only"],
                "executable_procedure_units": len(local_refs),
            })
        elif kb_path.name in {
            "foundation-video-knowledge-base.json",
            "targeted-foundation-video-knowledge-base.json",
        }:
            expected_stats.update({
                "access_failed": status_counts["access_failed"],
                "asr_reviewed_deep": sum(
                    1 for entry in local_entries
                    if entry.get("distillation_status") == "deep_distilled"
                    and entry.get("evidence_status") == "asr_reviewed"
                ),
                "asr_reviewed_claim_only": sum(
                    1 for entry in local_entries
                    if entry.get("distillation_status") == "claim_evidence_only"
                    and entry.get("evidence_status") == "asr_reviewed_dialogue_only"
                ),
                "executable_procedure_units_unique": len(local_refs),
                "claims_in_deep_evidence": sum(
                    asr_claim_counts[str(entry.get("video_id", ""))]
                    for entry in local_entries
                    if entry.get("distillation_status") == "deep_distilled"
                ),
            })
            if kb_path.name == "targeted-foundation-video-knowledge-base.json":
                expected_stats.update({
                    "visual_frame_verification_complete_for_sampled_claims": sum(
                        1 for entry in local_entries
                        if entry.get("visual_verification_status")
                        == "complete_for_sampled_claims"
                    ),
                    "visual_frame_verification_partial": sum(
                        1 for entry in local_entries
                        if entry.get("visual_verification_status") == "partial"
                    ),
                })
        elif kb_path.name == "script-screen-video-knowledge-base.json":
            expected_stats.update({
                "executable_procedure_units_unique": len(local_refs),
                "claims_in_deep_evidence": sum(
                    asr_claim_counts[str(entry.get("video_id", ""))]
                    for entry in local_entries
                    if entry.get("distillation_status") == "deep_distilled"
                ),
                "visual_frame_verification_complete_for_sampled_claims": sum(
                    1 for entry in local_entries
                    if entry.get("visual_verification_status")
                    == "complete_for_sampled_claims"
                ),
                "visual_frame_verification_partial": sum(
                    1 for entry in local_entries
                    if entry.get("visual_verification_status") == "partial"
                ),
                "public_script_and_film_evidence_gate": sum(
                    1 for entry in local_entries
                    if isinstance(entry.get("script_source"), dict)
                    and entry["script_source"].get("access_status") == "public_no_login"
                    and entry.get("frame_artifact")
                ),
            })
        elif kb_path.name == "ai-video-source-knowledge-base.json":
            expected_stats.update({
                "claims": sum(
                    asr_claim_counts[str(entry.get("video_id", ""))]
                    for entry in local_entries
                ),
                "executable_procedure_units_unique": len(local_refs),
                "visual_frame_verification_complete_for_sampled_claims": sum(
                    1 for entry in local_entries
                    if entry.get("visual_verification_status") == "complete_for_sampled_claims"
                ),
            })
        for field, expected in expected_stats.items():
            actual = stats.get(field) if isinstance(stats, dict) else None
            if actual != expected:
                errors.append(f"{kb_path.name}.corpus_stats.{field}: expected {expected}, got {actual}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "OK: "
        f"{len(entries)} unique videos across {len(knowledge_bases)} knowledge bases, "
        f"{len(deep_video_ids)} deep-distilled videos, "
        f"{len(procedure_sources)} uniquely referenced procedures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
