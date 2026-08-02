from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bridge import Bridge, BridgeError  # noqa: E402
import smoke_creative_model_bridge as smoke  # noqa: E402
import transport_client  # noqa: E402


class _TLSModelsHandler(BaseHTTPRequestHandler):
    seen: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.seen.append(self.path)
        payload = json.dumps({"object": "list", "data": [{"id": "tls/model"}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TransportClientTests(unittest.TestCase):
    def test_unset_ca_override_keeps_default_context_path(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(transport_client._ssl_context())

    def test_explicit_context_preserves_proxy_and_no_redirect_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cafile = Path(temporary) / "ca.pem"
            cafile.write_text("not a certificate", encoding="utf-8")
            context = ssl.create_default_context()
            with patch.dict(
                os.environ,
                {"SSL_CERT_FILE": str(cafile), "HTTPS_PROXY": "http://proxy.invalid:8080"},
                clear=True,
            ):
                with patch.object(transport_client.ssl, "create_default_context", return_value=context) as factory:
                    opener = transport_client._opener_without_redirects()
            factory.assert_called_once_with(cafile=str(cafile))
        self.assertTrue(any(isinstance(handler, urllib.request.HTTPSHandler) for handler in opener.handlers))
        self.assertTrue(any(isinstance(handler, urllib.request.ProxyHandler) for handler in opener.handlers))
        self.assertTrue(any(isinstance(handler, transport_client._NoRedirectHandler) for handler in opener.handlers))
        self.assertFalse(any(type(handler) is urllib.request.HTTPRedirectHandler for handler in opener.handlers))
        https = next(handler for handler in opener.handlers if isinstance(handler, urllib.request.HTTPSHandler))
        self.assertTrue(https._context.check_hostname)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)

    def test_explicit_ca_root_succeeds_after_stale_default_context_failure(self) -> None:
        _TLSModelsHandler.seen = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ca_cert, server_cert, server_key, unrelated_cert = smoke._make_tls_material(root)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile=server_cert, keyfile=server_key)
            server = smoke._LocalTLSHTTPServer(("127.0.0.1", 0), _TLSModelsHandler)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"https://127.0.0.1:{server.server_port}/v1/models"
                request = urllib.request.Request(url)
                with patch.dict(
                    os.environ,
                    {"NO_PROXY": "*", "no_proxy": "*"},
                    clear=True,
                ):
                    stale = urllib.request.build_opener(transport_client._NoRedirectHandler())
                    os.environ["SSL_CERT_FILE"] = str(ca_cert)
                    with self.assertRaises(urllib.error.URLError) as stale_error:
                        stale.open(request, timeout=2)
                    reason = stale_error.exception.reason
                    self.assertIsInstance(reason, ssl.SSLCertVerificationError)
                    self.assertEqual(reason.verify_code, 20)
                    self.assertEqual(_TLSModelsHandler.seen, [])

                    with transport_client._open_without_redirects(request, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        response.read()
                    self.assertEqual(_TLSModelsHandler.seen, ["/v1/models"])

                    os.environ["SSL_CERT_FILE"] = str(unrelated_cert)
                    with self.assertRaises(urllib.error.URLError) as unrelated_error:
                        transport_client._open_without_redirects(request, timeout=2)
                    unrelated_reason = unrelated_error.exception.reason
                    self.assertIsInstance(unrelated_reason, ssl.SSLCertVerificationError)
                    self.assertEqual(unrelated_reason.verify_code, 20)
                    self.assertEqual(_TLSModelsHandler.seen, ["/v1/models"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_invalid_explicit_ca_is_sanitized_by_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.toml"
            config.write_text(
                "[shell_environment_policy.set]\n"
                'CREATIVE_MODEL_PROVIDER = "invalid-ca"\n'
                'CREATIVE_MODEL_DEFAULT = "tls/model"\n\n'
                "[model_providers.invalid-ca]\n"
                'base_url = "https://127.0.0.1:1/v1"\n'
                'wire_api = "responses"\n'
                'env_key = "TLS_TEST_KEY"\n',
                encoding="utf-8",
            )
            missing = Path(temporary) / "missing-secret-ca.pem"
            with patch.dict(
                os.environ,
                {"TLS_TEST_KEY": "placeholder-key", "SSL_CERT_FILE": str(missing)},
                clear=True,
            ):
                with self.assertRaises(BridgeError) as context:
                    Bridge(config).creative_models()
            self.assertEqual(str(context.exception), "Responses API request could not be completed")
            self.assertNotIn(str(missing), str(context.exception))
            self.assertIsNone(context.exception.transport_diagnostic)


if __name__ == "__main__":
    unittest.main()
