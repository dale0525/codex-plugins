#!/usr/bin/env python3
"""Validate both frozen web-novel video corpora and distributable distillations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REFS = ROOT / "references"
NARRATIVE_EFFECT_CONTRACT = REFS / "narrative-effect-contract.md"
CREATIVE_SKILLS = (
    "web-novel-development",
    "web-novel-structure",
    "web-novel-characters",
    "web-novel-genre-craft",
    "web-novel-progression",
    "web-novel-prose-craft",
    "web-novel-revision",
)
DATASETS = (
    {
        "label": "base",
        "expected": 17,
        "manifest": "video-corpus-manifest.json",
        "evidence": "video-asr-evidence.json",
        "knowledge": "video-knowledge-base.json",
        "procedures": "distilled-procedures.json",
    },
    {
        "label": "extension",
        "expected": 20,
        "manifest": "video-corpus-extension-manifest.json",
        "evidence": "video-extension-asr-evidence.json",
        "knowledge": "video-extension-knowledge-base.json",
        "procedures": "distilled-extension-procedures.json",
    },
    {
        "label": "priority-234",
        "expected": 30,
        "manifest": "video-priority-234-manifest.json",
        "evidence": "video-priority-234-asr-evidence.json",
        "knowledge": "video-priority-234-knowledge-base.json",
        "procedures": "distilled-priority-234-procedures.json",
    },
)
CLAIM_TYPES = {
    "demonstration",
    "craft_model",
    "practitioner_experience",
    "platform_claim",
    "business_claim",
    "opinion",
}


def load(name: str) -> dict[str, Any]:
    path = REFS / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_creative_skill_structure(errors: list[str]) -> None:
    """Keep the shared narrative-effect contract discoverable from every creative skill."""

    require(
        NARRATIVE_EFFECT_CONTRACT.is_file(),
        "shared narrative-effect contract is missing",
        errors,
    )
    reference = "../web-novel-craft/references/narrative-effect-contract.md"
    for skill_name in CREATIVE_SKILLS:
        skill_path = ROOT.parent / skill_name / "SKILL.md"
        if not skill_path.is_file():
            require(False, f"creative skill is missing: {skill_path}", errors)
            continue
        try:
            text = skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            require(False, f"cannot read creative skill {skill_path}: {exc}", errors)
            continue
        require(
            reference in text,
            f"{skill_name}: must reference {reference}",
            errors,
        )


def by_id(
    items: list[dict[str, Any]], label: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        item_id = item.get("id")
        require(isinstance(item_id, str) and bool(item_id), f"{label}[{index}] has no id", errors)
        if not isinstance(item_id, str) or not item_id:
            continue
        require(item_id not in result, f"duplicate {label} id: {item_id}", errors)
        result[item_id] = item
    return result


def validate_coverage(
    prefix: str,
    evidence: dict[str, Any],
    knowledge: dict[str, Any],
    errors: list[str],
) -> None:
    duration = float(evidence.get("media_duration_sec", 0))
    coverage_end = float(evidence.get("coverage_end_sec", 0))
    status = evidence.get("coverage_status")
    require(duration > 0 and coverage_end > 0, f"{prefix}: invalid coverage bounds", errors)
    if status == "complete_speech_track":
        require(coverage_end >= duration - 15, f"{prefix}: coverage does not reach ending", errors)
        required_review_end = duration - 15
    elif status == "incomplete_media_tail_explicitly_excluded":
        exception = evidence.get("coverage_exception", {})
        speech_end = float(evidence.get("speech_track_end_sec", 0))
        require(abs(speech_end - coverage_end) <= 1, f"{prefix}: speech-track endpoint mismatch", errors)
        require(float(exception.get("start_sec", -1)) >= coverage_end - 1, f"{prefix}: gap start", errors)
        require(float(exception.get("end_sec", -1)) >= duration - 1, f"{prefix}: gap end", errors)
        require(bool(exception.get("reason")), f"{prefix}: missing gap reason", errors)
        require(bool(exception.get("claim_policy")), f"{prefix}: missing gap claim policy", errors)
        required_review_end = coverage_end - 1
    else:
        require(False, f"{prefix}: unsupported coverage status {status!r}", errors)
        required_review_end = coverage_end
    require(
        float(knowledge.get("reviewed_to_sec", 0)) >= required_review_end,
        f"{prefix}: review did not reach covered endpoint",
        errors,
    )
    for index, claim in enumerate(knowledge.get("claims", [])):
        timestamp = float(claim.get("timestamp_sec", -1))
        require(timestamp <= coverage_end, f"{prefix} claim {index}: timestamp lies in uncovered media", errors)


def validate_dataset(spec: dict[str, Any], errors: list[str]) -> set[str]:
    label = str(spec["label"])
    manifest = load(str(spec["manifest"]))
    evidence = load(str(spec["evidence"]))
    knowledge = load(str(spec["knowledge"]))
    procedures = load(str(spec["procedures"]))

    sources = by_id(manifest.get("sources", []), f"{label} manifest source", errors)
    evidence_by_id = by_id(evidence.get("sources", []), f"{label} evidence source", errors)
    knowledge_by_id = by_id(knowledge.get("sources", []), f"{label} knowledge source", errors)
    procedures_by_id = by_id(procedures.get("procedures", []), f"{label} procedure", errors)

    require(manifest.get("status") == "deep_distilled", f"{label}: manifest status", errors)
    require(len(sources) == spec["expected"], f"{label}: expected {spec['expected']} sources, found {len(sources)}", errors)
    require(set(sources) == set(evidence_by_id), f"{label}: manifest/evidence IDs differ", errors)
    require(set(sources) == set(knowledge_by_id), f"{label}: manifest/knowledge IDs differ", errors)
    require(bool(procedures_by_id), f"{label}: no distilled procedures", errors)

    covered_by_procedure: set[str] = set()
    for source_id, source in sources.items():
        prefix = f"{label} source {source_id}"
        require(source.get("required_status") == "deep_distilled", f"{prefix}: required_status", errors)
        require(source.get("status") == "deep_distilled", f"{prefix}: status", errors)
        ev = evidence_by_id.get(source_id, {})
        require(isinstance(ev.get("segment_count"), int) and ev.get("segment_count", 0) > 0, f"{prefix}: segment count", errors)
        require(bool(ev.get("transcript_sha256")), f"{prefix}: transcript hash", errors)
        require(ev.get("raw_transcript_distributed") is False, f"{prefix}: transcript distribution flag", errors)

        kb = knowledge_by_id.get(source_id, {})
        require(kb.get("status") == "deep_distilled", f"{prefix}: knowledge status", errors)
        require(kb.get("full_review_complete") is True, f"{prefix}: full review flag", errors)
        claims = kb.get("claims", [])
        require(isinstance(claims, list) and len(claims) >= 5, f"{prefix}: fewer than five claims", errors)
        for index, claim in enumerate(claims if isinstance(claims, list) else []):
            cp = f"{prefix} claim {index}"
            require(isinstance(claim.get("timestamp_sec"), (int, float)), f"{cp}: timestamp", errors)
            require(claim.get("type") in CLAIM_TYPES, f"{cp}: type", errors)
            require(claim.get("level") in {"E1", "E2", "E3", "E4"}, f"{cp}: level", errors)
            require(isinstance(claim.get("paraphrase"), str) and len(claim.get("paraphrase", "")) >= 12, f"{cp}: paraphrase", errors)
        for field in ("applicability", "non_applicability", "failure_boundaries", "counterexamples", "ambiguities"):
            require(isinstance(kb.get(field), list) and bool(kb.get(field)), f"{prefix}: missing {field}", errors)
        procedure_ids = kb.get("procedure_ids", [])
        require(isinstance(procedure_ids, list) and bool(procedure_ids), f"{prefix}: no procedure", errors)
        for procedure_id in procedure_ids if isinstance(procedure_ids, list) else []:
            require(procedure_id in procedures_by_id, f"{prefix}: unknown procedure {procedure_id}", errors)
        validate_coverage(prefix, ev, kb, errors)

    for procedure_id, procedure in procedures_by_id.items():
        prefix = f"{label} procedure {procedure_id}"
        source_ids = procedure.get("source_ids", [])
        require(isinstance(source_ids, list) and bool(source_ids), f"{prefix}: no sources", errors)
        for source_id in source_ids if isinstance(source_ids, list) else []:
            require(source_id in sources, f"{prefix}: unknown source {source_id}", errors)
            covered_by_procedure.add(source_id)
        require(bool(procedure.get("trigger")), f"{prefix}: trigger", errors)
        require(isinstance(procedure.get("inputs"), list) and bool(procedure.get("inputs")), f"{prefix}: inputs", errors)
        steps = procedure.get("steps", [])
        require(isinstance(steps, list) and bool(steps), f"{prefix}: steps", errors)
        for index, step in enumerate(steps if isinstance(steps, list) else []):
            require(bool(step.get("action")) and bool(step.get("check")), f"{prefix}: step {index}", errors)
        for field in ("output", "failure_signals", "repair", "acceptance", "example", "counterexample"):
            require(bool(procedure.get(field)), f"{prefix}: missing {field}", errors)
    require(set(sources) == covered_by_procedure, f"{label}: every source must support a procedure", errors)
    return set(sources)


def validate() -> list[str]:
    errors: list[str] = []
    validate_creative_skill_structure(errors)
    all_ids: set[str] = set()
    total = 0
    for spec in DATASETS:
        ids = validate_dataset(spec, errors)
        require(not all_ids.intersection(ids), f"duplicate IDs across corpora: {sorted(all_ids.intersection(ids))}", errors)
        all_ids.update(ids)
        total += int(spec["expected"])
    require(len(all_ids) == total == 67, f"combined corpus must contain 67 unique sources, found {len(all_ids)}", errors)
    return errors


def main() -> int:
    try:
        errors = validate()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Corpus valid: base 17/17, extension 20/20, priority-234 30/30; combined 67/67 deeply distilled sources.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
