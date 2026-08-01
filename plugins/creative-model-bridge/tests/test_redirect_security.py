from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))
from bridge import Bridge, BridgeError  # noqa: E402


class RedirectHandler(BaseHTTPRequestHandler):
    status = 302
    target = ""
    seen_authorizations: list[str | None] = []

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _redirect(self) -> None:
        self.__class__.seen_authorizations.append(self.headers.get("Authorization"))
        self.send_response(self.__class__.status)
        self.send_header("Location", self.__class__.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._redirect()

    def do_POST(self) -> None:  # noqa: N802
        self._redirect()


class DestinationHandler(BaseHTTPRequestHandler):
    seen_authorizations: list[str | None] = []

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _record(self) -> None:
        self.__class__.seen_authorizations.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = json.dumps({"data": [{"id": "unexpected"}]}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._record()

    def do_POST(self) -> None:  # noqa: N802
        self._record()


def run_server(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class RedirectSecurityTests(unittest.TestCase):
    def test_all_http_redirect_codes_are_rejected_without_cross_origin_forwarding(self) -> None:
        destination, destination_thread = run_server(DestinationHandler)
        redirect, redirect_thread = run_server(RedirectHandler)
        try:
            RedirectHandler.target = f"http://127.0.0.1:{destination.server_port}/v1/next"
            DestinationHandler.seen_authorizations = []
            RedirectHandler.seen_authorizations = []
            with tempfile.TemporaryDirectory(prefix="creative-redirect-") as temporary:
                config = Path(temporary) / "config.toml"
                config.write_text(
                    "[shell_environment_policy.set]\n"
                    'CREATIVE_MODEL_PROVIDER = "redirect"\n'
                    'CREATIVE_MODEL_DEFAULT = "opaque-model"\n\n'
                    "[model_providers.redirect]\n"
                    f'base_url = "http://127.0.0.1:{redirect.server_port}/v1"\n'
                    'wire_api = "responses"\n'
                    'env_key = "BRIDGE_REDIRECT_KEY"\n',
                    encoding="utf-8",
                )
                bridge = Bridge(config)
                for status in (301, 302, 303, 307, 308):
                    RedirectHandler.status = status
                    with patch.dict("os.environ", {"BRIDGE_REDIRECT_KEY": "placeholder-key"}):
                        with self.assertRaises(BridgeError) as get_error:
                            bridge.creative_models()
                        with self.assertRaises(BridgeError) as post_error:
                            bridge.creative_generate({"task": "写作"})
                    self.assertIn("redirect refused", str(get_error.exception))
                    self.assertIn("redirect refused", str(post_error.exception))
            self.assertEqual(len(DestinationHandler.seen_authorizations), 0)
            self.assertEqual(len(RedirectHandler.seen_authorizations), 10)
            self.assertTrue(all(value == "Bearer placeholder-key" for value in RedirectHandler.seen_authorizations))
        finally:
            redirect.shutdown()
            redirect.server_close()
            destination.shutdown()
            destination.server_close()
            redirect_thread.join(timeout=2)
            destination_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
