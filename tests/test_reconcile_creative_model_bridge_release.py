from __future__ import annotations

import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, "scripts")
from reconcile_creative_model_bridge_release import ASSETS, normalize_digest, plan  # noqa: E402


DESIRED = {name: "sha256:" + "a" * 64 for name in ASSETS if name != "checksums.txt"} | {"checksums.txt": ""}


class ReleaseStatePlannerTests(unittest.TestCase):
    def test_absent_creates_draft(self) -> None:
        self.assertEqual(plan({"status": "absent", "assets": []}, DESIRED)["action"], "create-draft")

    def test_draft_missing_assets_can_be_completed(self) -> None:
        names = sorted(ASSETS - {"checksums.txt"})
        self.assertEqual(plan({"status": "draft", "assets": names}, DESIRED)["action"], "complete-draft")

    def test_draft_digest_difference_is_replaceable(self) -> None:
        names = {name: ("sha256:" + "b" * 64 if name != "checksums.txt" else "") for name in ASSETS}
        self.assertEqual(plan({"status": "draft", "assets": names}, DESIRED)["action"], "replace-draft")

    def test_published_exact_is_read_only(self) -> None:
        assets = {name: ("sha256:" + "a" * 64 if name != "checksums.txt" else "") for name in ASSETS}
        self.assertEqual(plan({"status": "published", "assets": assets}, DESIRED), {"action": "published-read-only", "mutation": False})

    def test_cli_converts_github_assets_list_of_dicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checksums = root / "checksums.txt"
            binaries = sorted(ASSETS - {"checksums.txt"})
            checksums.write_text("\n".join("a" * 64 + "  " + name for name in binaries) + "\n", encoding="utf-8")
            state = root / "state.json"
            state.write_text(json.dumps({"isDraft": False, "assets": [{"name": name, "digest": "sha256:" + "a" * 64} for name in sorted(ASSETS)]}), encoding="utf-8")
            result = subprocess.run([sys.executable, "scripts/reconcile_creative_model_bridge_release.py", "--state-file", str(state), "--checksums", str(checksums)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("published-read-only", result.stdout)

    def test_published_mismatch_and_unknown_extra_are_hard_failures(self) -> None:
        with self.assertRaises(ValueError):
            plan({"status": "published", "assets": sorted(ASSETS - {"checksums.txt"})}, DESIRED)
        with self.assertRaises(ValueError):
            plan({"status": "published", "assets": {name: ("sha256:" + "b" * 64 if name != "checksums.txt" else "") for name in ASSETS}}, DESIRED)
        with self.assertRaises(ValueError):
            normalize_digest("SHA256:" + "a" * 64)
        with self.assertRaises(ValueError):
            plan({"status": "draft", "assets": sorted(ASSETS | {"unexpected.bin"})}, DESIRED)


if __name__ == "__main__":
    unittest.main()
