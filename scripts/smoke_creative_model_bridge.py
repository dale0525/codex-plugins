#!/usr/bin/env python3
"""Run the checked-in MCP command through offline and local HTTPS paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


def _run_rpc(binary: Path, payload: str, environment: dict[str, str]) -> list[dict[str, object]]:
    result = subprocess.run(
        [str(binary)],
        cwd=binary.parent,
        input=payload,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"bridge exited with {result.returncode}")
    try:
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid MCP output: {result.stdout}") from error


def _openssl_executable() -> str | None:
    candidates = [shutil.which("openssl")]
    plugin_root = Path(__file__).resolve().parents[1] / "plugins/creative-model-bridge/.pixi/envs/default/bin/openssl"
    candidates.append(str(plugin_root) if plugin_root.is_file() else None)
    return next((item for item in candidates if item), None)


def _make_tls_material(root: Path) -> tuple[Path, Path, Path]:
    openssl = _openssl_executable()
    if not openssl:
        raise RuntimeError("TLS smoke requires openssl; no executable was found")
    ca_key, ca_cert = root / "ca.key", root / "ca.pem"
    server_key, server_csr, server_cert = root / "server.key", root / "server.csr", root / "server.pem"
    extensions = root / "server.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost,IP:127.0.0.1\n",
        encoding="utf-8",
    )
    commands = [
        [openssl, "genrsa", "-out", str(ca_key), "2048"],
        [openssl, "req", "-x509", "-new", "-nodes", "-key", str(ca_key), "-sha256", "-days", "1", "-subj", "/CN=Creative Smoke CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-out", str(ca_cert)],
        [openssl, "genrsa", "-out", str(server_key), "2048"],
        [openssl, "req", "-new", "-key", str(server_key), "-subj", "/CN=localhost", "-out", str(server_csr)],
        [openssl, "x509", "-req", "-in", str(server_csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(server_cert), "-days", "1", "-sha256", "-extfile", str(extensions)],
    ]
    try:
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"TLS smoke could not create ephemeral certificates with {openssl}: {error}") from error
    return ca_cert, server_cert, server_key


class _TLSHandler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _write(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _auth(self) -> bool:
        return self.headers.get("Authorization") == "Bearer placeholder-smoke-token"

    def do_GET(self) -> None:  # noqa: N802
        _TLSHandler.seen.append((self.command, self.path))
        if self.path != "/v1/models" or not self._auth():
            self.send_error(401)
            return
        self._write({"object": "list", "data": [{"id": "smoke/model"}]})

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        _TLSHandler.seen.append((self.command, self.path))
        if self.path != "/v1/responses" or not self._auth():
            self.send_error(401)
            return
        self._write({"id": "tls-response", "output_text": "tls smoke success", "usage": {"input_tokens": 4, "output_tokens": 3}})


def _tls_smoke(binary: Path, root: Path) -> None:
    ca_cert, server_cert, server_key = _make_tls_material(root)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=server_cert, keyfile=server_key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TLSHandler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        home = root / "tls-home"
        home.mkdir()
        (home / "config.toml").write_text(
            "[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = \"smoke\"\nCREATIVE_MODEL_DEFAULT = \"smoke/model\"\n\n"
            "[model_providers.smoke]\n"
            f"base_url = \"https://127.0.0.1:{server.server_port}/v1\"\nwire_api = \"responses\"\nenv_key = \"SMOKE_BEARER\"\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(home), "SSL_CERT_FILE": str(ca_cert), "SMOKE_BEARER": "placeholder-smoke-token"})
        requests = "\n".join([
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"creative_models","arguments":{}}}',
            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"creative_generate","arguments":{"task":"TLS smoke"}}}',
            "",
        ])
        responses = _run_rpc(binary, requests, environment)
        if [item.get("id") for item in responses] != [1, 2]:
            raise RuntimeError(f"TLS smoke returned unexpected IDs: {responses}")
        models = responses[0].get("result", {}).get("structuredContent", {})
        generated = responses[1].get("result", {}).get("structuredContent", {})
        if models.get("models") != ["smoke/model"] or generated.get("text") != "tls smoke success" or not generated.get("usage"):
            raise RuntimeError(f"TLS smoke response assertion failed: {responses}")
        if _TLSHandler.seen != [("GET", "/v1/models"), ("POST", "/v1/responses")]:
            raise RuntimeError(f"TLS smoke did not exercise both endpoints: {_TLSHandler.seen}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main() -> int:
    binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if binary is None:
        raise SystemExit("usage: smoke_creative_model_bridge.py BINARY")
    try:
        with tempfile.TemporaryDirectory(prefix="creative-smoke-") as temporary:
            root = Path(temporary)
            environment = os.environ.copy()
            # Keep the offline/provision phase deterministic; the TLS phase
            # below installs its own ephemeral CA explicitly.
            environment.pop("SSL_CERT_FILE", None)
            environment.pop("CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE", None)
            environment["CREATIVE_MODEL_BRIDGE_BIN"] = str(binary)
            environment["CREATIVE_MODEL_BRIDGE_OFFLINE"] = "1"
            home = root / "offline-home"
            home.mkdir()
            (home / "config.toml").write_text(
                "[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = \"smoke\"\nCREATIVE_MODEL_DEFAULT = \"smoke/model\"\n\n"
                "[model_providers.smoke]\nbase_url = \"http://offline.invalid/v1\"\nwire_api = \"responses\"\n",
                encoding="utf-8",
            )
            environment["CODEX_HOME"] = str(home)
            payload = "\n".join([
                '{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
                '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"creative_preview","arguments":{"task":"offline smoke","model":"smoke-model"}}}',
                "",
            ])
            responses = _run_rpc(binary, payload, environment)
            if [item.get("id") for item in responses] != [1, 2, 3] or "creative_preview" not in json.dumps(responses, ensure_ascii=False):
                raise RuntimeError(f"unexpected MCP offline smoke output: {responses}")
            preview = responses[2].get("result", {}).get("structuredContent", {})
            if preview.get("network") is not False:
                raise RuntimeError(f"preview smoke was not offline: {preview}")
            setup = subprocess.run([str(binary), "provision", "setup", "--yes"], capture_output=True, text=True, env=environment, timeout=90)
            if setup.returncode != 0:
                raise RuntimeError(setup.stderr or "provision setup failed")
            status = subprocess.run([str(binary), "provision", "status"], capture_output=True, text=True, env=environment, timeout=90)
            if status.returncode != 0 or '"status": "installed"' not in status.stdout:
                raise RuntimeError(status.stderr or status.stdout or "provision status failed")
            uninstall = subprocess.run([str(binary), "provision", "uninstall"], capture_output=True, text=True, env=environment, timeout=90)
            if uninstall.returncode != 0 or "mcp_servers.creative-model-bridge" in (home / "config.toml").read_text(encoding="utf-8"):
                raise RuntimeError(uninstall.stderr or "provision uninstall failed")
            _TLSHandler.seen = []
            _tls_smoke(binary, root)
    except RuntimeError as error:
        print(f"creative-model-bridge TLS smoke: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
