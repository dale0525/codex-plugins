from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "film-craft-orchestrator"
SKILLS = PLUGIN / "skills"

EXPECTED_SKILLS = {
    "film-craft-orchestrator",
    "film-adaptation",
    "screenwriting",
    "directing",
    "cinematography",
    "ai-video-production",
    "continuity-qc",
    "editing-sound",
}


class FilmCraftPluginTests(unittest.TestCase):
    def test_manifest_and_skill_set_are_complete(self) -> None:
        manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "film-craft-orchestrator")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertIn("Seven focused skills", manifest["interface"]["longDescription"])
        actual = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(actual, EXPECTED_SKILLS)
        self.assertFalse((SKILLS / "video-evidence-research").exists())

    def test_shared_runtime_has_one_canonical_home(self) -> None:
        orchestrator = SKILLS / "film-craft-orchestrator"
        for resource in ("references", "scripts", "assets", "tests"):
            self.assertTrue((orchestrator / resource).is_dir(), resource)
        for skill_name in EXPECTED_SKILLS - {"film-craft-orchestrator"}:
            skill = SKILLS / skill_name
            for resource in ("references", "scripts", "assets", "tests"):
                self.assertFalse((skill / resource).exists(), f"{skill_name}/{resource}")

    def test_orchestrator_routes_every_focused_skill(self) -> None:
        text = (SKILLS / "film-craft-orchestrator" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for skill_name in EXPECTED_SKILLS - {"film-craft-orchestrator"}:
            self.assertIn(f"${skill_name}", text)
        self.assertNotIn("video-evidence-research", text)

    def test_film_tree_is_not_an_external_sync_destination(self) -> None:
        sync_config = (ROOT / "sync-sources.toml").read_text(encoding="utf-8")
        self.assertNotIn("film-craft-orchestrator", sync_config)
        self.assertNotIn("video-evidence-research", sync_config)

    def test_focused_skills_resolve_the_shared_root(self) -> None:
        shared_root = SKILLS / "film-craft-orchestrator"
        for skill_name in EXPECTED_SKILLS - {"film-craft-orchestrator"}:
            resolved = (SKILLS / skill_name / "../film-craft-orchestrator").resolve()
            self.assertEqual(resolved, shared_root.resolve())

            text = (SKILLS / skill_name / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("`references/", text)
            self.assertNotIn("`scripts/", text)
            self.assertNotIn("`assets/", text)
            self.assertNotIn("python scripts/", text)
            self.assertIn("../film-craft-orchestrator/references/", text)


if __name__ == "__main__":
    unittest.main()
