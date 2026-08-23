import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
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

    def test_config_reader_detaches_closed_stdin_before_communicating(self):
        process = _FakeConfigProcess()
        with patch.object(bridge.subprocess, "Popen", return_value=process), patch.object(
            bridge.time, "sleep"
        ):
            config = bridge.read_effective_config("/tmp", "codex")
        self.assertEqual(config, {"model_provider": "test"})
        self.assertTrue(process.stdin is None)

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
