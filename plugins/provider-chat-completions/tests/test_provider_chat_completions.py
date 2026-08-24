import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
RUN_PS1 = SCRIPT_DIR / "run.ps1"
sys.path.insert(0, str(SCRIPT_DIR))
import provider_chat_completions as bridge  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    response_body = {}
    status = 200
    redirect_location = None
    seen_headers = {}
    seen_body = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).seen_body = json.loads(self.rfile.read(length))
        type(self).seen_headers = {key.lower(): value for key, value in self.headers.items()}
        if type(self).redirect_location is not None:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_location)
            self.end_headers()
            return
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).response_body).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args):
        return


class _FakeStdin:
    def __init__(self):
        self.closed = False

    def write(self, _data):
        return None

    def flush(self):
        return None

    def close(self):
        self.closed = True


class _FakeConfigProcess:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.returncode = 0

    def communicate(self, timeout):
        if self.stdin is not None:
            raise ValueError("closed stdin was not detached")
        if timeout != bridge.CONFIG_TIMEOUT_SECONDS:
            raise AssertionError("unexpected config timeout")
        return b'{"id":2,"result":{"config":{"model_provider":"test"}}}\n', b""


class _FailedConfigProcess(_FakeConfigProcess):
    def __init__(self):
        super().__init__()
        self.returncode = 7

    def communicate(self, timeout):
        if self.stdin is not None:
            raise ValueError("closed stdin was not detached")
        if timeout != bridge.CONFIG_TIMEOUT_SECONDS:
            raise AssertionError("unexpected config timeout")
        return (
            b"",
            b'{"token":"json-secret","experimental_bearer_token":"provider-secret"}\n'
            b"Authorization: Basic basic-secret\n"
            b"Authorization: Bearer super-secret token=another-secret\n",
        )


class _DeniedWriteStdin(_FakeStdin):
    def write(self, _data):
        raise PermissionError(13, "pipe denied")


class _IOFailureConfigProcess:
    def __init__(self):
        self.stdin = _DeniedWriteStdin()
        self.returncode = None
        self.killed = False
        self.reaped = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout):
        if timeout != bridge.CONFIG_REAP_TIMEOUT_SECONDS:
            raise AssertionError("unexpected reap timeout")
        self.reaped = True
        return b"", b"token=must-not-leak"


class BridgeTests(unittest.TestCase):
    def setUp(self):
        _Handler.status = 200
        _Handler.redirect_location = None
        _Handler.response_body = {
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.provider = {
            "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "experimental_bearer_token": "test-secret",
            "requires_openai_auth": True,
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_windows_launcher_detaches_preflight_stdin(self):
        launcher = RUN_PS1.read_text(encoding="utf-8")
        self.assertIn("function Test-Python38", launcher)
        self.assertIn("$info.RedirectStandardInput = $true", launcher)
        self.assertIn("$process.StandardInput.Close()", launcher)
        self.assertIn("& $python.Source -3 $scriptPath @args", launcher)
        self.assertIn("& $python.Source $scriptPath @args", launcher)
        self.assertNotIn("& $python.Source -3 -c", launcher)
        self.assertNotIn("& $python.Source -c", launcher)

        if platform.system() != "Windows":
            return
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell is not installed")

        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "run.ps1"
            core = Path(directory) / "provider_chat_completions.py"
            launcher.write_text(RUN_PS1.read_text(encoding="utf-8"), encoding="utf-8")
            core.write_text(
                "import sys\n"
                "sys.stdout.write(sys.stdin.read())\n",
                encoding="utf-8",
            )
            payload = (
                '{"model":"chosen-model","messages":[{"role":"user",'
                '"content":"stdin must survive preflight"}],"sentinel":"unchanged"}\n'
            )
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(launcher),
                ],
                input=payload.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(completed.stdout.decode("utf-8"), payload)

    def test_request_keeps_messages_and_owns_stream(self):
        request = bridge.build_request(
            {
                "model": "chosen-model",
                "messages": [{"role": "user", "content": "hello"}],
                "parameters": {"temperature": 0.2, "stream": False},
            }
        )
        self.assertEqual(request["model"], "chosen-model")
        self.assertEqual(request["messages"][0]["content"], "hello")
        self.assertEqual(request["stream"], False)
        self.assertEqual(request["temperature"], 0.2)

    def test_capture_arguments_require_an_absolute_output_path(self):
        with self.assertRaises(bridge.BridgeError) as relative:
            bridge.parse_cli_arguments(["--output-file", "result.json"])
        self.assertEqual(relative.exception.code, "output_file_invalid")

        with self.assertRaises(bridge.BridgeError) as unknown:
            bridge.parse_cli_arguments(["--unexpected"])
        self.assertEqual(unknown.exception.code, "arguments_invalid")

    def test_capture_writes_complete_result_and_bounded_manifest(self):
        content = ("paragraph with a complete response\n" * 5000).rstrip()
        result = {
            "ok": True,
            "model": "chosen-model",
            "content": content,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 1, "completion_tokens": 5000},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            byte_count = bridge.write_result_file(str(path), result)
            saved = path.read_bytes()
            self.assertEqual(saved, bridge._result_bytes(result))
            self.assertEqual(byte_count, len(saved))
            self.assertEqual(json.loads(saved.decode("utf-8")), result)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            manifest = bridge.capture_manifest(result, str(path), byte_count)
            self.assertEqual(manifest["result_file"], str(path))
            self.assertEqual(manifest["bytes"], byte_count)
            self.assertEqual(manifest["content_chars"], len(content))
            self.assertNotIn(content, json.dumps(manifest))

    def test_capture_manifest_does_not_echo_provider_controlled_fields(self):
        result = {
            "ok": True,
            "model": "m" * 100000,
            "content": "short",
            "finish_reason": "r" * 100000,
        }
        manifest = bridge.capture_manifest(result, "/tmp/result.json", 123)
        serialized = json.dumps(manifest)
        self.assertLess(len(serialized), 512)
        self.assertNotIn(result["model"], serialized)
        self.assertNotIn(result["finish_reason"], serialized)

    def test_windows_capture_restricts_acl_before_writing(self):
        with patch.object(bridge.os, "name", "nt"), patch.object(
            bridge.os, "environ", {"USERNAME": "logic"}
        ), patch.object(bridge.subprocess, "run") as run:
            bridge._restrict_output_permissions(r"C:\Temp\result.json")
        run.assert_called_once_with(
            ["icacls", r"C:\Temp\result.json", "/inheritance:r", "/grant:r", "logic:F"],
            check=True,
            stdout=bridge.subprocess.DEVNULL,
            stderr=bridge.subprocess.DEVNULL,
            timeout=bridge.OUTPUT_PERMISSION_TIMEOUT_SECONDS,
        )

    def test_main_capture_mode_emits_manifest_only(self):
        result = {
            "ok": True,
            "model": "chosen-model",
            "content": "complete response that must stay on disk",
            "finish_reason": "stop",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            stdout = io.BytesIO()
            with patch.object(bridge, "process_request", return_value=result), patch.object(
                bridge.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}"))
            ), patch.object(bridge.sys, "stdout", SimpleNamespace(buffer=stdout)):
                return_code = bridge.main(["--output-file", str(path)])

            self.assertEqual(return_code, 0)
            manifest = json.loads(stdout.getvalue().decode("utf-8"))
            self.assertEqual(manifest["result_file"], str(path))
            self.assertNotIn(result["content"], stdout.getvalue().decode("utf-8"))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)

    def test_config_reader_detaches_closed_stdin_before_communicating(self):
        process = _FakeConfigProcess()
        with patch.object(bridge.subprocess, "Popen", return_value=process), patch.object(
            bridge.time, "sleep"
        ):
            config = bridge.read_effective_config("/tmp", "codex")
        self.assertEqual(config, {"model_provider": "test"})
        self.assertTrue(process.stdin is None)

    def test_windows_helper_is_selected_from_codex_home(self):
        environment = {
            "CODEX_HOME": r"C:\Users\logic\.codex",
            "USERPROFILE": r"C:\Users\logic",
        }
        expected = r"C:\Users\logic\.codex\plugins\.plugin-appserver\codex.exe"
        self.assertEqual(bridge.resolve_codex_binary(environment, platform="nt"), expected)

    def test_windows_helper_uses_userprofile_when_codex_home_is_unset(self):
        environment = {"USERPROFILE": r"C:\Users\logic"}
        expected = r"C:\Users\logic\.codex\plugins\.plugin-appserver\codex.exe"
        self.assertEqual(bridge.resolve_codex_binary(environment, platform="nt"), expected)

    def test_explicit_codex_binary_wins_over_windows_helper(self):
        environment = {
            "PROVIDER_CHAT_CODEX_BIN": r"D:\tools\codex.exe",
            "CODEX_HOME": r"C:\Users\logic\.codex",
        }
        self.assertEqual(bridge.resolve_codex_binary(environment, platform="nt"), r"D:\tools\codex.exe")

    def test_windows_helper_never_falls_back_to_path_alias(self):
        expected = r"C:\Users\logic\.codex\plugins\.plugin-appserver\codex.exe"
        self.assertEqual(
            bridge.resolve_codex_binary({"USERPROFILE": r"C:\Users\logic"}, platform="nt"),
            expected,
        )

    def test_explicit_codex_binary_must_be_absolute(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.resolve_codex_binary({"PROVIDER_CHAT_CODEX_BIN": "codex.exe"}, platform="nt")
        self.assertEqual(error.exception.code, "codex_bin_not_absolute")

    def test_permission_error_has_explicit_safe_launch_failure(self):
        launch_error = PermissionError(13, "Access is denied", r"C:\Program Files\WindowsApps\codex.exe")
        launch_error.winerror = 5
        with patch.object(bridge.subprocess, "Popen", side_effect=launch_error):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.read_effective_config("/tmp", r"C:\Program Files\WindowsApps\codex.exe")
        self.assertEqual(error.exception.code, "codex_launch_denied")
        self.assertFalse(error.exception.retryable)
        result = bridge.failure_result(error.exception)
        self.assertEqual(result["diagnostic"]["winerror"], 5)
        self.assertEqual(result["diagnostic"]["executable"], r"C:\Program Files\WindowsApps\codex.exe")

    def test_nonzero_config_exit_captures_redacted_diagnostics(self):
        process = _FailedConfigProcess()
        with patch.object(bridge.subprocess, "Popen", return_value=process), patch.object(
            bridge.time, "sleep"
        ):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.read_effective_config("/tmp", "codex-helper")
        self.assertEqual(error.exception.code, "config_read_failed")
        diagnostic = bridge.failure_result(error.exception)["diagnostic"]
        self.assertEqual(diagnostic["returncode"], 7)
        serialized = json.dumps(diagnostic)
        for secret in (
            "json-secret",
            "provider-secret",
            "basic-secret",
            "super-secret",
            "another-secret",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(diagnostic["stderr"]["present"], True)
        self.assertGreater(diagnostic["stderr"]["bytes"], 0)

    def test_pipe_permission_error_is_reaped_and_not_reported_as_launch_denied(self):
        process = _IOFailureConfigProcess()
        with patch.object(bridge.subprocess, "Popen", return_value=process):
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.read_effective_config("/tmp", "codex-helper")
        self.assertEqual(error.exception.code, "config_read_failed")
        self.assertTrue(process.killed)
        self.assertTrue(process.reaped)
        self.assertNotIn("must-not-leak", json.dumps(bridge.failure_result(error.exception)))

    def test_protected_and_streaming_parameters_are_rejected(self):
        with self.assertRaises(bridge.BridgeError) as protected:
            bridge.build_request({"model": "m", "messages": [{}], "parameters": {"model": "other"}})
        self.assertEqual(protected.exception.code, "protected_parameter")
        with self.assertRaises(bridge.BridgeError) as streaming:
            bridge.build_request({"model": "m", "messages": [{}], "parameters": {"stream": True}})
        self.assertEqual(streaming.exception.code, "streaming_not_supported")

    def test_request_cannot_override_provider_working_directory(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.process_request(
                {"cwd": "/tmp", "model": "m", "messages": [{"role": "user", "content": "x"}]},
                config_resolver=lambda _cwd: {},
            )
        self.assertEqual(error.exception.code, "cwd_override_not_allowed")

    def test_invalid_request_body_is_rejected_before_network(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.post_chat_completion(
                self.provider,
                {"model": "chosen-model", "messages": [{"content": object()}]},
            )
        self.assertEqual(error.exception.code, "request_body_invalid")

    def test_surrogate_request_body_is_rejected_safely(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.post_chat_completion(
                self.provider,
                {"model": "chosen-model", "messages": [{"content": "\ud800"}]},
            )
        self.assertEqual(error.exception.code, "request_body_invalid")

    def test_process_request_validates_before_config_resolution(self):
        calls = []
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.process_request(
                {"model": "", "messages": [{"role": "user", "content": "x"}]},
                config_resolver=lambda _cwd: calls.append(True),
            )
        self.assertEqual(error.exception.code, "model_required")
        self.assertEqual(calls, [])

    def test_huge_timeout_is_a_request_error(self):
        calls = []
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.process_request(
                {
                    "model": "chosen-model",
                    "messages": [{"role": "user", "content": "x"}],
                    "timeout_seconds": 10**10000,
                },
                config_resolver=lambda _cwd: calls.append(True),
            )
        self.assertEqual(error.exception.code, "timeout_invalid")
        self.assertEqual(calls, [])

    def test_one_call_uses_provider_and_returns_normalized_result(self):
        result = bridge.post_chat_completion(
            self.provider,
            {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
        )
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(_Handler.seen_headers["authorization"], "Bearer test-secret")
        self.assertEqual(_Handler.seen_body["model"], "chosen-model")
        self.assertEqual(_Handler.seen_body["stream"], False)

    def test_env_key_and_headers_are_resolved_without_fallback(self):
        old = os.environ.get("TEST_PROVIDER_KEY")
        os.environ["TEST_PROVIDER_KEY"] = "env-secret"
        try:
            provider = dict(self.provider)
            provider.pop("experimental_bearer_token")
            provider["env_key"] = "TEST_PROVIDER_KEY"
            provider["http_headers"] = {"X-Provider": "configured"}
            result = bridge.post_chat_completion(
                provider,
                {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(_Handler.seen_headers["authorization"], "Bearer env-secret")
            self.assertEqual(_Handler.seen_headers["x-provider"], "configured")
        finally:
            if old is None:
                os.environ.pop("TEST_PROVIDER_KEY", None)
            else:
                os.environ["TEST_PROVIDER_KEY"] = old

    def test_forbidden_and_duplicate_provider_headers_are_rejected(self):
        with self.assertRaises(bridge.BridgeError) as forbidden:
            bridge.resolve_headers({"http_headers": {"Host": "example.test"}})
        self.assertEqual(forbidden.exception.code, "headers_invalid")

        with self.assertRaises(bridge.BridgeError) as duplicate:
            bridge.resolve_headers({"http_headers": {"X-Test": "one", "x-test": "two"}})
        self.assertEqual(duplicate.exception.code, "headers_invalid")

    def test_error_does_not_include_response_body_or_secret(self):
        _Handler.status = 401
        _Handler.response_body = {"error": {"message": "test-secret should not leak"}}
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.post_chat_completion(
                self.provider,
                {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
            )
        result = bridge.failure_result(error.exception)
        self.assertEqual(result, {"ok": False, "stage": "http", "code": "http_error", "retryable": False, "http_status": 401})
        self.assertNotIn("test-secret", json.dumps(result))

    def test_credential_bearing_redirect_is_not_followed(self):
        target_handler = type(
            "TargetHandler",
            (_Handler,),
            {"request_count": 0, "redirect_location": None},
        )

        def target_post(handler):
            type(handler).request_count += 1
            handler.send_response(200)
            handler.end_headers()

        target_handler.do_POST = target_post
        target = ThreadingHTTPServer(("127.0.0.1", 0), target_handler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        try:
            _Handler.redirect_location = f"http://127.0.0.1:{target.server_port}/final"
            with self.assertRaises(bridge.BridgeError) as error:
                bridge.post_chat_completion(
                    self.provider,
                    {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
                )
            self.assertEqual(error.exception.code, "redirect_not_allowed")
            self.assertEqual(target_handler.request_count, 0)
            self.assertEqual(_Handler.seen_headers["authorization"], "Bearer test-secret")
        finally:
            _Handler.redirect_location = None
            target.shutdown()
            target.server_close()
            target_thread.join(timeout=2)

    def test_malformed_provider_url_is_rejected(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.build_endpoint({"base_url": "https://bad\x00.example/v1"})
        self.assertEqual(error.exception.code, "base_url_invalid")

    def test_missing_content_is_protocol_failure(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.normalize_response({"choices": [{"message": {}, "finish_reason": "stop"}]})
        self.assertEqual(error.exception.code, "content_missing")

    def test_non_json_response_constants_are_rejected(self):
        _Handler.response_body = {
            "model": "test-model",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"completion_tokens": float("nan")},
        }
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.post_chat_completion(
                self.provider,
                {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
            )
        self.assertEqual(error.exception.code, "invalid_json")


if __name__ == "__main__":
    unittest.main()
