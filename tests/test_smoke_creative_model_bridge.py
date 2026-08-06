from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_creative_model_bridge as smoke  # noqa: E402


class CreativeModelBridgeSmokeTests(unittest.TestCase):
    def test_decode_validates_v1_chunk_sequence_and_hashes(self) -> None:
        raw = json.dumps({"text": "成稿", "network": False}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        data = raw.decode("utf-8")
        chunk_digest = hashlib.sha256(raw).hexdigest()
        frames = "\n".join(
            [
                json.dumps({"protocol": 1, "type": "ready"}),
                json.dumps({"protocol": 1, "type": "response", "id": "1", "ok": True, "sha256": digest, "bytes": len(raw), "chunks": 1}),
                json.dumps({"protocol": 1, "type": "data", "id": "1", "seq": 0, "data": data, "chunk_sha256": chunk_digest, "sha256": digest, "done": True}),
            ]
        )
        self.assertEqual(smoke._decode(frames, request_id="1", phase="test")["text"], "成稿")

    def test_decode_rejects_truncated_or_tampered_result(self) -> None:
        frames = "\n".join(
            [
                json.dumps({"protocol": 1, "type": "ready"}),
                json.dumps({"protocol": 1, "type": "response", "id": "1", "ok": True, "sha256": "0" * 64, "bytes": 4, "chunks": 1}),
                json.dumps({"protocol": 1, "type": "data", "id": "1", "seq": 0, "data": "null", "chunk_sha256": "0" * 64, "sha256": "0" * 64, "done": False}),
            ]
        )
        with self.assertRaises(smoke.SmokeFailure):
            smoke._decode(frames, request_id="1", phase="test")

    def test_migration_fixture_is_explicit_and_preserves_unrelated_table(self) -> None:
        with tempfile.TemporaryDirectory(prefix="creative-smoke-fixture-") as temporary:
            home = Path(temporary)
            install_id, command, pointer = smoke._migration_fixture(home)
            self.assertEqual(len(install_id), 36)
            self.assertTrue(command.startswith("/tmp/"))
            self.assertTrue(pointer.is_file())
            self.assertIn("[mcp_servers.other]", (home / "config.toml").read_text(encoding="utf-8"))

    def test_smoke_main_requires_binary_and_never_prints_secret(self) -> None:
        stream = io.StringIO()
        with patch.object(smoke.sys, "argv", ["smoke_creative_model_bridge.py"]), patch.object(smoke.sys, "stderr", stream):
            self.assertEqual(smoke.main(), 2)
        self.assertNotIn("token", stream.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
