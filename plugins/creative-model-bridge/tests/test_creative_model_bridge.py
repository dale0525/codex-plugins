from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import creative_model_bridge as cmb  # noqa: E402


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, chunks: list[bytes] | None = None, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.status = status
        self.code = status
        self.headers = headers or {"content-type": "text/event-stream"}
        self.chunks = chunks
        self.closed = False
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if self.chunks is not None:
            if not self.chunks:
                return b""
            return self.chunks.pop(0)
        if self._offset >= len(self.body):
            return b""
        if size < 0:
            size = len(self.body) - self._offset
        chunk = self.body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[object, float]] = []

    def __call__(self, request: object, timeout: float) -> FakeResponse:
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class SubprocessHandler(BaseHTTPRequestHandler):
    calls: list[dict[str, object]] = []

    def log_message(self, _format: str, *args: object) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.__class__.calls.append(json.loads(self.rfile.read(length).decode("utf-8")))
        events = [
            {"id": "subprocess-1", "choices": [{"delta": {"reasoning_content": "思考"}}]},
            {"choices": [{"delta": {"content": "输出\n",}, "finish_reason": "stop"}]},
        ]
        body = "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)
        body += "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def sse(*events: dict[str, object] | str) -> bytes:
    frames: list[str] = []
    for event in events:
        if isinstance(event, str):
            data = event
        else:
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        frames.append(f"data: {data}\n\n")
    return "".join(frames).encode("utf-8")


class CreativeModelBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cmb-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.config.write_text(
            "[shell_environment_policy.set]\n"
            'CREATIVE_MODEL_PROVIDER = "provider"\n'
            'CREATIVE_MODEL_DEFAULT = "default/opaque:model"\n\n'
            "[model_providers.provider]\n"
            'base_url = "https://provider.example/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "PROVIDER_KEY"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, **extra: object) -> dict[str, object]:
        value: dict[str, object] = {"task": "写一个短场景"}
        value.update(extra)
        return value

    def generate(self, request: dict[str, object], response: FakeResponse | Exception | None = None) -> tuple[dict[str, object], FakeOpener]:
        response = response or FakeResponse(
            sse(
                {"id": "request-1", "choices": [{"delta": {"content": "完成"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                "[DONE]",
            )
        )
        opener = FakeOpener(response)
        with patch.dict(os.environ, {"PROVIDER_KEY": "placeholder-key"}, clear=True):
            result = cmb.generate(request, config_path=self.config, opener=opener)
        return result, opener

    def test_exact_payload_uses_opaque_model_stream_and_order(self) -> None:
        result, opener = self.generate(
            self.request(
                model="vendor/name:v9+opaque",
                constraints=["keep tense"],
                output_spec={"format": "markdown"},
                context_text=["first", "second"],
                temperature=0.4,
                max_tokens=123,
            )
        )
        self.assertEqual(result["output"], "完成")
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(timeout, 60.0)
        assert hasattr(request, "full_url")
        self.assertEqual(request.full_url, "https://provider.example/v1/chat/completions")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "vendor/name:v9+opaque")
        self.assertTrue(body["stream"])
        self.assertEqual(body["max_tokens"], 60_000)
        self.assertEqual(body["temperature"], 0.4)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["messages"][0], {"role": "system", "content": cmb.SYSTEM_PROMPT})
        prompt = body["messages"][1]["content"]
        self.assertLess(prompt.index("任务:"), prompt.index("约束:"))
        self.assertLess(prompt.index("约束:"), prompt.index("输出规格:"))
        self.assertLess(prompt.index("输出规格:"), prompt.index("上下文文字:"))
        self.assertLess(prompt.index("first"), prompt.index("second"))

    def test_system_none_omits_system_message(self) -> None:
        _, opener = self.generate(self.request(system_mode="none"))
        request, _ = opener.calls[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["messages"], [{"role": "user", "content": "任务:\n写一个短场景"}])

    def test_context_files_are_ordered_bounded_decoded_and_explicitly_delimited(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes(b"\xef\xbb\xbffirst\n")
        second.write_bytes("第二".encode("gb18030"))
        preview_request = self.request(context_files=[str(second), str(first)])
        provider = cmb.load_provider(self.config)
        payload, _, files = cmb.build_payload(preview_request, provider)
        prompt = payload["messages"][1]["content"]
        self.assertIn("第二", prompt)
        self.assertIn("first", prompt)
        self.assertLess(prompt.index("第二"), prompt.index("first"))
        self.assertIn("--- BEGIN FILE: ", prompt)
        self.assertIn("--- END FILE: ", prompt)
        self.assertEqual([item.encoding for item in files], ["gb18030", "utf-8-sig"])

    def test_binary_and_symlink_files_are_rejected(self) -> None:
        binary = self.root / "image.png"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        with self.assertRaises(cmb.FileContextError):
            cmb.read_context_files([str(binary)])
        target = self.root / "target.txt"
        target.write_text("safe", encoding="utf-8")
        link = self.root / "link.txt"
        link.symlink_to(target)
        with self.assertRaises(cmb.FileContextError):
            cmb.read_context_files([str(link)])

    def test_reasoning_and_output_are_accumulated_separately_verbatim(self) -> None:
        exact = "line 1\nline 2\n\n"
        response = FakeResponse(
            sse(
                {"id": "reasoning-1", "choices": [{"delta": {"reasoning_content": "think ", "content": "line 1"}}]},
                {"choices": [{"delta": {"reasoning": "more", "content": "\nline 2\n\n"}, "finish_reason": "stop"}], "usage": {"total_tokens": 8}},
                "[DONE]",
            ),
            chunks=[b"data: {\"id\":\"reasoning-1\",\"choices\":[{\"delta\":{\"reasoning_content\":\"think \",\"content\":\"line 1\"}}]}\r\n\r\n", sse({"choices": [{"delta": {"reasoning": "more", "content": "\nline 2\n\n"}, "finish_reason": "stop"}], "usage": {"total_tokens": 8}}, "[DONE]")[0:]
            ],
        )
        result, _ = self.generate(self.request(), response)
        self.assertEqual(result["reasoning"], "think more")
        self.assertEqual(result["output"], exact)
        self.assertEqual(result["usage"], {"total_tokens": 8})
        self.assertEqual(result["request_id"], "reasoning-1")

    def test_crlf_split_and_multiline_data_are_one_sse_event(self) -> None:
        first = b'data: {"id":"split-1","choices":[\r'
        second = b'\ndata: {"delta":{"content":"split"},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n'
        response = FakeResponse(b"", chunks=[first, second])
        result, opener = self.generate(self.request(), response)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result["output"], "split")
        self.assertEqual(result["request_id"], "split-1")

    def test_subprocess_writes_one_utf8_json_object_under_cp1252(self) -> None:
        SubprocessHandler.calls = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), SubprocessHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.config.write_text(
                "[shell_environment_policy.set]\n"
                'CREATIVE_MODEL_PROVIDER = "provider"\n'
                'CREATIVE_MODEL_DEFAULT = "default/opaque:model"\n\n'
                "[model_providers.provider]\n"
                f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                'wire_api = "responses"\n'
                'env_key = "PROVIDER_KEY"\n',
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "CODEX_HOME": str(self.root),
                    "PROVIDER_KEY": "placeholder-key",
                    "PYTHONIOENCODING": "cp1252",
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            process = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "scripts" / "creative_model_bridge.py")],
                input=json.dumps(self.request(), ensure_ascii=False).encode("utf-8"),
                capture_output=True,
                env=environment,
                timeout=10,
            )
            self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(process.stdout.count(b"\n"), 1)
            result = json.loads(process.stdout.decode("utf-8"))
            self.assertEqual(result["reasoning"], "思考")
            self.assertEqual(result["output"], "输出\n")
            self.assertEqual(len(SubprocessHandler.calls), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_malformed_stream_and_http_error_are_not_retried(self) -> None:
        malformed = FakeResponse(b"data: {bad-json}\n\n")
        opener = FakeOpener(malformed)
        with patch.dict(os.environ, {"PROVIDER_KEY": "placeholder-key"}, clear=True), self.assertRaises(cmb.BridgeError):
            cmb.generate(self.request(), config_path=self.config, opener=opener)
        self.assertEqual(len(opener.calls), 1)

        error = urllib.error.HTTPError("https://provider.example/v1/chat/completions", 429, "secret body", {}, None)
        opener = FakeOpener(error)
        with patch.dict(os.environ, {"PROVIDER_KEY": "placeholder-key"}, clear=True), self.assertRaises(cmb.BridgeError) as raised:
            cmb.generate(self.request(), config_path=self.config, opener=opener)
        self.assertIn("429", str(raised.exception))
        self.assertNotIn("secret body", str(raised.exception))
        self.assertEqual(len(opener.calls), 1)

    def test_default_model_fixed_limit_and_credential_precedence(self) -> None:
        response = FakeResponse(sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]"))
        opener = FakeOpener(response)
        with patch.dict(os.environ, {"PROVIDER_KEY": "provider-key", "CREATIVE_MODEL_API_KEY": "fixed-key"}, clear=True):
            cmb.generate(self.request(), config_path=self.config, opener=opener)
        request, _ = opener.calls[0]
        self.assertEqual(request.headers["Authorization"], "Bearer provider-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "gemini-3-pro")
        self.assertEqual(body["max_tokens"], 60_000)

        for supplied_limits in (
            {"max_tokens": 1},
            {"max_output_tokens": 2},
            {"max_tokens": 3, "max_output_tokens": 4},
            {"max_tokens": "ignored", "max_output_tokens": None},
        ):
            response = FakeResponse(sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]"))
            opener = FakeOpener(response)
            with patch.dict(os.environ, {"PROVIDER_KEY": "provider-key"}, clear=True):
                cmb.generate(self.request(**supplied_limits), config_path=self.config, opener=opener)
            request, _ = opener.calls[0]
            self.assertEqual(json.loads(request.data.decode("utf-8"))["max_tokens"], 60_000)

        response = FakeResponse(sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]"))
        opener = FakeOpener(response)
        with patch.dict(os.environ, {"CREATIVE_MODEL_API_KEY": "fixed-key"}, clear=True):
            cmb.generate(self.request(model="opaque/model"), config_path=self.config, opener=opener)
        request, _ = opener.calls[0]
        self.assertEqual(request.headers["Authorization"], "Bearer fixed-key")
        self.assertEqual(json.loads(request.data.decode("utf-8"))["model"], "opaque/model")

        missing = self.root / "missing.toml"
        missing.write_text(
            "[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = \"provider\"\n\n"
            "[model_providers.provider]\nbase_url = \"https://provider.example/v1\"\n"
            "experimental_bearer_token = \"development-only\"\n",
            encoding="utf-8",
        )
        response = FakeResponse(sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, "[DONE]"))
        opener = FakeOpener(response)
        with patch.dict(os.environ, {}, clear=True):
            result = cmb.generate(self.request(model="opaque/default"), config_path=missing, opener=opener)
        self.assertEqual(result["output"], "ok")
        self.assertEqual(opener.calls[0][0].headers["Authorization"], "Bearer development-only")


if __name__ == "__main__":
    unittest.main()
