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
    def _completed(self, *, stdout: object = "", stderr: object = "", returncode: object = 1) -> subprocess.CompletedProcess[object]:
        return subprocess.CompletedProcess(["bash", "bootstrap.sh", "cache"], returncode, stdout=stdout, stderr=stderr)

    def _frames(self, value: str, *, ok: bool = True, input_gate: bool = True) -> str:
        raw = value.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        chunk = {
            "protocol": 1,
            "type": "data",
            "id": "1",
            "seq": 0,
            "data": value,
            "chunk_sha256": digest,
            "sha256": digest,
            "done": True,
        }
        ready = {
            "protocol": 1,
            "type": "ready",
            "input_echo": False if input_gate else True,
            "input_mode": "pipe" if input_gate else "argv",
        }
        response = {
            "protocol": 1,
            "type": "response",
            "id": "1",
            "ok": ok,
            "sha256": digest,
            "bytes": len(raw),
            "chunks": 1,
        }
        return "\n".join(json.dumps(item, ensure_ascii=False) for item in (ready, response, chunk))

    def test_decode_validates_v1_chunk_sequence_and_hashes(self) -> None:
        raw = json.dumps({"text": "成稿", "network": False}, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(smoke._decode(self._frames(raw), request_id="1", phase="preview")["text"], "成稿")

    def test_decode_rejects_truncated_or_tampered_result(self) -> None:
        frames = "\n".join(
            [
                json.dumps({"protocol": 1, "type": "ready"}),
                json.dumps({"protocol": 1, "type": "response", "id": "1", "ok": True, "sha256": "0" * 64, "bytes": 4, "chunks": 1}),
                json.dumps({"protocol": 1, "type": "data", "id": "1", "seq": 0, "data": "null", "chunk_sha256": "0" * 64, "sha256": "0" * 64, "done": False}),
            ]
        )
        with self.assertRaises(smoke.SmokeFailure) as raised:
            smoke._decode(frames, request_id="1", phase="preview")
        self.assertEqual(raised.exception.render(), "phase=preview reason=chunk_completion_mismatch launcher=unknown action=unknown")

    def test_decode_bridge_error_is_fixed_and_does_not_echo_payload(self) -> None:
        secret = "bridge-error-secret"
        frames = self._frames(json.dumps({"error": secret}), ok=False)
        with self.assertRaises(smoke.SmokeFailure) as raised:
            smoke._decode(frames, request_id="1", phase="preview")
        self.assertEqual(raised.exception.render(), "phase=preview reason=bridge_error launcher=unknown action=unknown")
        self.assertNotIn(secret, raised.exception.render())

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
        self.assertEqual(stream.getvalue(), "usage: smoke_creative_model_bridge.py BINARY\n")

    def test_main_unexpected_exception_has_fixed_output(self) -> None:
        secret = "unexpected-exception-secret"
        with tempfile.NamedTemporaryFile(prefix="cmb-binary-") as binary:
            stream = io.StringIO()
            with (
                patch.object(smoke.sys, "argv", ["smoke_creative_model_bridge.py", binary.name]),
                patch.object(smoke.sys, "stderr", stream),
                patch.object(smoke, "_config", side_effect=RuntimeError(secret)),
            ):
                self.assertEqual(smoke.main(), 1)
            self.assertEqual(
                stream.getvalue(),
                "creative-model-bridge smoke failure: phase=startup reason=unexpected_exception launcher=unknown action=unknown exception=other\n",
            )
            self.assertNotIn(secret, stream.getvalue())

    def test_windows_launcher_command_uses_git_bash(self) -> None:
        launcher = Path("D:/checkout/plugins/creative-model-bridge/scripts/bootstrap.sh")
        with patch.object(smoke.os, "name", "nt"):
            self.assertEqual(smoke._launcher_command(launcher, "cache"), ["bash", launcher.as_posix(), "cache"])
        with patch.object(smoke.os, "name", "posix"):
            self.assertEqual(smoke._launcher_command(launcher, "cache"), [str(launcher), "cache"])

    def test_smoke_failure_renderer_has_exact_allowlist_order(self) -> None:
        failure = smoke.SmokeFailure(
            "preview",
            "process_exit",
            launcher="git_bash_bootstrap",
            action="run",
            returncode=7,
            stdout_bytes=0,
            stderr_bytes=87,
        )
        expected = "phase=preview reason=process_exit launcher=git_bash_bootstrap action=run returncode=7 stdout_bytes=0 stderr_bytes=87"
        self.assertEqual(failure.render(), expected)
        self.assertEqual(str(failure), expected)

    def test_smoke_failure_rejects_unapproved_metadata(self) -> None:
        invalid = (
            {"phase": "test"},
            {"reason": "raw-secret"},
            {"launcher": "raw-secret"},
            {"action": "raw-secret"},
            {"exception": "raw-secret"},
            {"returncode": True},
            {"returncode": 2**40},
            {"stdout_bytes": -1},
            {"stdout_capped": True},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                arguments = {"phase": "preview", "reason": "process_exit", **changes}
                smoke.SmokeFailure(**arguments)

    def test_launcher_classifier_is_strict_and_never_returns_tokens(self) -> None:
        known = (
            (["/private/secret/bootstrap.sh", "cache"], ("posix_bootstrap", "cache")),
            (["bash", "/private/secret/bootstrap.sh", "run"], ("git_bash_bootstrap", "run")),
            (["bash.exe", "D:/private/secret/bootstrap.sh", "install"], ("git_bash_bootstrap", "install")),
        )
        for command, expected in known:
            self.assertEqual(smoke._classify_command(command), expected)
        unknown = (
            ["/private/secret/bootstrap.sh", "cache", "--password", "secret"],
            ["/private/secret/not-bootstrap.sh", "cache"],
            ["/private/secret/bootstrap.sh", "secret-action"],
            ["bash-secret", "/private/secret/bootstrap.sh", "cache"],
            [b"/private/secret/bootstrap.sh", "cache"],
            ["bash", "/private/secret/bootstrap.sh", "cache", "\x00"],
        )
        for command in unknown:
            self.assertEqual(smoke._classify_command(command), ("unknown", "unknown"))

    def test_process_observation_covers_original_secret_fixtures(self) -> None:
        fixtures = (
            ("Authorization: Basic basic-secret", "basic-secret"),
            (r'payload={\"token\":\"escaped-json-secret\"}', "escaped-json-secret"),
        )
        command = ["bash", "/private/secret/bootstrap.sh", "cache"]
        for stderr, secret in fixtures:
            with self.subTest(secret=secret):
                observation = smoke._process_observation(command, self._completed(stderr=stderr))
                rendered = smoke._failure("cache", "process_exit", observation=observation, returncode=7).render()
                self.assertEqual(
                    rendered,
                    f"phase=cache reason=process_exit launcher=git_bash_bootstrap action=cache returncode=7 stdout_bytes=0 stderr_bytes={len(stderr.encode('utf-8'))}",
                )
                self.assertNotIn(secret, rendered)

        command_with_spaces = ["smoke", "--password", "two word secret"]
        observation = smoke._process_observation(command_with_spaces, self._completed(stderr="phase=cache"))
        rendered = smoke._failure("cache", "process_exit", observation=observation, returncode=7).render()
        self.assertEqual(
            rendered,
            "phase=cache reason=process_exit launcher=unknown action=unknown returncode=7 stdout_bytes=0 stderr_bytes=11",
        )
        self.assertNotIn("two word secret", rendered)

    def test_adversarial_stream_shapes_are_value_free(self) -> None:
        payloads = (
            ("Authorization: Basic basic-header-secret", "basic-header-secret"),
            ("authorization: Bearer bearer-header-secret", "bearer-header-secret"),
            ("Proxy-Authorization: Digest digest-header-secret", "digest-header-secret"),
            ("X-Custom-Header: custom-header-secret", "custom-header-secret"),
            ('{"outer":{"token":"nested-json-secret"}}', "nested-json-secret"),
            (r'payload={\"token\":\"escaped-json-secret-2\"}', "escaped-json-secret-2"),
            ("line-one\r\n\x1b[31mansi-secret\x00", "ansi-secret"),
            ("中文内容🙂秘密", "秘密"),
        )
        command = ["bash", "/private/stream-secret/bootstrap.sh", "run"]
        for payload, secret in payloads:
            with self.subTest(secret=secret):
                observation = smoke._process_observation(command, self._completed(stdout=payload, stderr=payload))
                rendered = smoke._failure("preview", "process_exit", observation=observation, returncode=2).render()
                byte_count = len(payload.encode("utf-8"))
                self.assertEqual(
                    rendered,
                    f"phase=preview reason=process_exit launcher=git_bash_bootstrap action=run returncode=2 stdout_bytes={byte_count} stderr_bytes={byte_count}",
                )
                self.assertNotIn(secret, rendered)

    def test_adversarial_argv_shapes_are_unknown_without_echo(self) -> None:
        commands = (
            ["/private/argv-secret/bootstrap.sh", "--password=equals-secret"],
            ["/private/argv-secret/bootstrap.sh", "--password", "two word secret"],
            ["/private/argv-secret/bootstrap.sh", "--password=quoted 'quote-secret'"],
            ["/private/argv-secret/bootstrap.sh", "/slash/path-secret"],
            ["/private/argv-secret/bootstrap.sh", "line\nnewline-secret"],
            ["/private/argv-secret/bootstrap.sh", "\x00nul-secret"],
            [b"/private/argv-secret/bootstrap.sh", "cache"],
            ["bash", "/private/argv-secret/bootstrap.sh", b"cache"],
            ["bash", "/private/argv-secret/bootstrap.sh", "cache", "--password", "extra-secret"],
        )
        expected = "phase=cache reason=process_exit launcher=unknown action=unknown returncode=2 stdout_bytes=0 stderr_bytes=0"
        for command in commands:
            with self.subTest(command=command):
                observation = smoke._process_observation(command, self._completed())
                rendered = smoke._failure("cache", "process_exit", observation=observation, returncode=2).render()
                self.assertEqual(rendered, expected)
                self.assertNotIn("secret", rendered)

    def test_observation_is_noninterfering_for_same_shape_and_lengths(self) -> None:
        first = smoke._process_observation(
            ["/tmp/first-secret/bootstrap.sh", "run"],
            self._completed(stdout=b"\x00\xffA\r\n", stderr=b"\x1b[31msecret-a"),
        )
        second = smoke._process_observation(
            ["/tmp/second-secret/bootstrap.sh", "run"],
            self._completed(stdout=b"\xf0\x9f\x92\xa9\x00", stderr=b"\x1b[32msecret-b"),
        )
        first_rendered = smoke._failure("preview", "process_exit", observation=first, returncode=1).render()
        second_rendered = smoke._failure("preview", "process_exit", observation=second, returncode=1).render()
        self.assertEqual(first_rendered, second_rendered)
        self.assertEqual(
            first_rendered,
            "phase=preview reason=process_exit launcher=posix_bootstrap action=run returncode=1 stdout_bytes=5 stderr_bytes=13",
        )
        for secret in ("first-secret", "second-secret", "secret-a", "secret-b"):
            self.assertNotIn(secret, first_rendered + second_rendered)

    def test_stream_observation_counts_bytes_and_caps_without_content(self) -> None:
        secret_a = "超长-A-秘密-🙂"
        secret_b = "超长-B-秘密-🧪"
        first = smoke._process_observation(
            ["bash", "/tmp/bootstrap.sh", "run"],
            self._completed(stdout=(secret_a * 2000).encode("utf-8"), stderr=b"\xff" * (smoke.MAX_DIAGNOSTIC_STREAM_BYTES + 20)),
        )
        second = smoke._process_observation(
            ["bash", "/tmp/bootstrap.sh", "run"],
            self._completed(stdout=(secret_b * 2000).encode("utf-8"), stderr=b"\x00" * (smoke.MAX_DIAGNOSTIC_STREAM_BYTES + 20)),
        )
        first_rendered = smoke._failure("preview", "process_exit", observation=first, returncode=1).render()
        second_rendered = smoke._failure("preview", "process_exit", observation=second, returncode=1).render()
        self.assertEqual(first_rendered, second_rendered)
        self.assertEqual(
            first_rendered,
            "phase=preview reason=process_exit launcher=git_bash_bootstrap action=run returncode=1 stdout_bytes=4096 stderr_bytes=4096 stdout_capped=true stderr_capped=true",
        )
        self.assertNotIn(secret_a, first_rendered)
        self.assertNotIn(secret_b, second_rendered)

    def test_process_observation_ignores_non_string_stream_objects(self) -> None:
        secret = "non-string-secret"
        observation = smoke._process_observation(
            ["bash", "/tmp/bootstrap.sh", "run"],
            self._completed(stdout={"secret": secret}, stderr=object()),
        )
        rendered = smoke._failure("preview", "process_exit", observation=observation, returncode=1).render()
        self.assertEqual(
            rendered,
            "phase=preview reason=process_exit launcher=git_bash_bootstrap action=run returncode=1 stdout_bytes=0 stderr_bytes=0",
        )
        self.assertNotIn(secret, rendered)

    def test_run_spawn_failure_uses_exception_enum_only(self) -> None:
        command = ["bash", "/private/spawn-secret/bootstrap.sh", "cache"]
        with patch.object(smoke.subprocess, "run", side_effect=FileNotFoundError("spawn-secret")):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke._run(command, {}, phase="cache")
        self.assertEqual(
            raised.exception.render(),
            "phase=cache reason=spawn_failed launcher=git_bash_bootstrap action=cache exception=file_not_found",
        )
        self.assertNotIn("spawn-secret", raised.exception.render())

    def test_run_permission_oserror_and_unicode_failures_are_fixed(self) -> None:
        cases = (
            (PermissionError("permission-secret"), "permission", "spawn_failed"),
            (OSError("os-secret"), "os_error", "spawn_failed"),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "decode-secret"), "unicode", "output_decode_failed"),
        )
        command = ["/private/run-secret/bootstrap.sh", "run"]
        for error, exception, reason in cases:
            with self.subTest(exception=exception), patch.object(smoke.subprocess, "run", side_effect=error):
                with self.assertRaises(smoke.SmokeFailure) as raised:
                    smoke._run(command, {}, phase="preview")
            self.assertEqual(
                raised.exception.render(),
                f"phase=preview reason={reason} launcher=posix_bootstrap action=run exception={exception}",
            )
            self.assertNotIn("secret", raised.exception.render())

    def test_run_timeout_reports_only_bounded_metadata(self) -> None:
        error = subprocess.TimeoutExpired(
            ["bash", "timeout-secret", "cache"],
            17,
            output=b"stdout-timeout-secret",
            stderr=b"stderr-timeout-secret",
        )
        with patch.object(smoke.subprocess, "run", side_effect=error):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke._run(["bash", "/tmp/bootstrap.sh", "cache"], {}, phase="cache", timeout=17.0)
        self.assertEqual(
            raised.exception.render(),
            "phase=cache reason=timeout launcher=git_bash_bootstrap action=cache exception=timeout timeout_seconds=17 stdout_bytes=21 stderr_bytes=21",
        )
        self.assertNotIn("timeout-secret", raised.exception.render())

    def test_run_nonzero_and_invalid_returncode_are_safe(self) -> None:
        result = self._completed(stdout="stdout-secret", stderr="stderr-secret", returncode=9)
        with patch.object(smoke.subprocess, "run", return_value=result):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke._run(["bash", "/tmp/bootstrap.sh", "run"], {}, phase="preview")
        self.assertEqual(
            raised.exception.render(),
            "phase=preview reason=process_exit launcher=git_bash_bootstrap action=run returncode=9 stdout_bytes=13 stderr_bytes=13",
        )
        self.assertNotIn("stdout-secret", raised.exception.render())
        self.assertNotIn("stderr-secret", raised.exception.render())

        invalid = self._completed(returncode=True)
        with patch.object(smoke.subprocess, "run", return_value=invalid):
            with self.assertRaises(smoke.SmokeFailure) as raised:
                smoke._run(["bash", "/tmp/bootstrap.sh", "run"], {}, phase="preview")
        self.assertEqual(
            raised.exception.render(),
            "phase=preview reason=invalid_returncode launcher=git_bash_bootstrap action=run stdout_bytes=0 stderr_bytes=0",
        )


if __name__ == "__main__":
    unittest.main()
