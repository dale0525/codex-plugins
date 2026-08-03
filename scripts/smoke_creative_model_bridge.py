#!/usr/bin/env python3
"""Run the checked-in MCP command through offline and local HTTPS paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


_DIAGNOSTIC_KEYS = frozenset(
    {"phase", "outer_type", "reason_type", "errno", "ssl_verify_code", "ssl_reason"}
)
_DIAGNOSTIC_PHASES = frozenset({"models", "responses"})
_DIAGNOSTIC_REASONS = frozenset(
    {
        "CERTIFICATE_VERIFY_FAILED",
        "HOSTNAME_MISMATCH",
        "SELF_SIGNED_CERTIFICATE",
        "UNABLE_TO_GET_ISSUER",
        "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    }
)
_DIAGNOSTIC_TYPES = frozenset(
    {
        "HTTPError",
        "JSONDecodeError",
        "OSError",
        "SSLError",
        "SSLCertVerificationError",
        "TimeoutError",
        "URLError",
        "unknown",
    }
)


class _SmokeFailure(RuntimeError):
    """A fixed-shape, host-safe smoke failure; never stores exception text."""

    def __init__(
        self,
        phase: str,
        category: str,
        *,
        returncode: int | None = None,
        diagnostic: dict[str, object] | None = None,
    ) -> None:
        self.phase = phase
        self.category = category
        self.returncode = returncode if type(returncode) is int else None
        self.diagnostic = _validate_transport_diagnostic(diagnostic)
        self.emitted = False
        super().__init__(phase, category)

    def payload(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "category": self.category,
            "returncode": self.returncode,
            "transport_diagnostic": self.diagnostic,
            "handler_seen": _handler_seen_fingerprint(),
        }


def _validate_transport_diagnostic(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != _DIAGNOSTIC_KEYS:
        return None
    phase = value.get("phase")
    outer_type = value.get("outer_type")
    reason_type = value.get("reason_type")
    errno = value.get("errno")
    ssl_verify_code = value.get("ssl_verify_code")
    ssl_reason = value.get("ssl_reason")
    if type(phase) is not str or phase not in _DIAGNOSTIC_PHASES:
        return None
    if type(outer_type) is not str or outer_type not in _DIAGNOSTIC_TYPES:
        return None
    if reason_type is not None and (type(reason_type) is not str or reason_type not in _DIAGNOSTIC_TYPES):
        return None
    if errno is not None and type(errno) is not int:
        return None
    if ssl_verify_code is not None and (type(ssl_verify_code) is not int or ssl_verify_code < 0):
        return None
    if ssl_reason is not None and (type(ssl_reason) is not str or ssl_reason not in _DIAGNOSTIC_REASONS):
        return None
    return {
        "phase": phase,
        "outer_type": outer_type,
        "reason_type": reason_type,
        "errno": errno,
        "ssl_verify_code": ssl_verify_code,
        "ssl_reason": ssl_reason,
    }


def _handler_seen_fingerprint() -> list[str]:
    allowed = {
        ("GET", "/v1/models"): "GET /v1/models",
        ("POST", "/v1/chat/completions"): "POST /v1/chat/completions",
    }
    return [label for event, label in allowed.items() if event in _TLSHandler.seen]


def _emit_failure(failure: _SmokeFailure) -> None:
    if failure.emitted:
        return
    print(
        "creative-model-bridge smoke failure: "
        + json.dumps(failure.payload(), ensure_ascii=True, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )
    failure.emitted = True


def _run_rpc(
    binary: Path,
    payload: str,
    environment: dict[str, str],
    *,
    phase: str = "rpc",
) -> list[dict[str, object]]:
    try:
        result = subprocess.run(
            [str(binary)],
            cwd=binary.parent,
            input=payload.encode("utf-8"),
            capture_output=True,
            env=environment,
            check=False,
            timeout=90,
        )
    except OSError:
        raise _SmokeFailure(phase, "launch")
    except subprocess.TimeoutExpired:
        raise _SmokeFailure(phase, "timeout")
    except UnicodeDecodeError:
        raise _SmokeFailure(phase, "decode")
    if type(result.returncode) is not int or result.returncode != 0:
        raise _SmokeFailure(phase, "nonzero", returncode=result.returncode)
    try:
        stdout = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _SmokeFailure(phase, "decode")
    responses: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            raise _SmokeFailure(phase, "parse")
        if not isinstance(parsed, dict):
            raise _SmokeFailure(phase, "parse")
        responses.append(parsed)
    return responses


def _run_command(
    command: list[str],
    environment: dict[str, str],
    *,
    phase: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            env=environment,
            timeout=90,
            check=False,
        )
    except OSError:
        raise _SmokeFailure(phase, "launch")
    except subprocess.TimeoutExpired:
        raise _SmokeFailure(phase, "timeout")
    except UnicodeDecodeError:
        raise _SmokeFailure(phase, "decode")
    if type(result.returncode) is not int or result.returncode != 0:
        raise _SmokeFailure(phase, "nonzero", returncode=result.returncode)
    return result


def _tls_environment(
    base: dict[str, str],
    home: Path,
    ca_cert: Path,
) -> dict[str, str]:
    environment = base.copy()
    environment["NO_PROXY"] = "*"
    environment["no_proxy"] = "*"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.update(
        {
            "CODEX_HOME": str(home),
            "SSL_CERT_FILE": str(ca_cert),
            "SMOKE_BEARER": "placeholder-smoke-token",
            "CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS": "1",
        }
    )
    return environment


def _trusted_error_diagnostic(responses: list[dict[str, object]]) -> dict[str, object] | None:
    for item in responses:
        result = item.get("result")
        if not isinstance(result, dict) or result.get("isError") is not True:
            continue
        diagnostic = _validate_transport_diagnostic(result.get("transport_diagnostic"))
        if diagnostic is None:
            raise _SmokeFailure("trusted-response", "assertion")
        return diagnostic
    return None


def _openssl_executable(*, os_name: str | None = None) -> str | None:
    plugin_root = Path(__file__).resolve().parents[1] / "plugins/creative-model-bridge/.pixi/envs/default"
    locked = plugin_root / ("Library/bin/openssl.exe" if (os_name or os.name) == "nt" else "bin/openssl")
    if locked.is_file():
        return str(locked)
    return shutil.which("openssl")


def _run_fixture_openssl(
    openssl: str,
    arguments: list[str],
    *,
    phase: str,
    timeout: float = 30,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            [openssl, *arguments],
            input=input_text,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise _SmokeFailure(phase, "openssl")
    if type(result.returncode) is not int or result.returncode != 0:
        raise _SmokeFailure(phase, "verify", returncode=result.returncode)
    return result


def _make_tls_material(root: Path, openssl: str | None = None) -> tuple[Path, Path, Path, Path]:
    openssl = openssl or _openssl_executable()
    if not openssl:
        raise _SmokeFailure("tls-setup", "openssl")
    ca_key, ca_cert = root / "ca.key", root / "ca.pem"
    untrusted_key, untrusted_cert = root / "untrusted-ca.key", root / "untrusted-ca.pem"
    server_key, server_csr, server_cert = root / "server.key", root / "server.csr", root / "server.pem"
    ca_config, leaf_config = root / "ca.cnf", root / "leaf.cnf"
    try:
        ca_config.write_text(
            "[ req ]\n"
            "distinguished_name = ca_dn\n"
            "x509_extensions = ca_ext\n"
            "prompt = no\n\n"
            "[ ca_dn ]\n"
            "CN = Creative Smoke CA\n\n"
            "[ ca_ext ]\n"
            "basicConstraints = critical,CA:true,pathlen:0\n"
            "keyUsage = critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier = hash\n"
            "authorityKeyIdentifier = keyid:always,issuer\n",
            encoding="utf-8",
        )
        leaf_config.write_text(
            "[ req ]\n"
            "distinguished_name = leaf_dn\n"
            "prompt = no\n\n"
            "[ leaf_dn ]\n"
            "CN = localhost\n\n"
            "[ leaf_ext ]\n"
            "basicConstraints = critical,CA:false\n"
            "keyUsage = critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage = serverAuth\n"
            "subjectAltName = DNS:localhost,IP:127.0.0.1\n"
            "subjectKeyIdentifier = hash\n"
            "authorityKeyIdentifier = keyid,issuer\n",
            encoding="utf-8",
        )
    except OSError:
        raise _SmokeFailure("tls-setup", "filesystem")
    try:
        commands = [
            ["genrsa", "-out", str(ca_key), "2048"],
            ["req", "-x509", "-new", "-nodes", "-key", str(ca_key), "-sha256", "-days", "1", "-config", str(ca_config), "-extensions", "ca_ext", "-set_serial", "1001", "-out", str(ca_cert)],
            ["genrsa", "-out", str(untrusted_key), "2048"],
            ["req", "-x509", "-new", "-nodes", "-key", str(untrusted_key), "-sha256", "-days", "1", "-subj", "/CN=Untrusted Smoke CA", "-config", str(ca_config), "-extensions", "ca_ext", "-set_serial", "2001", "-out", str(untrusted_cert)],
            ["genrsa", "-out", str(server_key), "2048"],
            ["req", "-new", "-key", str(server_key), "-config", str(leaf_config), "-out", str(server_csr)],
            ["x509", "-req", "-in", str(server_csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-set_serial", "1002", "-out", str(server_cert), "-days", "1", "-sha256", "-extfile", str(leaf_config), "-extensions", "leaf_ext"],
        ]
        for arguments in commands:
            _run_fixture_openssl(openssl, arguments, phase="tls-setup")
    except _SmokeFailure:
        raise _SmokeFailure("tls-setup", "certificate")
    return ca_cert, server_cert, server_key, untrusted_cert


def _verify_tls_fixture(openssl: str, ca_cert: Path, server_cert: Path) -> None:
    _run_fixture_openssl(
        openssl,
        ["verify", "-purpose", "sslserver", "-verify_ip", "127.0.0.1", "-CAfile", str(ca_cert), str(server_cert)],
        phase="tls-fixture-file-verify",
    )


def _verify_tls_live(openssl: str, ca_cert: Path, port: int) -> None:
    _run_fixture_openssl(
        openssl,
        [
            "s_client",
            "-connect",
            f"127.0.0.1:{port}",
            "-CAfile",
            str(ca_cert),
            "-verify_return_error",
            "-verify_ip",
            "127.0.0.1",
            "-servername",
            "localhost",
        ],
        phase="tls-fixture-live-verify",
        timeout=10,
        input_text="",
    )


class _TLSHandler(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _write_json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_sse(self, payload: dict[str, object], usage: dict[str, object]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        first = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        tail = json.dumps({"id": payload.get("id"), "choices": [], "usage": usage}, ensure_ascii=False).encode("utf-8")
        self.wfile.write(b"data: " + first + b"\n\n")
        self.wfile.write(b"data: " + tail + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")

    def _auth(self) -> bool:
        return self.headers.get("Authorization") == "Bearer placeholder-smoke-token"

    def do_GET(self) -> None:  # noqa: N802
        _TLSHandler.seen.append((self.command, self.path))
        if self.path != "/v1/models" or not self._auth():
            self.send_error(401)
            return
        self._write_json({"object": "list", "data": [{"id": "smoke/model"}]})

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(size)
        _TLSHandler.seen.append((self.command, self.path))
        if self.path != "/v1/chat/completions" or not self._auth():
            self.send_error(401)
            return
        self._write_sse(
            {"id": "tls-response", "choices": [{"delta": {"content": "TLS 烟雾成功"}, "finish_reason": "stop"}]},
            {"input_tokens": 4, "output_tokens": 3},
        )


class _LocalTLSHTTPServer(ThreadingHTTPServer):
    """Bind loopback without macOS ``getfqdn`` reverse-lookup surprises."""

    def server_bind(self) -> None:
        original_getfqdn = socket.getfqdn
        try:
            # Preserve socket.bind/listen and TLS wrapping; bypass only the
            # hostname lookup performed by HTTPServer.server_bind on macOS.
            socket.getfqdn = lambda host: host
            super().server_bind()
        finally:
            socket.getfqdn = original_getfqdn


def _phase(name: str) -> None:
    print(f"creative-model-bridge smoke phase: {name}", file=sys.stderr, flush=True)


def _tls_smoke(binary: Path, root: Path) -> None:
    _TLSHandler.seen = []
    server: _LocalTLSHTTPServer | None = None
    thread: threading.Thread | None = None
    try:
        openssl = _openssl_executable()
        if not openssl:
            raise _SmokeFailure("tls-setup", "openssl")
        ca_cert, server_cert, server_key, untrusted_cert = _make_tls_material(root, openssl)
        _verify_tls_fixture(openssl, ca_cert, server_cert)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=server_cert, keyfile=server_key)
        try:
            server = _LocalTLSHTTPServer(("127.0.0.1", 0), _TLSHandler)
        except OSError:
            raise _SmokeFailure("tls-setup", "launch")
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _verify_tls_live(openssl, ca_cert, server.server_port)
        home = root / "tls-home"
        home.mkdir()
        (home / "config.toml").write_text(
            "[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = \"smoke\"\nCREATIVE_MODEL_DEFAULT = \"smoke/model\"\n\n"
            "[model_providers.smoke]\n"
            f"base_url = \"https://127.0.0.1:{server.server_port}/v1\"\nwire_api = \"responses\"\nenv_key = \"SMOKE_BEARER\"\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        # Exercise and then remove hostile proxy settings: loopback TLS must
        # connect directly even when a caller's environment supplies proxies.
        environment.update({
            "HTTP_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "all_proxy": "http://127.0.0.1:9",
        })
        environment = {
            key: value
            for key, value in environment.items()
            if not key.lower().endswith("_proxy")
        }
        environment = _tls_environment(environment, home, ca_cert)
        requests = "\n".join([
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"creative_models","arguments":{}}}',
            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"creative_generate","arguments":{"task":"TLS smoke"}}}',
            "",
        ])
        responses = _run_rpc(binary, requests, environment, phase="trusted-rpc")
        if [item.get("id") for item in responses] != [1, 2]:
            raise _SmokeFailure("trusted-assertion", "assertion")
        diagnostic = _trusted_error_diagnostic(responses)
        if diagnostic is not None:
            raise _SmokeFailure("trusted-response", "is-error", diagnostic=diagnostic)
        models_result = responses[0].get("result")
        generated_result = responses[1].get("result")
        models = models_result.get("structuredContent", {}) if isinstance(models_result, dict) else {}
        generated = generated_result.get("structuredContent", {}) if isinstance(generated_result, dict) else {}
        if (
            not isinstance(models, dict)
            or not isinstance(generated, dict)
            or models.get("models") != ["smoke/model"]
            or generated.get("text") != "TLS 烟雾成功"
            or not generated.get("usage")
        ):
            raise _SmokeFailure("trusted-assertion", "assertion")
        if any(
            isinstance(item.get("result"), dict) and "transport_diagnostic" in item["result"]
            for item in responses
        ):
            raise _SmokeFailure("trusted-response", "assertion")
        if _TLSHandler.seen != [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]:
            raise _SmokeFailure("trusted-assertion", "assertion")
        _TLSHandler.seen = []
        untrusted_environment = environment.copy()
        # The leaf certificate is intentionally not a CA trust anchor.  The
        # request must fail without reaching the HTTP handler.
        untrusted_environment["SSL_CERT_FILE"] = str(untrusted_cert)
        # WP2's server integration may surface the typed diagnostic on this
        # test-only switch.  Older candidates simply keep the sanitized text.
        untrusted_environment["CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS"] = "1"
        negative = _run_rpc(
            binary,
            requests.replace('"id":1', '"id":3').replace('"id":2', '"id":4'),
            untrusted_environment,
            phase="negative-rpc",
        )
        if [item.get("id") for item in negative] != [3, 4]:
            raise _SmokeFailure("negative-response", "assertion")
        for item in negative:
            result = item.get("result")
            if not isinstance(result, dict) or result.get("isError") is not True:
                raise _SmokeFailure("negative-response", "assertion")
            diagnostic = _validate_transport_diagnostic(result.get("transport_diagnostic"))
            if diagnostic is None:
                raise _SmokeFailure("negative-response", "assertion")
            expected_phase = "models" if item.get("id") == 3 else "responses"
            if (
                diagnostic["phase"] != expected_phase
                or diagnostic["reason_type"] != "SSLCertVerificationError"
                or diagnostic["outer_type"] not in {"URLError", "SSLError"}
                or type(diagnostic["ssl_verify_code"]) is not int
                or diagnostic["ssl_verify_code"] < 0
                or diagnostic["ssl_reason"] not in _DIAGNOSTIC_REASONS
            ):
                raise _SmokeFailure("negative-response", "assertion")
            print(f"creative-model-bridge transport diagnostic: {json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)}", file=sys.stderr, flush=True)
        if _TLSHandler.seen != []:
            raise _SmokeFailure("negative-assertion", "assertion")
    except _SmokeFailure as failure:
        _emit_failure(failure)
        raise
    except ssl.SSLError:
        failure = _SmokeFailure("trusted-setup", "certificate")
        _emit_failure(failure)
        raise failure
    except OSError:
        failure = _SmokeFailure("trusted-setup", "filesystem")
        _emit_failure(failure)
        raise failure
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2)


def main() -> int:
    binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if binary is None:
        raise SystemExit("usage: smoke_creative_model_bridge.py BINARY")
    try:
        with tempfile.TemporaryDirectory(prefix="creative-smoke-") as temporary:
            root = Path(temporary)
            _phase("offline")
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
            environment["PYTHONIOENCODING"] = "utf-8"
            payload = "\n".join([
                '{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
                '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"creative_preview","arguments":{"task":"offline smoke","model":"smoke-model"}}}',
                "",
            ])
            responses = _run_rpc(binary, payload, environment, phase="offline-rpc")
            if [item.get("id") for item in responses] != [1, 2, 3] or "creative_preview" not in json.dumps(responses, ensure_ascii=False):
                raise _SmokeFailure("offline-assertion", "assertion")
            preview_result = responses[2].get("result")
            preview = preview_result.get("structuredContent", {}) if isinstance(preview_result, dict) else {}
            if not isinstance(preview, dict) or preview.get("network") is not False:
                raise _SmokeFailure("offline-assertion", "assertion")
            _phase("provision")
            _run_command([str(binary), "provision", "setup", "--yes"], environment, phase="provision-setup")
            status = _run_command([str(binary), "provision", "status"], environment, phase="provision-status")
            if status.returncode != 0 or '"status": "installed"' not in status.stdout:
                raise _SmokeFailure("provision-status", "assertion", returncode=status.returncode)
            uninstall = _run_command([str(binary), "provision", "uninstall"], environment, phase="provision-uninstall")
            if uninstall.returncode != 0 or "mcp_servers.creative-model-bridge" in (home / "config.toml").read_text(encoding="utf-8"):
                raise _SmokeFailure("provision-uninstall", "assertion", returncode=uninstall.returncode)
            _TLSHandler.seen = []
            _phase("trusted-tls")
            _tls_smoke(binary, root)
    except _SmokeFailure as failure:
        _emit_failure(failure)
        return 1
    except OSError:
        _emit_failure(_SmokeFailure("offline-setup", "filesystem"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
