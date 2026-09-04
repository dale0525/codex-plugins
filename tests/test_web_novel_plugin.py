from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.sync_external_content import _normalize_skill_text, load_config


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
    "humanizer-zh",
}

INDEPENDENT_SKILLS = {"humanizer-zh"}

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
        self.assertEqual(manifest["version"], "0.3.1")
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

    def test_humanizer_is_explicit_and_preserves_source_facts(self) -> None:
        skill_root = SKILLS / "humanizer-zh"
        text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        policy = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("仅在用户明确要求去除 AI 痕迹", text)
        self.assertIn("不新增原文没有的事实", text)
        self.assertIn("allow_implicit_invocation: false", policy)
        self.assertNotIn("软件更新添加了批处理、键盘快捷键和离线模式", text)
        self.assertNotIn("开发社区有一半人疯了", text)
        self.assertNotIn("沉重的节拍增加了攻击性的基调", text)
        for unsupported in (
            "每周集市和 18 世纪教堂",
            "中国科学院 2019 年的调查",
            "根据注册文件，该公司成立于 1994 年",
            "计划明年再开设两个地点",
        ):
            self.assertNotIn(unsupported, text)
        self.assertFalse((skill_root / "README.md").exists())

    def test_orchestrated_humanizer_and_effect_contract_boundaries_are_persistent(self) -> None:
        humanizer = (SKILLS / "humanizer-zh" / "SKILL.md").read_text(encoding="utf-8")
        sync_config = (ROOT / "sync-sources.toml").read_text(encoding="utf-8")
        routing = (SHARED / "references" / "web-novel-routing.md").read_text(
            encoding="utf-8"
        )
        effect_contract = (
            SHARED / "references" / "narrative-effect-contract.md"
        ).read_text(encoding="utf-8")

        for required in (
            "orchestrated_fiction_edit",
            "语义等价的表达层最小修改",
            "保留有功能的排比/反复/破折号/金句/残句",
            "退回 `provider`",
            "没有可安全修改的片段就保持原文",
        ):
            with self.subTest(required=required):
                self.assertIn(required, humanizer)
                self.assertIn(required, sync_config)
        self.assertNotIn("重写每个有问题的部分", humanizer)
        self.assertNotIn("effect_contract:", routing)
        self.assertIn("不要求作者或调用方预填", routing)
        self.assertIn("低选择过渡", effect_contract)
        self.assertIn("不强造一次重大选择", effect_contract)

    def test_humanizer_sync_transform_rebuilds_the_orchestration_boundary(self) -> None:
        spec = next(
            item
            for item in load_config(ROOT / "sync-sources.toml", ROOT)
            if item.source_id == "humanizer-zh"
        )
        replacements = [
            (find, replace)
            for skill, find, replace in spec.skill_text_replacements
            if skill == "humanizer-zh"
        ]
        self.assertGreater(len(replacements), 10)

        with tempfile.TemporaryDirectory(prefix="humanizer-transform-test-") as temporary:
            skill_path = Path(temporary) / "humanizer-zh" / "SKILL.md"
            skill_path.parent.mkdir(parents=True)
            skill_path.write_text(
                "---\nname: humanizer-zh\ndescription: fixture\n---\n\n"
                + "\n\n".join(find for find, _ in replacements),
                encoding="utf-8",
            )

            _normalize_skill_text(
                skill_path,
                spec.skill_text_replacements,
                "humanizer-zh",
            )
            transformed = skill_path.read_text(encoding="utf-8")
            for find, replace in replacements:
                with self.subTest(find=find[:40]):
                    self.assertNotIn(find, transformed)
                    self.assertIn(replace, transformed)
            self.assertIn("orchestrated_fiction_edit", transformed)
            self.assertIn("没有可安全修改的片段就保持原文", transformed)
            self.assertNotIn("重写每个有问题的部分", transformed)

            # A daily sync may apply the same repository transform again; the
            # result must remain byte-identical rather than stacking policy text.
            before_second_pass = skill_path.read_bytes()
            _normalize_skill_text(
                skill_path,
                spec.skill_text_replacements,
                "humanizer-zh",
            )
            self.assertEqual(skill_path.read_bytes(), before_second_pass)

    def test_focused_skills_resolve_shared_root(self) -> None:
        for skill_name in EXPECTED_SKILLS - {"web-novel-craft"} - INDEPENDENT_SKILLS:
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
