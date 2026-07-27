from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/fastctx"


class FastCtxPluginTests(unittest.TestCase):
    def test_windows_provisioner_uses_reviewed_portable_bash_without_pixi(self) -> None:
        script = (PLUGIN / "scripts/provision.ps1").read_text(encoding="utf-8")
        self.assertIn("windows-bash-runtime.json", script)
        self.assertIn("FASTCTX_BASH", script)
        self.assertIn("tar.exe", script)
        self.assertIn("Portable Git archive checksum verification failed", script)
        self.assertNotRegex(script, re.compile(r"\b(?:pixi|conda|m2-bash)\b", re.IGNORECASE))

    def test_windows_bash_runtime_is_pinned_to_github_digest(self) -> None:
        metadata = json.loads(
            (PLUGIN / "windows-bash-runtime.json").read_text(encoding="utf-8")
        )
        asset = metadata["asset"]
        self.assertEqual(metadata["repository"], "git-for-windows/git")
        self.assertIn(f"/download/{metadata['tag']}/", asset["url"])
        self.assertTrue(asset["url"].endswith(f"/{asset['name']}"))
        self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(asset["size"], 0)


if __name__ == "__main__":
    unittest.main()
