from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_repository


class RepositorySyncMetadataValidationTests(unittest.TestCase):
    def test_creative_model_bridge_one_shot_script_contract_is_checked(self) -> None:
        plugin = Path(__file__).resolve().parents[1] / "plugins/creative-model-bridge"
        manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        validation = validate_repository.Validation()
        with patch.object(validate_repository, "ROOT", plugin.parents[1]):
            validate_repository._validate_creative_script(plugin, manifest, validation)
        self.assertEqual(validation.errors, [])

        skill = plugin / "skills/creative-model-bridge/SKILL.md"
        original = skill.read_text(encoding="utf-8")
        for marker in validate_repository.CREATIVE_SKILL_REQUIRED_MARKERS:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(
                prefix="repository-validator-test-"
            ) as temporary:
                root = Path(temporary)
                copied_plugin = root / "plugins/creative-model-bridge"
                shutil.copytree(plugin, copied_plugin)
                copied_skill = copied_plugin / "skills/creative-model-bridge/SKILL.md"
                copied_skill.write_text(original.replace(marker, ""), encoding="utf-8")
                validation = validate_repository.Validation()
                with patch.object(validate_repository, "ROOT", root):
                    validate_repository._validate_creative_script(
                        copied_plugin, manifest, validation
                    )
                self.assertTrue(
                    any(marker in error for error in validation.errors),
                    (marker, validation.errors),
                )

    def test_release_lock_requires_v2_and_checksum_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="repository-validator-test-") as temporary:
            root = Path(temporary)
            manifest = root / "plugins/fastctx/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            metadata = root / "plugins/fastctx/upstream-release.json"
            metadata.write_text("{}\n", encoding="utf-8")
            (root / "sync-sources.toml").write_text(
                "version = 1\n\n"
                "[[github_releases]]\n"
                'id = "fastctx-release"\n'
                'repository = "yc-duan/fastctx"\n'
                'metadata_destination = "plugins/fastctx/upstream-release.json"\n'
                'plugin_manifest = "plugins/fastctx/.codex-plugin/plugin.json"\n'
                'checksum_asset = "SHA256SUMS"\n'
                'required_assets = ["fastctx-test.tar.gz"]\n',
                encoding="utf-8",
            )
            (root / "sync-lock.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sources": {
                            "fastctx-release": {
                                "kind": "github-release",
                                "tag_object_sha": "a" * 40,
                                "commit": "b" * 40,
                                "assets": {"test": {"sha256": "c" * 64}},
                                "checksum_asset": {"sha256": "invalid"},
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_sync_metadata(validation)
            self.assertIn("sync-lock.json: version must be 2", validation.errors)
            self.assertIn(
                "sync lock fastctx-release: invalid checksum asset digest",
                validation.errors,
            )

    def test_fastctx_windows_runtime_rejects_untrusted_asset_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fastctx-runtime-validator-test-") as temporary:
            root = Path(temporary)
            metadata = root / "plugins/fastctx/windows-bash-runtime.json"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repository": "git-for-windows/git",
                        "version": "2.55.0.3",
                        "tag": "v2.55.0.windows.3",
                        "release_id": 1,
                        "published_at": "2026-07-14T18:41:31Z",
                        "asset": {
                            "name": "PortableGit-2.55.0.3-64-bit.7z.exe",
                            "url": "https://example.com/untrusted.exe",
                            "size": 1,
                            "sha256": "invalid",
                            "archive_format": "7z",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_fastctx_windows_runtime(validation)
            self.assertIn(
                "FastCtx Windows Bash runtime: invalid asset URL",
                validation.errors,
            )
            self.assertIn(
                "FastCtx Windows Bash runtime: invalid asset digest",
                validation.errors,
            )
            self.assertIn(
                "FastCtx Windows Bash runtime: archive_format must be tar.bz2",
                validation.errors,
            )

    def test_mcp_companion_validates_path_launcher_cwd_and_env_vars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-validator-test-") as temporary:
            root = Path(temporary)
            plugin = root / "plugins/example"
            launcher = plugin / "bin/launch"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            launcher.chmod(0o755)
            companion = plugin / ".mcp.json"
            companion.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "example": {
                                "command": "./bin/launch",
                                "cwd": ".",
                                "args": [],
                                "startup_timeout_sec": 45,
                                "env_vars": ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(
                    plugin,
                    {"mcpServers": "./.mcp.json"},
                    validation,
                )
            self.assertEqual(validation.errors, [])

    def test_mcp_companion_rejects_invalid_startup_timeout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-validator-test-") as temporary:
            root = Path(temporary)
            plugin = root / "plugins/example"
            launcher = plugin / "bin/launch"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            launcher.chmod(0o755)
            companion = plugin / ".mcp.json"
            companion.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "example": {
                                "command": "./bin/launch",
                                "startup_timeout_sec": 0,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(
                    plugin,
                    {"mcpServers": "./.mcp.json"},
                    validation,
                )
            self.assertTrue(any("startup_timeout_sec must be a positive number" in error for error in validation.errors))

    def test_mcp_companion_rejects_escape_and_unallowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mcp-validator-test-") as temporary:
            root = Path(temporary)
            plugin = root / "plugins/example"
            plugin.mkdir(parents=True)
            companion = plugin / ".mcp.json"
            companion.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "example": {
                                "command": "../outside",
                                "cwd": "..",
                                "env_vars": ["HOME"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(
                    plugin,
                    {"mcpServers": "./.mcp.json"},
                    validation,
                )
            self.assertTrue(any("cwd must stay inside" in error for error in validation.errors))
            self.assertTrue(any("non-allowlisted" in error for error in validation.errors))
            self.assertTrue(any("command target escapes" in error for error in validation.errors))

    def test_pixi_launcher_requires_and_validates_lockfile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pixi-launcher-validator-test-") as temporary:
            root = Path(temporary)
            plugin = root / "plugins/example"
            launcher = plugin / "bin/launch"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(
                "#!/bin/sh\nexec pixi run --manifest-path \"$PLUGIN_ROOT/pixi.toml\" --locked python -u server.py\n",
                encoding="utf-8",
            )
            launcher.chmod(0o755)
            (plugin / "pixi.toml").write_text(
                "[workspace]\nplatforms = [\"linux-64\"]\n",
                encoding="utf-8",
            )
            companion = plugin / ".mcp.json"
            companion.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "example": {
                                "command": "./bin/launch",
                                "cwd": ".",
                                "args": [],
                                "env_vars": ["CODEX_HOME"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(
                    plugin,
                    {"mcpServers": "./.mcp.json"},
                    validation,
                )
            self.assertTrue(any("Pixi launcher requires pixi.lock" in error for error in validation.errors))

            (plugin / "pixi.lock").write_text(
                "version: 6\n"
                "environments:\n"
                "  default:\n"
                "    packages:\n"
                "      linux-64:\n"
                "      - conda: https://conda.example/pkg.conda\n"
                "packages:\n"
                "- conda: https://conda.example/pkg.conda\n"
                f"  sha256: {'a' * 64}\n",
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(
                    plugin,
                    {"mcpServers": "./.mcp.json"},
                    validation,
                )
            self.assertEqual(validation.errors, [])

    def test_git_alias_command_is_exact_and_direct_pixi_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="git-alias-validator-test-") as temporary:
            root = Path(temporary)
            plugin = root / "plugins/example"
            plugin.mkdir(parents=True)
            scripts = plugin / "scripts"
            scripts.mkdir()
            bootstrap = scripts / "bootstrap.sh"
            bootstrap.write_text("#!/bin/sh\n", encoding="utf-8")
            bootstrap.chmod(0o755)
            companion = plugin / ".mcp.json"
            companion.write_text(json.dumps({"mcpServers": {"example": {
                "command": "git",
                "args": ["-c", 'alias.creative-model-bridge=!sh "${GIT_PREFIX}scripts/bootstrap.sh"', "creative-model-bridge"],
                "cwd": ".", "env_vars": ["CODEX_HOME"],
            }}}), encoding="utf-8")
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(plugin, {"mcpServers": "./.mcp.json"}, validation)
            self.assertEqual(validation.errors, [])

            payload = json.loads(companion.read_text(encoding="utf-8"))
            payload["mcpServers"]["example"]["command"] = "pixi"
            companion.write_text(json.dumps(payload), encoding="utf-8")
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_mcp_servers(plugin, {"mcpServers": "./.mcp.json"}, validation)
            self.assertTrue(any("direct Pixi command" in error for error in validation.errors))


class WorkflowActionPinValidationTests(unittest.TestCase):
    def _validate_workflow(self, uses: str) -> list[str]:
        with tempfile.TemporaryDirectory(prefix="workflow-validator-test-") as temporary:
            root = Path(temporary)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "release.yml").write_text(
                "name: test\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                f"      - uses: {uses}\n",
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_workflows(validation)
            return validation.errors

    def test_external_workflow_action_refs_require_full_lowercase_shas(self) -> None:
        invalid_refs = (
            "actions/checkout@v4",
            "actions/checkout@main",
            "actions/checkout@1234",
            "actions/checkout@3D3C42E5AAC5BA805825DA76410C181273BA90B1",
        )
        for ref in invalid_refs:
            with self.subTest(ref=ref):
                errors = self._validate_workflow(ref)
                self.assertTrue(any("full lowercase commit SHA" in error for error in errors))

    def test_full_sha_and_local_workflow_action_refs_are_accepted(self) -> None:
        full_sha = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        with tempfile.TemporaryDirectory(prefix="workflow-validator-test-") as temporary:
            root = Path(temporary)
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "release.yml").write_text(
                "name: test\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                f"      - uses: {full_sha}\n"
                "      - uses: ./local-action\n",
                encoding="utf-8",
            )
            validation = validate_repository.Validation()
            with patch.object(validate_repository, "ROOT", root):
                validate_repository._validate_workflows(validation)
            self.assertEqual(validation.errors, [])


if __name__ == "__main__":
    unittest.main()
