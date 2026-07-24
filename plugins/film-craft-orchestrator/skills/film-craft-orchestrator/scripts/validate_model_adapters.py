#!/usr/bin/env python3
"""Validate versioned AI video model capability adapters."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ai_video_common import die_on_errors, load_json, require_fields


REQUIRED = [
    "id", "provider", "model_family", "model_versions", "api_surface",
    "status", "last_verified_at", "capabilities", "limits", "official_sources",
    "official_evidence_refs",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("adapters", type=Path)
    parser.add_argument("--official-evidence", type=Path)
    args = parser.parse_args()
    data = load_json(args.adapters)
    evidence_path = args.official_evidence or args.adapters.parent / "ai-video-official-evidence.json"
    evidence = load_json(evidence_path)
    errors: list[str] = []
    evidence_sources = evidence.get("sources") if isinstance(evidence, dict) else None
    if not isinstance(evidence_sources, list) or not evidence_sources:
        errors.append("official evidence: sources must be a non-empty list")
        evidence_sources = []
    evidence_by_id: dict[str, dict] = {}
    for index, source in enumerate(evidence_sources):
        label = f"official_evidence.sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{label}: must be an object")
            continue
        require_fields(
            source,
            ["id", "publisher", "title", "url", "source_type", "status", "accessed_at", "adapter_refs", "claims"],
            label,
            errors,
        )
        source_id = str(source.get("id", ""))
        if source_id in evidence_by_id:
            errors.append(f"{label}: duplicate id {source_id}")
        else:
            evidence_by_id[source_id] = source
    adapters = data.get("adapters") if isinstance(data, dict) else None
    if not isinstance(adapters, list) or not adapters:
        errors.append("root: adapters must be a non-empty list")
        return die_on_errors(errors)
    ids: set[str] = set()
    for index, adapter in enumerate(adapters):
        label = f"adapters[{index}]"
        if not isinstance(adapter, dict):
            errors.append(f"{label}: must be an object")
            continue
        require_fields(adapter, REQUIRED, label, errors)
        adapter_id = str(adapter.get("id", ""))
        if adapter_id in ids:
            errors.append(f"{label}: duplicate id {adapter_id}")
        ids.add(adapter_id)
        versions = adapter.get("model_versions")
        if not isinstance(versions, list) or not versions:
            errors.append(f"{label}: model_versions must be non-empty list")
        capabilities = adapter.get("capabilities")
        if not isinstance(capabilities, dict) or not capabilities:
            errors.append(f"{label}: capabilities must be non-empty object")
        sources = adapter.get("official_sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{label}: at least one official source is required")
        else:
            for source_index, source in enumerate(sources):
                source_label = f"{label}.official_sources[{source_index}]"
                if not isinstance(source, dict):
                    errors.append(f"{source_label}: must be object")
                    continue
                require_fields(source, ["title", "url", "accessed_at", "type"], source_label, errors)
                if source.get("url") and not str(source["url"]).startswith("https://"):
                    errors.append(f"{source_label}: url must use https")
        evidence_refs = adapter.get("official_evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            errors.append(f"{label}: official_evidence_refs must be non-empty list")
        else:
            for evidence_ref in evidence_refs:
                evidence_source = evidence_by_id.get(str(evidence_ref))
                if evidence_source is None:
                    errors.append(f"{label}: unknown official_evidence_ref {evidence_ref}")
                elif adapter_id not in evidence_source.get("adapter_refs", []):
                    errors.append(
                        f"{label}: evidence {evidence_ref} does not point back to {adapter_id}"
                    )
        verified = adapter.get("last_verified_at")
        try:
            if verified:
                date.fromisoformat(str(verified))
        except ValueError:
            errors.append(f"{label}: last_verified_at must be YYYY-MM-DD")
        deprecation = adapter.get("deprecation", {})
        if adapter.get("status") == "deprecated_available":
            if not isinstance(deprecation, dict) or not deprecation.get("shutdown_at"):
                errors.append(f"{label}: deprecated adapter requires shutdown_at")
    for source_id, source in evidence_by_id.items():
        for adapter_ref in source.get("adapter_refs", []):
            if str(adapter_ref) not in ids:
                errors.append(f"official evidence {source_id}: unknown adapter_ref {adapter_ref}")
        if not isinstance(source.get("claims"), list) or not source.get("claims"):
            errors.append(f"official evidence {source_id}: claims must be non-empty")
    stats = evidence.get("stats", {}) if isinstance(evidence, dict) else {}
    expected_stats = {
        "sources": len(evidence_sources),
        "official_verified": sum(
            1 for source in evidence_sources
            if isinstance(source, dict) and source.get("status") == "official_verified"
        ),
        "official_video_metadata": sum(
            1 for source in evidence_sources
            if isinstance(source, dict) and source.get("status") == "official_video_metadata"
        ),
        "claims": sum(
            len(source.get("claims", [])) for source in evidence_sources
            if isinstance(source, dict) and isinstance(source.get("claims"), list)
        ),
    }
    for field, expected in expected_stats.items():
        if stats.get(field) != expected:
            errors.append(f"official evidence stats.{field}: expected {expected}, got {stats.get(field)}")
    result = die_on_errors(errors)
    if not result:
        print(f"OK: {len(adapters)} model adapters with official sources")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
