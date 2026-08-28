from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillTriggerPolicyTests(unittest.TestCase):
    def test_high_risk_local_skills_are_explicit_only(self) -> None:
        paths = (
            "plugins/aihero-workflow/skills/grilling/agents/openai.yaml",
            "plugins/aihero-workflow/skills/improve-codebase-architecture/agents/openai.yaml",
            "plugins/codex-sync/skills/codex-sync/agents/openai.yaml",
            "plugins/film-craft-orchestrator/skills/film-craft-orchestrator/agents/openai.yaml",
            "plugins/web-novel-craft/skills/web-novel-craft/agents/openai.yaml",
            "plugins/web-novel-craft/skills/web-novel-evidence-research/agents/openai.yaml",
        )
        for relative in paths:
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("allow_implicit_invocation: false", text)

    def test_synced_apple_skills_declare_explicit_only_policy(self) -> None:
        for skill_name in (
            "emil-design-eng",
            "find-animation-opportunities",
            "improve-animations",
            "pick-ui-library",
            "prototype",
            "review-animations",
        ):
            path = ROOT / "plugins/apple-design/skills" / skill_name / "agents/openai.yaml"
            with self.subTest(skill_name=skill_name):
                self.assertIn("allow_implicit_invocation: false", path.read_text(encoding="utf-8"))

    def test_web_novel_child_references_resolve_from_shared_root(self) -> None:
        skills = ROOT / "plugins/web-novel-craft/skills"
        shared_root = skills / "web-novel-craft"
        for path in skills.glob("*/SKILL.md"):
            if path.parent.name == "web-novel-craft":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("`references/", text)
            self.assertNotIn("`scripts/", text)
            self.assertIn("../web-novel-craft/references/", text)
            self.assertTrue(shared_root.is_dir())

    def test_external_sync_rechecks_after_rebase_before_push(self) -> None:
        workflow = (ROOT / ".github/workflows/sync-external-content.yml").read_text(
            encoding="utf-8"
        )
        rebase = workflow.index("git pull --rebase")
        recheck = workflow.index("pixi run check", rebase)
        push = workflow.index("git push", recheck)
        self.assertLess(rebase, recheck)
        self.assertLess(recheck, push)


if __name__ == "__main__":
    unittest.main()
