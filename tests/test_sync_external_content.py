from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.sync_external_content import SyncError, load_config, synchronize


class ExternalContentSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="external-sync-test-")
        self.root = Path(self.temporary.name) / "marketplace"
        self.upstream = Path(self.temporary.name) / "upstream"
        self.root.mkdir()
        self.upstream.mkdir()
        self._git("init", "--initial-branch=main")
        skill = self.upstream / "skills/example-skill/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\n"
            "name: example-skill\n"
            "description: Example skill for synchronization tests.\n"
            "disable-model-invocation: true\n"
            "---\n\n"
            "# Example\n",
            encoding="utf-8",
        )
        (self.upstream / "LICENSE").write_text("Example license\n", encoding="utf-8")
        self._commit("Initial upstream")

        manifest = self.root / "plugins/apple-design/.codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "apple-design", "version": "1.2.3"}) + "\n",
            encoding="utf-8",
        )
        stale_skill = self.root / "plugins/apple-design/skills/stale/SKILL.md"
        stale_skill.parent.mkdir(parents=True)
        stale_skill.write_text("stale\n", encoding="utf-8")
        self.config = self.root / "sync-sources.toml"
        self.lock = self.root / "sync-lock.json"
        self.config.write_text(
            "version = 1\n\n"
            "[[sources]]\n"
            "id = \"example-skills\"\n"
            f"repository = \"{self.upstream.as_uri()}\"\n"
            "ref = \"main\"\n"
            "source = \"skills\"\n"
            "destination = \"plugins/apple-design/skills\"\n"
            "plugin_manifest = \"plugins/apple-design/.codex-plugin/plugin.json\"\n"
            "license_source = \"LICENSE\"\n"
            "license_destination = \"plugins/apple-design/third-party/upstream-LICENSE\"\n"
            "remove_skill_frontmatter_fields = [\"disable-model-invocation\"]\n"
            "skill_description_suffixes = { example-skill = \"Use only when explicitly requested.\" }\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.upstream), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def _commit(self, message: str) -> None:
        self._git("add", ".")
        self._git(
            "-c",
            "user.name=Logic Tan",
            "-c",
            "user.email=logictan89@gmail.com",
            "commit",
            "-m",
            message,
        )

    def _version(self) -> str:
        manifest = self.root / "plugins/apple-design/.codex-plugin/plugin.json"
        return json.loads(manifest.read_text(encoding="utf-8"))["version"]

    def test_sync_normalizes_bumps_and_is_idempotent(self) -> None:
        self.assertTrue(synchronize(self.config, self.lock))
        skill = self.root / "plugins/apple-design/skills/example-skill/SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertNotIn("disable-model-invocation", skill.read_text(encoding="utf-8"))
        self.assertIn(
            "Use only when explicitly requested.",
            skill.read_text(encoding="utf-8"),
        )
        self.assertFalse((skill.parent.parent / "stale").exists())
        self.assertEqual(self._version(), "1.2.4")
        self.assertEqual(
            (
                self.root
                / "plugins/apple-design/third-party/upstream-LICENSE"
            ).read_text(encoding="utf-8"),
            "Example license\n",
        )

        self.assertFalse(synchronize(self.config, self.lock))
        self.assertEqual(self._version(), "1.2.4")

        upstream_skill = self.upstream / "skills/example-skill/SKILL.md"
        with upstream_skill.open("a", encoding="utf-8") as handle:
            handle.write("\nUpdated upstream.\n")
        self._commit("Update upstream")

        self.assertTrue(synchronize(self.config, self.lock))
        self.assertEqual(self._version(), "1.2.5")

    def test_config_rejects_destination_escape(self) -> None:
        text = self.config.read_text(encoding="utf-8")
        self.config.write_text(
            text.replace(
                'destination = "plugins/apple-design/skills"',
                'destination = "../outside"',
            ),
            encoding="utf-8",
        )
        with self.assertRaises(SyncError):
            load_config(self.config, self.root)


if __name__ == "__main__":
    unittest.main()
