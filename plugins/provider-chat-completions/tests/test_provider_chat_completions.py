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
            "http_headers": {"Authorization": "Bearer test-secret"},
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

    def test_cache_loader_reads_sync_file_without_appserver_or_permission_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "credential.json"
            cache.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider": "company",
                        "base_url": "https://provider.example/v1",
                        "headers": {"Authorization": "Bearer cached-secret"},
                        "env_http_headers": {},
                        "query_params": {},
                        "requires_openai_auth": True,
                        "fingerprint": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(root, 0o755)
            os.chmod(cache, 0o644)
            provider = bridge.load_cached_provider({"PROVIDER_CHAT_CREDENTIAL_FILE": str(cache)})
        self.assertEqual(provider["base_url"], "https://provider.example/v1")
        self.assertEqual(provider["http_headers"]["Authorization"], "Bearer cached-secret")

    def test_main_accepts_power_shell_json_encodings(self):
        request = {
            "model": "chosen-model",
            "messages": [{"role": "user", "content": "你好"}],
        }
        request_text = json.dumps(request, ensure_ascii=False)
        encoded_inputs = [
            request_text.encode("utf-8"),
            b"\xef\xbb\xbf" + request_text.encode("utf-8"),
            request_text.encode("utf-16le"),
            request_text.encode("utf-16be"),
            b"\xff\xfe" + request_text.encode("utf-16le"),
            b"\xfe\xff" + request_text.encode("utf-16be"),
        ]
        for encoded in encoded_inputs:
            seen = []
            stdout = io.BytesIO()
            with patch.object(
                bridge, "process_request", side_effect=lambda value: seen.append(value) or {"ok": True}
            ), patch.object(
                bridge.sys,
                "stdin",
                SimpleNamespace(buffer=io.BytesIO(encoded)),
            ), patch.object(bridge.sys, "stdout", SimpleNamespace(buffer=stdout)):
                return_code = bridge.main([])
            self.assertEqual(return_code, 0)
            self.assertEqual(seen, [request])
            self.assertEqual(json.loads(stdout.getvalue().decode("utf-8")), {"ok": True})


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
                provider_resolver=lambda: {},
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
                provider_resolver=lambda: calls.append(True),
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
                provider_resolver=lambda: calls.append(True),
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

    def test_remote_http_credentials_are_rejected_before_network(self):
        provider = {
            "base_url": "http://provider.example/v1",
            "http_headers": {"Authorization": "Bearer test-secret"},
        }
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.post_chat_completion(
                provider,
                {"model": "chosen-model", "messages": [{"role": "user", "content": "hello"}]},
            )
        self.assertEqual(error.exception.code, "insecure_http_credentials")

    def test_credential_query_parameters_are_rejected(self):
        with self.assertRaises(bridge.BridgeError) as error:
            bridge.build_endpoint({"base_url": "https://provider.example/v1?api_key=secret"})
        self.assertEqual(error.exception.code, "credential_in_url_rejected")

    def test_env_key_and_headers_are_resolved_without_fallback(self):
        old = os.environ.get("TEST_PROVIDER_KEY")
        os.environ["TEST_PROVIDER_KEY"] = "env-secret"
        try:
            provider = dict(self.provider)
            provider.pop("http_headers")
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
