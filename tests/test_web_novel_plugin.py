from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "web-novel-craft"
SKILLS = PLUGIN / "skills"
SHARED = SKILLS / "web-novel-craft"

EXPECTED_SKILLS = {
    "web-novel-craft",
    "web-novel-development",
    "web-novel-structure",
    "web-novel-characters",
    "web-novel-genre-craft",
    "web-novel-progression",
    "web-novel-prose-craft",
    "web-novel-revision",
    "web-novel-evidence-research",
}

REMOVED_MANAGEMENT_FILES = {
    "ai-canonical-state.md",
    "ai-evaluation.md",
    "ai-rights-and-disclosure.md",
    "ai-web-novel-workflow.md",
    "artifact-contracts.md",
    "orchestration-guardrails.md",
    "production.md",
    "serialization.md",
}

RESEARCH_SCRIPTS = {
    "compile_evidence_index.py",
    "normalize_youtube_json3.py",
    "transcribe_media.py",
    "validate_corpus.py",
    "verify_research_workspace.py",
}


class WebNovelPluginTests(unittest.TestCase):
    def test_manifest_and_skill_set_are_complete(self) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "web-novel-craft")
        self.assertEqual(manifest["author"]["name"], "Logic Tan")
        self.assertEqual(manifest["version"], "0.2.2")
        self.assertEqual(manifest["skills"], "./skills/")
        actual = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(actual, EXPECTED_SKILLS)

    def test_shared_resources_have_one_home(self) -> None:
        for resource in ("references", "scripts"):
            self.assertTrue((SHARED / resource).is_dir(), resource)
        for skill_name in EXPECTED_SKILLS - {"web-novel-craft"}:
            skill = SKILLS / skill_name
            for resource in ("references", "scripts", "assets", "tests"):
                self.assertFalse((skill / resource).exists(), f"{skill_name}/{resource}")

    def test_orchestrator_routes_every_focused_skill(self) -> None:
        text = (SHARED / "SKILL.md").read_text(encoding="utf-8")
        for skill_name in EXPECTED_SKILLS - {"web-novel-craft"}:
            self.assertIn(f"${skill_name}", text)

    def test_focused_skills_resolve_shared_root(self) -> None:
        for skill_name in EXPECTED_SKILLS - {"web-novel-craft"}:
            text = (SKILLS / skill_name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("../web-novel-craft/", text)
            resolved = (SKILLS / skill_name / "../web-novel-craft").resolve()
            self.assertEqual(resolved, SHARED.resolve())

    def test_chapter_length_contract_is_shared_by_chapter_workflows(self) -> None:
        workflow = (SHARED / "references" / "writer-workflow.md").read_text(
            encoding="utf-8"
        )
        for required_rule in (
            "2500–2800 字",
            "原则上不得少于 2500 字",
            "序章、尾声和番外",
            "不计标题、标点、空格、章节说明或其他元数据",
            "已有 2380 字",
            "预计会超过 2800 字",
            "局部场景或句段交付不单独套用整章字数",
        ):
            self.assertIn(required_rule, workflow)

        for skill_name in (
            "web-novel-craft",
            "web-novel-structure",
            "web-novel-prose-craft",
            "web-novel-revision",
        ):
            skill = (SKILLS / skill_name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("writer-workflow.md", skill)

    def test_project_management_layer_is_absent(self) -> None:
        actual_skills = {path.name for path in SKILLS.iterdir() if path.is_dir()}
        self.assertNotIn("web-novel-ai-production", actual_skills)
        self.assertNotIn("web-novel-production", actual_skills)
        self.assertNotIn("web-novel-serialization", actual_skills)
        references = SHARED / "references"
        for filename in REMOVED_MANAGEMENT_FILES:
            self.assertFalse((references / filename).exists(), filename)
        self.assertFalse((references / "fanfiction-canon-rights.md").exists())
        actual_scripts = {
            path.name for path in (SHARED / "scripts").iterdir() if path.is_file()
        }
        self.assertEqual(actual_scripts, RESEARCH_SCRIPTS)

    def test_frozen_corpus_passes_strict_validator(self) -> None:
        script = SHARED / "scripts" / "validate_corpus.py"
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("67/67", result.stdout)

    def test_raw_transcripts_are_not_distributed(self) -> None:
        references = SHARED / "references"
        self.assertFalse((SHARED / "transcripts").exists())
        for filename in (
            "video-asr-evidence.json",
            "video-extension-asr-evidence.json",
            "video-priority-234-asr-evidence.json",
        ):
            evidence = json.loads((references / filename).read_text(encoding="utf-8"))
            for source in evidence["sources"]:
                self.assertIs(source["raw_transcript_distributed"], False)
                self.assertNotIn("segments", source)
                self.assertNotIn("text", source)

    def test_extension_tail_gap_is_explicit_and_claim_free(self) -> None:
        references = SHARED / "references"
        evidence = json.loads(
            (references / "video-extension-asr-evidence.json").read_text(encoding="utf-8")
        )
        knowledge = json.loads(
            (references / "video-extension-knowledge-base.json").read_text(encoding="utf-8")
        )
        gaps = [
            item
            for item in evidence["sources"]
            if item["coverage_status"] != "complete_speech_track"
        ]
        self.assertEqual([item["id"] for item in gaps], ["FyRNX51MI3g"])
        gap = gaps[0]
        self.assertTrue(gap["coverage_exception"]["reason"])
        self.assertTrue(gap["coverage_exception"]["claim_policy"])
        source = next(item for item in knowledge["sources"] if item["id"] == gap["id"])
        self.assertLessEqual(
            max(claim["timestamp_sec"] for claim in source["claims"]),
            gap["coverage_end_sec"],
        )


if __name__ == "__main__":
    unittest.main()
