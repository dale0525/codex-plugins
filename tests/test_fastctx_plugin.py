from __future__ import annotations

import json
import re
import stat
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/fastctx"


class FastCtxPluginTests(unittest.TestCase):
    def test_windows_provisioner_uses_reviewed_portable_bash_without_pixi(self) -> None:
        script = (PLUGIN / "scripts/provision.ps1").read_text(encoding="utf-8")
        self.assertIn("windows-bash-runtime.json", script)
        self.assertIn("fastctx-mcp-env.ps1", script)
        self.assertIn("FASTCTX_BASH", script)
        self.assertIn("tar.exe", script)
        self.assertIn("-tjf $archive", script)
        self.assertIn("-xjf $archive", script)
        self.assertIn("Git for Windows runtime archive checksum verification failed", script)
        self.assertNotRegex(script, re.compile(r"\b(?:pixi|conda|m2-bash)\b", re.IGNORECASE))
        self.assertNotIn(".7z.exe", script)

    def test_owned_runtime_metadata_is_explicitly_transitional_and_digest_pinned(self) -> None:
        metadata = json.loads((PLUGIN / "runtime-release.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 2)
        self.assertEqual(metadata["distribution"], "transitional-upstream")
        self.assertTrue(metadata["transitional"])
        self.assertIn("do not edit hashes manually", metadata["transition_note"])
        self.assertEqual(set(metadata["assets"]), {
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
            "x86_64-pc-windows-msvc",
            "x86_64-unknown-linux-gnu",
        })
        for asset in metadata["assets"].values():
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(asset["size"], 0)
            self.assertTrue(asset["url"].endswith("/" + asset["name"]))

    def test_owned_engine_and_provisioners_share_the_compact_contract(self) -> None:
        agents = (PLUGIN / "engine/src/control/agents.rs").read_text(encoding="utf-8")
        shell = (PLUGIN / "scripts/provision.sh").read_text(encoding="utf-8")
        powershell = (PLUGIN / "scripts/provision.ps1").read_text(encoding="utf-8")
        cargo = (PLUGIN / "engine/Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.2.5"', cargo)
        self.assertIn('repository = "https://github.com/dale0525/codex-plugins"', cargo)
        self.assertIn('CODEX_PLUGIN_DISTRIBUTION: &str = "codex-plugin"',
                      (PLUGIN / "engine/src/update/check.rs").read_text(encoding="utf-8"))
        self.assertIn("AGENTS_SECTION.to_string()", agents)
        self.assertIn("runtime-release.json", shell)
        self.assertIn("runtime-release.json", powershell)
        self.assertIn("System.IO.Compression.ZipFile]::OpenRead", powershell)
        self.assertIn("unsafe path", powershell)

    def test_owned_release_workflow_packages_licenses_and_uses_least_privilege(self) -> None:
        workflow = (ROOT / ".github/workflows/release-fastctx.yml").read_text(encoding="utf-8")
        self.assertIn("LICENSE-APACHE NOTICE THIRD_PARTY_LICENSES.md", workflow)
        self.assertIn("fastctx LICENSE-APACHE NOTICE THIRD_PARTY_LICENSES.md", workflow)
        self.assertIn("--check-archive", workflow)
        self.assertIn("--binary fastctx.exe --payload-dir .", workflow)
        self.assertIn("cargo build --locked --release", workflow)
        self.assertIn("cargo test --locked --no-default-features", workflow)
        self.assertIn("cargo zigbuild --locked --release --target x86_64-unknown-linux-gnu.2.31", workflow)
        self.assertIn("readelf -W --version-info", workflow)
        self.assertIn("--check-glibc-version-info", workflow)
        self.assertIn("ZipFile]::CreateFromDirectory($package, $archive)", workflow)
        self.assertNotIn("Compress-Archive", workflow)
        self.assertNotIn("Join-Path $package '*'", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("release version does not match Cargo.toml", (ROOT / "scripts/write_fastctx_runtime_release.py").read_text(encoding="utf-8"))

    def test_readme_does_not_claim_fastctx_daily_sync(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("FastCtx is not a periodic sync source", readme)
        self.assertNotIn("Release metadata and supported-platform\narchive digests are synchronized daily", readme)

    def test_windows_provisioner_persists_device_local_bash_in_mcp_environment(self) -> None:
        script = (PLUGIN / "scripts/provision.ps1").read_text(encoding="utf-8")
        helper = (PLUGIN / "scripts/fastctx-mcp-env.ps1").read_text(encoding="utf-8")
        self.assertIn("Set-FastCtxMcpBashEnvironment", script)
        self.assertIn("Assert-FastCtxMcpBashEnvironment", script)
        self.assertIn("[mcp_servers.fastctx.env]", helper)
        self.assertIn("FASTCTX_BASH", helper)
        self.assertNotIn("codex-config", helper)

    def test_windows_bash_bridge_invokes_pwsh_instead_of_parsing_ps1(self) -> None:
        bridge = (PLUGIN / "scripts/provision-windows.sh").read_text(encoding="utf-8")
        provisioner = PLUGIN / "scripts/provision.ps1"
        powershell = provisioner.read_text(encoding="utf-8")
        skill = (PLUGIN / "skills/fastctx/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("pwsh.exe", bridge)
        self.assertIn("-ExecutionPolicy Bypass", bridge)
        self.assertIn('-File "$provisioner"', bridge)
        self.assertNotIn("source $provisioner", bridge)
        self.assertTrue(powershell.startswith("#!/usr/bin/env -S pwsh "))
        self.assertTrue(provisioner.stat().st_mode & stat.S_IXUSR)
        self.assertIn("provision-windows.sh", skill)
        self.assertIn("Never execute `provision.ps1` as a bare command", skill)

    def test_windows_bash_runtime_is_pinned_to_github_digest(self) -> None:
        metadata = json.loads(
            (PLUGIN / "windows-bash-runtime.json").read_text(encoding="utf-8")
        )
        asset = metadata["asset"]
        self.assertEqual(metadata["repository"], "git-for-windows/git")
        self.assertEqual(metadata["release_id"], 354001887)
        self.assertIn(f"/download/{metadata['tag']}/", asset["url"])
        self.assertTrue(asset["url"].endswith(f"/{asset['name']}"))
        self.assertEqual(asset["archive_format"], "tar.bz2")
        self.assertEqual(asset["name"], "Git-2.55.0.3-64-bit.tar.bz2")
        self.assertEqual(asset["size"], 122162896)
        self.assertEqual(
            asset["sha256"],
            "4ee071816e424f928f493c4b42e5486d05344a371665c82f1802ebcecaa1d19a",
        )


if __name__ == "__main__":
    unittest.main()
