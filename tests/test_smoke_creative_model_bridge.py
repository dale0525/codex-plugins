from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import smoke_creative_model_bridge as smoke  # noqa: E402


_DIAGNOSTIC = {
    "phase": "models",
    "outer_type": "URLError",
    "reason_type": "SSLCertVerificationError",
    "errno": 1,
    "ssl_verify_code": 20,
    "ssl_reason": "UNABLE_TO_GET_ISSUER",
}


class CreativeModelBridgeSmokeTests(unittest.TestCase):
    def test_locked_openssl_priority_and_path_fallback_for_posix_and_windows(self) -> None:
        def components(value: str) -> tuple[str, ...]:
            return PurePosixPath(value.replace("\\", "/")).parts

        with (
            patch.object(smoke.Path, "is_file", return_value=True),
            patch.object(smoke.shutil, "which", return_value="/path/openssl"),
        ):
            posix_locked = components(smoke._openssl_executable(os_name="posix"))
            self.assertEqual(posix_locked[-5:], PurePosixPath(".pixi/envs/default/bin/openssl").parts)
        with (
            patch.object(smoke.Path, "is_file", return_value=True),
            patch.object(smoke.shutil, "which", return_value="C:/path/openssl.exe"),
        ):
            windows_locked = components(smoke._openssl_executable(os_name="nt"))
            self.assertEqual(windows_locked[-6:], PureWindowsPath(r".pixi\envs\default\Library\bin\openssl.exe").parts)
        with (
            patch.object(smoke.Path, "is_file", return_value=False),
            patch.object(smoke.shutil, "which", return_value="/fallback/openssl"),
        ):
            self.assertEqual(smoke._openssl_executable(os_name="posix"), "/fallback/openssl")
        with (
            patch.object(smoke.Path, "is_file", return_value=False),
            patch.object(smoke.shutil, "which", return_value=r"C:\fallback\openssl.exe"),
        ):
            self.assertEqual(smoke._openssl_executable(os_name="nt"), r"C:\fallback\openssl.exe")

    def test_tls_fixture_commands_have_named_extensions_and_explicit_serials(self) -> None:
        commands: list[list[str]] = []

        def capture(openssl: str, arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(openssl, "/locked/openssl")
            commands.append(arguments)
            return subprocess.CompletedProcess([openssl, *arguments], 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temporary, patch.object(smoke, "_run_fixture_openssl", side_effect=capture):
            root = Path(temporary)
            ca_cert, server_cert, _server_key, _untrusted_cert = smoke._make_tls_material(root, "/locked/openssl")
            self.assertTrue(server_cert.parent == root)
            ca_config = (root / "ca.cnf").read_text(encoding="utf-8")
            leaf_config = (root / "leaf.cnf").read_text(encoding="utf-8")
        self.assertIn("CA:true,pathlen:0", ca_config)
        self.assertIn("keyCertSign,cRLSign", ca_config)
        self.assertIn("subjectKeyIdentifier = hash", ca_config)
        self.assertIn("authorityKeyIdentifier = keyid:always,issuer", ca_config)
        self.assertIn("CA:false", leaf_config)
        self.assertIn("extendedKeyUsage = serverAuth", leaf_config)
        self.assertIn("subjectAltName = DNS:localhost,IP:127.0.0.1", leaf_config)
        self.assertIn("authorityKeyIdentifier = keyid,issuer", leaf_config)
        self.assertEqual(len(commands), 7)
        self.assertIn("-set_serial", commands[1])
        self.assertIn("1001", commands[1])
        self.assertIn("2001", commands[3])
        self.assertIn("1002", commands[6])
        self.assertNotIn("-CAcreateserial", " ".join(" ".join(command) for command in commands))

    def test_tls_verify_command_identity_and_safe_failures(self) -> None:
        calls: list[tuple[str, list[str], dict[str, object]]] = []

        def capture(openssl: str, arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((openssl, arguments, kwargs))
            return subprocess.CompletedProcess([openssl, *arguments], 0, stdout="", stderr="")

        with patch.object(smoke, "_run_fixture_openssl", side_effect=capture):
            smoke._verify_tls_fixture("/locked/openssl", Path("ca.pem"), Path("server.pem"))
            smoke._verify_tls_live("/locked/openssl", Path("ca.pem"), 443)
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertEqual(calls[0][1][:2], ["verify", "-purpose"])
        self.assertIn("sslserver", calls[0][1])
        self.assertIn("-verify_ip", calls[0][1])
        self.assertIn("127.0.0.1", calls[0][1])
        self.assertEqual(calls[1][1][0], "s_client")
        self.assertIn("-verify_return_error", calls[1][1])
        self.assertEqual(calls[1][2]["input_text"], "")
        with patch.object(smoke.subprocess, "run", side_effect=OSError("/secret/openssl")):
            with self.assertRaises(smoke._SmokeFailure) as context:
                smoke._run_fixture_openssl("/locked/openssl", ["verify"], phase="tls-fixture-file-verify")
        self.assertEqual((context.exception.phase, context.exception.category), ("tls-fixture-file-verify", "openssl"))
        self.assertNotIn("secret", json.dumps(context.exception.payload()))
        with patch.object(
            smoke.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["openssl"], 1, stdout="PEM SECRET", stderr="PEM SECRET"),
        ):
            with self.assertRaises(smoke._SmokeFailure) as context:
                smoke._run_fixture_openssl("/locked/openssl", ["s_client"], phase="tls-fixture-live-verify")
        self.assertEqual((context.exception.phase, context.exception.category, context.exception.returncode), ("tls-fixture-live-verify", "verify", 1))
        self.assertNotIn("PEM", json.dumps(context.exception.payload()))

    def test_trusted_tls_child_environment_enables_typed_diagnostics(self) -> None:
        environment = smoke._tls_environment({"BASE": "value"}, ROOT / "home", ROOT / "ca.pem")
        self.assertEqual(environment["CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS"], "1")
        self.assertEqual(environment["SSL_CERT_FILE"], str(ROOT / "ca.pem"))
        self.assertEqual(environment["BASE"], "value")

    def test_trusted_is_error_emits_exact_safe_diagnostic_fingerprint(self) -> None:
        responses = [{"id": 1, "result": {"isError": True, "transport_diagnostic": _DIAGNOSTIC}}]
        diagnostic = smoke._trusted_error_diagnostic(responses)
        self.assertEqual(diagnostic, _DIAGNOSTIC)
        failure = smoke._SmokeFailure("trusted-response", "is-error", diagnostic=diagnostic)
        stream = io.StringIO()
        with redirect_stderr(stream):
            smoke._emit_failure(failure)
        line = stream.getvalue().strip()
        payload = json.loads(line.split(": ", 1)[1])
        self.assertEqual(
            set(payload), {"phase", "category", "returncode", "transport_diagnostic", "handler_seen"}
        )
        self.assertEqual(payload["transport_diagnostic"], _DIAGNOSTIC)
        self.assertEqual(payload["handler_seen"], [])
        self.assertNotIn("secret", line.lower())

    def test_tls_smoke_passes_trusted_flag_and_emits_trusted_error(self) -> None:
        class FakeContext:
            def load_cert_chain(self, **_: object) -> None:
                return None

            def wrap_socket(self, socket: object, **_: object) -> object:
                return socket

        class FakeServer:
            server_port = 443
            socket = object()

            def serve_forever(self) -> None:
                return None

            def shutdown(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        class FakeThread:
            def start(self) -> None:
                return None

            def join(self, **_: object) -> None:
                return None

        captured: list[dict[str, str]] = []

        def fake_rpc(_binary: Path, _payload: str, environment: dict[str, str], **_: object) -> list[dict[str, object]]:
            captured.append(environment)
            return [
                {"id": 1, "result": {"isError": True, "transport_diagnostic": _DIAGNOSTIC}},
                {"id": 2, "result": {"isError": True, "transport_diagnostic": {**_DIAGNOSTIC, "phase": "responses"}}},
            ]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            materials = (root / "ca.pem", root / "server.pem", root / "server.key", root / "untrusted.pem")
            stream = io.StringIO()
            with (
                patch.object(smoke, "_make_tls_material", return_value=materials),
                patch.object(smoke, "_verify_tls_fixture"),
                patch.object(smoke, "_verify_tls_live"),
                patch.object(smoke.ssl, "SSLContext", return_value=FakeContext()),
                patch.object(smoke, "_LocalTLSHTTPServer", return_value=FakeServer()),
                patch.object(smoke.threading, "Thread", return_value=FakeThread()),
                patch.object(smoke, "_run_rpc", side_effect=fake_rpc),
                redirect_stderr(stream),
            ):
                with self.assertRaises(smoke._SmokeFailure) as context:
                    smoke._tls_smoke(Path("bridge"), root)
        self.assertEqual(context.exception.phase, "trusted-response")
        self.assertEqual(captured[0]["CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS"], "1")
        self.assertEqual(context.exception.diagnostic, _DIAGNOSTIC)
        self.assertIn('"phase": "trusted-response"', stream.getvalue())
        self.assertIn('"transport_diagnostic": {', stream.getvalue())
        self.assertNotIn("secret", stream.getvalue().lower())

    def test_rpc_failure_categories_are_fixed_and_secret_free(self) -> None:
        cases = (
            ("launch", OSError("https://secret.example/token"), None),
            ("timeout", subprocess.TimeoutExpired("bridge", 90), None),
        )
        for category, error, returncode in cases:
            with self.subTest(category=category):
                with patch.object(smoke.subprocess, "run", side_effect=error):
                    with self.assertRaises(smoke._SmokeFailure) as context:
                        smoke._run_rpc(ROOT / "bridge", "{}", {}, phase="trusted-rpc")
                failure = context.exception
                self.assertEqual((failure.phase, failure.category, failure.returncode), ("trusted-rpc", category, returncode))
                self.assertNotIn("secret", json.dumps(failure.payload()))

        with patch.object(
            smoke.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["bridge"], 7, stdout=b"", stderr=b"secret"),
        ):
            with self.assertRaises(smoke._SmokeFailure) as context:
                smoke._run_rpc(ROOT / "bridge", "{}", {}, phase="negative-rpc")
        self.assertEqual((context.exception.phase, context.exception.category, context.exception.returncode), ("negative-rpc", "nonzero", 7))

        for stdout, category in ((b"\xff", "decode"), (b"not-json\n", "parse")):
            with self.subTest(category=category):
                completed = subprocess.CompletedProcess(["bridge"], 0, stdout=stdout, stderr=b"secret")
                with patch.object(smoke.subprocess, "run", return_value=completed):
                    with self.assertRaises(smoke._SmokeFailure) as context:
                        smoke._run_rpc(ROOT / "bridge", "{}", {}, phase="trusted-rpc")
                self.assertEqual(context.exception.category, category)
                self.assertNotIn("secret", json.dumps(context.exception.payload()))

        decode_error = UnicodeDecodeError("utf-8", b"secret", 0, 1, "secret decode detail")
        with patch.object(smoke.subprocess, "run", side_effect=decode_error):
            with self.assertRaises(smoke._SmokeFailure) as context:
                smoke._run_command(["bridge", "provision", "status"], {}, phase="provision-status")
        self.assertEqual((context.exception.phase, context.exception.category), ("provision-status", "decode"))
        self.assertNotIn("secret", json.dumps(context.exception.payload()))

    def test_assertion_category_is_stage_specific_and_fixed(self) -> None:
        failure = smoke._SmokeFailure("trusted-assertion", "assertion")
        self.assertEqual(failure.payload()["phase"], "trusted-assertion")
        self.assertEqual(failure.payload()["category"], "assertion")
        self.assertIsNone(failure.payload()["returncode"])
        self.assertEqual(failure.payload()["handler_seen"], [])

    def test_invalid_trusted_diagnostic_fails_closed_without_values(self) -> None:
        with self.assertRaises(smoke._SmokeFailure) as context:
            smoke._trusted_error_diagnostic(
                [{"id": 1, "result": {"isError": True, "transport_diagnostic": {"secret": "token"}}}]
            )
        failure = context.exception
        self.assertEqual((failure.phase, failure.category), ("trusted-response", "assertion"))
        self.assertNotIn("token", json.dumps(failure.payload()))

    def test_every_diagnostic_field_rejects_non_scalar_counterexamples(self) -> None:
        counterexamples = {
            "phase": ([], {}, True, 1),
            "outer_type": ([], {}, False, 1),
            "reason_type": ([], {}, True, 1),
            "errno": ([], {}, False, "1", 1.0),
            "ssl_verify_code": ([], {}, True, "20", 20.0, -1),
            "ssl_reason": ([], {}, True, 20),
        }
        for field, values in counterexamples.items():
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    invalid = {**_DIAGNOSTIC, field: value}
                    with self.assertRaises(smoke._SmokeFailure) as context:
                        smoke._trusted_error_diagnostic(
                            [{"id": 1, "result": {"isError": True, "transport_diagnostic": invalid}}]
                        )
                    failure = context.exception
                    self.assertEqual((failure.phase, failure.category), ("trusted-response", "assertion"))
                    self.assertEqual(failure.diagnostic, None)
                    self.assertNotIn("secret", json.dumps(failure.payload()))


if __name__ == "__main__":
    unittest.main()
