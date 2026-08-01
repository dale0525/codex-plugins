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

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.calls.append((self.path, None))
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
        body = {
            "id": "response-request",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "原样返回"}]}],
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MockResponsesIntegrationTests(unittest.TestCase):
    def test_models_and_responses_use_standard_paths_and_payload(self) -> None:
        MockResponsesHandler.calls = []
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
                self.assertEqual([path for path, _ in MockResponsesHandler.calls], ["/v1/models", "/v1/responses"])
                response_payload = MockResponsesHandler.calls[1][1]
                self.assertEqual(response_payload["model"], "mock/model-v1")
                self.assertEqual(response_payload["temperature"], 0.2)
                self.assertEqual(response_payload["instructions"], "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
