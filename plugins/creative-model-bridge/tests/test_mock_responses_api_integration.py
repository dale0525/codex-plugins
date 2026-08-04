from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))
from bridge import Bridge  # noqa: E402


class MockResponsesHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict[str, object] | None]] = []
    request_headers: list[tuple[str, dict[str, str]]] = []
    response_payload: dict[str, object] | None = None

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.calls.append((self.path, None))
        self.__class__.request_headers.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
        body = {"object": "list", "data": [{"id": "mock/model-v1"}]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-request-id", "models-request")
        encoded = json.dumps(body).encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size).decode("utf-8"))
        self.__class__.calls.append((self.path, payload))
        self.__class__.request_headers.append((self.path, {key.lower(): value for key, value in self.headers.items()}))
        body = self.__class__.response_payload or {
            "id": "chat-request",
            "choices": [{"delta": {"content": "原样返回"}, "finish_reason": "stop"}],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(("data: " + json.dumps(body, ensure_ascii=False) + "\n\n").encode("utf-8"))
        if isinstance(body.get("usage"), dict):
            usage = {"choices": [], "usage": body["usage"]}
            self.wfile.write(("data: " + json.dumps(usage, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")


class MockResponsesIntegrationTests(unittest.TestCase):
    def test_models_and_responses_use_standard_paths_and_payload(self) -> None:
        MockResponsesHandler.calls = []
        MockResponsesHandler.request_headers = []
        MockResponsesHandler.response_payload = None
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="creative-bridge-http-") as temporary:
                config = Path(temporary) / "config.toml"
                config.write_text(
                    "[shell_environment_policy.set]\n"
                    'CREATIVE_MODEL_PROVIDER = "mock"\n'
                    'CREATIVE_MODEL_DEFAULT = "mock/model-v1"\n\n'
                    "[model_providers.mock]\n"
                    f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                    'wire_api = "responses"\n'
                    'env_key = "BRIDGE_INTEGRATION_KEY"\n',
                    encoding="utf-8",
                )
                bridge = Bridge(config)
                with patch.dict("os.environ", {"BRIDGE_INTEGRATION_KEY": "placeholder-key"}):
                    models = bridge.creative_models()
                    generated = bridge.creative_generate({"task": "写作", "temperature": 0.2})
                self.assertEqual(models["models"], ["mock/model-v1"])
                self.assertEqual(generated["text"], "原样返回")
                self.assertEqual([path for path, _ in MockResponsesHandler.calls], ["/v1/models", "/v1/chat/completions"])
                self.assertEqual(
                    [path for path, _ in MockResponsesHandler.request_headers],
                    ["/v1/models", "/v1/chat/completions"],
                )
                for _, headers in MockResponsesHandler.request_headers:
                    self.assertEqual(headers["user-agent"], "creative-model-bridge/0.1.16")
                    self.assertFalse(
                        any(
                            key.startswith(("codex", "x-codex", "originator")) or key in {"session", "x-session"}
                            for key in headers
                        )
                    )
                response_payload = MockResponsesHandler.calls[1][1]
                self.assertEqual(response_payload["model"], "mock/model-v1")
                self.assertEqual(response_payload["temperature"], 0.2)
                self.assertEqual(response_payload["max_tokens"], 60000)
                self.assertTrue(response_payload["stream"])
                self.assertEqual(response_payload["stream_options"], {"include_usage": True})
                self.assertEqual(response_payload["messages"][0], {"role": "system", "content": "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_compatible_cpa_text_shape_round_trips_verbatim(self) -> None:
        MockResponsesHandler.calls = []
        MockResponsesHandler.request_headers = []
        MockResponsesHandler.response_payload = {
            "id": "response-cpa-shape",
            "choices": [{"delta": {"content": "第一行\n第二行"}, "finish_reason": "stop"}],
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), MockResponsesHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="creative-bridge-http-cpa-") as temporary:
                config = Path(temporary) / "config.toml"
                config.write_text(
                    "[shell_environment_policy.set]\n"
                    'CREATIVE_MODEL_PROVIDER = "mock"\n'
                    'CREATIVE_MODEL_DEFAULT = "mock/model-v1"\n\n'
                    "[model_providers.mock]\n"
                    f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
                    'wire_api = "responses"\n'
                    'env_key = "BRIDGE_INTEGRATION_KEY"\n',
                    encoding="utf-8",
                )
                with patch.dict("os.environ", {"BRIDGE_INTEGRATION_KEY": "placeholder-key"}):
                    generated = Bridge(config).creative_generate({"task": "写作"})
                self.assertEqual(generated["text"], "第一行\n第二行")
                self.assertEqual(generated["request_id"], "response-cpa-shape")
        finally:
            MockResponsesHandler.response_payload = None
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
