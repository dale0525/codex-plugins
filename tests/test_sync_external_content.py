from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

from scripts.sync_external_content import (
    SyncError,
    load_config,
    load_github_release_config,
    synchronize,
)


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
            "allowed-tools:\n"
            "  - Read\n"
            "  - Write\n"
            "metadata:\n"
            "  trigger: test synchronization\n"
            "  source: fixture\n"
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
        local_policy = self.root / "plugins/apple-design/skills/example-skill/agents/openai.yaml"
        local_policy.parent.mkdir(parents=True)
        local_policy.write_text(
            "interface:\n"
            "  display_name: \"Repository Example\"\n"
            "  short_description: \"Repository-owned policy\"\n"
            "  default_prompt: \"Use $example-skill only when explicitly invoked.\"\n"
            "policy:\n"
            "  allow_implicit_invocation: false\n",
            encoding="utf-8",
        )
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
            "remove_skill_frontmatter_fields = [\"disable-model-invocation\", \"allowed-tools\", \"metadata\"]\n"
            "skill_description_suffixes = { example-skill = \"Use only when explicitly requested.\" }\n"
            "skill_implicit_invocation = { example-skill = false }\n"
            "skill_text_replacements = [{ skill = \"example-skill\", find = \"# Example\", replace = \"# Normalized Example\" }]\n",
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

    def _release_fixture(self):
        manifest = self.root / "plugins/fastctx/.codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"name": "fastctx", "version": "0.1.0"}) + "\n",
            encoding="utf-8",
        )
        asset_name = "fastctx-test-target.tar.gz"
        asset_content = b"reviewed release archive"
        asset_digest = __import__("hashlib").sha256(asset_content).hexdigest()
        checksums = f"{asset_digest}  {asset_name}\n".encode()
        checksum_digest = __import__("hashlib").sha256(checksums).hexdigest()
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n[[github_releases]]\n"
                'id = "fastctx-release"\n'
                'repository = "yc-duan/fastctx"\n'
                'metadata_destination = "plugins/fastctx/upstream-release.json"\n'
                'plugin_manifest = "plugins/fastctx/.codex-plugin/plugin.json"\n'
                'checksum_asset = "SHA256SUMS"\n'
                f'required_assets = ["{asset_name}"]\n'
            )
        release = {
            "id": 7,
            "tag_name": "v0.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-07-25T20:47:52Z",
            "assets": [
                {
                    "name": asset_name,
                    "browser_download_url": "https://download.example/asset",
                    "size": len(asset_content),
                    "digest": f"sha256:{asset_digest}",
                },
                {
                    "name": "SHA256SUMS",
                    "browser_download_url": "https://download.example/checksums",
                    "size": len(checksums),
                    "digest": f"sha256:{checksum_digest}",
                },
            ],
        }

        def github_json(url: str):
            if "/releases?" in url:
                return [release]
            if "/git/ref/tags/" in url:
                return {"object": {"type": "commit", "sha": "a" * 40}}
            raise AssertionError(url)

        def download(url: str, destination: Path) -> bytes:
            content = checksums if url.endswith("checksums") else asset_content
            destination.write_bytes(content)
            return content

        return manifest, asset_digest, github_json, download

    def test_sync_normalizes_bumps_and_is_idempotent(self) -> None:
        self.assertTrue(synchronize(self.config, self.lock))
        skill = self.root / "plugins/apple-design/skills/example-skill/SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertNotIn("disable-model-invocation", skill.read_text(encoding="utf-8"))
        frontmatter = skill.read_text(encoding="utf-8").split("---", 2)[1]
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(parsed["name"], "example-skill")
        self.assertNotIn("allowed-tools", frontmatter)
        self.assertNotIn("metadata:", frontmatter)
        self.assertNotIn("trigger: test synchronization", frontmatter)
        self.assertIn(
            "Use only when explicitly requested.",
            skill.read_text(encoding="utf-8"),
        )
        self.assertIn("# Normalized Example", skill.read_text(encoding="utf-8"))
        policy = skill.parent / "agents/openai.yaml"
        policy_text = policy.read_text(encoding="utf-8")
        self.assertIn("Repository-owned policy", policy_text)
        self.assertIn("allow_implicit_invocation: false", policy_text)
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

    def test_root_skill_uses_destination_name_for_policy(self) -> None:
        standalone = self.upstream / "standalone"
        standalone.mkdir()
        (standalone / "SKILL.md").write_text(
            "---\n"
            "name: root-skill\n"
            "description: Root-level skill synchronization test.\n"
            "---\n\n"
            "# Root skill\n",
            encoding="utf-8",
        )
        self._commit("Add root-level skill")

        manifest = self.root / "plugins/root-plugin/.codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps({"name": "root-plugin", "version": "0.1.0"}) + "\n",
            encoding="utf-8",
        )
        with self.config.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n[[sources]]\n"
                'id = "root-skill"\n'
                f'repository = "{self.upstream.as_uri()}"\n'
                'ref = "main"\n'
                'source = "standalone"\n'
                'destination = "plugins/root-plugin/skills/root-skill"\n'
                'plugin_manifest = "plugins/root-plugin/.codex-plugin/plugin.json"\n'
                'skill_implicit_invocation = { root-skill = true }\n'
            )

        self.assertTrue(synchronize(self.config, self.lock))
        policy = self.root / "plugins/root-plugin/skills/root-skill/agents/openai.yaml"
        self.assertTrue(policy.is_file())
        self.assertIn("allow_implicit_invocation: true", policy.read_text(encoding="utf-8"))

    def test_description_suffix_supports_yaml_block_scalar(self) -> None:
        upstream_skill = self.upstream / "skills/example-skill/SKILL.md"
        upstream_skill.write_text(
            "---\n"
            "name: example-skill\n"
            "description: |\n"
            "  First description line.\n"
            "  Second description line.\n"
            "metadata:\n"
            "  source: fixture\n"
            "---\n\n"
            "# Example\n",
            encoding="utf-8",
        )
        self._commit("Use a block scalar description")

        self.assertTrue(synchronize(self.config, self.lock))
        skill = self.root / "plugins/apple-design/skills/example-skill/SKILL.md"
        frontmatter = skill.read_text(encoding="utf-8").split("---", 2)[1]
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(
            parsed["description"],
            "First description line.\nSecond description line.\n"
            "Use only when explicitly requested.\n",
        )
        self.assertNotIn("metadata", parsed)

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

    def test_github_release_sync_verifies_assets_and_is_idempotent(self) -> None:
        manifest, asset_digest, github_json, download = self._release_fixture()

        with (
            patch("scripts.sync_external_content._github_json", side_effect=github_json),
            patch("scripts.sync_external_content._download", side_effect=download),
        ):
            self.assertTrue(synchronize(self.config, self.lock))
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["version"],
                "0.1.1",
            )
            metadata = json.loads(
                (self.root / "plugins/fastctx/upstream-release.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(metadata["version"], "0.2.3")
            self.assertEqual(metadata["assets"]["test-target"]["sha256"], asset_digest)
            self.assertFalse(synchronize(self.config, self.lock))

    def test_github_release_sync_ignores_newer_prerelease(self) -> None:
        _, _, github_json, download = self._release_fixture()
        stable = github_json("https://api.github.com/releases?per_page=100")[0]
        prerelease = dict(stable)
        prerelease.update(
            {
                "id": 8,
                "tag_name": "v0.2.4-rc.1",
                "prerelease": True,
                "published_at": "2026-07-26T20:47:52Z",
            }
        )

        def github_json_with_prerelease(url: str):
            if "/releases?" in url:
                return [prerelease, stable]
            return github_json(url)

        with (
            patch(
                "scripts.sync_external_content._github_json",
                side_effect=github_json_with_prerelease,
            ),
            patch("scripts.sync_external_content._download", side_effect=download),
        ):
            self.assertTrue(synchronize(self.config, self.lock))
        metadata = json.loads(
            (self.root / "plugins/fastctx/upstream-release.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["version"], "0.2.3")

    def test_github_release_rejects_mutated_locked_tag_without_writes(self) -> None:
        manifest, _, github_json, download = self._release_fixture()
        with (
            patch("scripts.sync_external_content._github_json", side_effect=github_json),
            patch("scripts.sync_external_content._download", side_effect=download),
        ):
            self.assertTrue(synchronize(self.config, self.lock))
        metadata = self.root / "plugins/fastctx/upstream-release.json"
        manifest_before = manifest.read_bytes()
        metadata_before = metadata.read_bytes()
        lock_data = json.loads(self.lock.read_text(encoding="utf-8"))
        release_entry = lock_data["sources"]["fastctx-release"]
        release_entry["release_id"] = 8
        self.lock.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")

        with (
            patch("scripts.sync_external_content._github_json", side_effect=github_json),
            patch("scripts.sync_external_content._download", side_effect=download),
        ):
            with self.assertRaisesRegex(SyncError, "changed after it was locked"):
                synchronize(self.config, self.lock)
        self.assertEqual(manifest.read_bytes(), manifest_before)
        self.assertEqual(metadata.read_bytes(), metadata_before)

    def test_release_config_rejects_checksum_as_required_asset(self) -> None:
        self._release_fixture()
        text = self.config.read_text(encoding="utf-8")
        self.config.write_text(
            text.replace(
                'required_assets = ["fastctx-test-target.tar.gz"]',
                'required_assets = ["fastctx-test-target.tar.gz", "SHA256SUMS"]',
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SyncError, "must not also appear"):
            load_github_release_config(self.config, self.root)


if __name__ == "__main__":
    unittest.main()
