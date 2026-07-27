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
        self.assertIn("-tjf $archive", script)
        self.assertIn("-xjf $archive", script)
        self.assertIn("Git for Windows runtime archive checksum verification failed", script)
        self.assertNotRegex(script, re.compile(r"\b(?:pixi|conda|m2-bash)\b", re.IGNORECASE))
        self.assertNotIn(".7z.exe", script)

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
