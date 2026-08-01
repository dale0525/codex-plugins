from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import validate_repository


class RepositorySyncMetadataValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
