#!/usr/bin/env python3
"""Newline-delimited JSON-RPC stdio server for Creative Model Bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# A copied plugin cache has this file as its working-tree root.  Importing by
# the sibling directory keeps the launcher independent of the source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # direct script launch from a copied plugin cache
    from bridge import BRIDGE_VERSION, Bridge, BridgeError, REQUEST_SCHEMA  # type: ignore  # noqa: E402
    from provision import ProvisionError, main as provision_main, resolve_ssl_cert_file  # type: ignore  # noqa: E402
except ImportError:  # package import in tests or embedding applications
    from .bridge import BRIDGE_VERSION, Bridge, BridgeError, REQUEST_SCHEMA  # noqa: E402
    from .provision import ProvisionError, main as provision_main, resolve_ssl_cert_file  # noqa: E402


def _prompt_report_schema() -> dict[str, Any]:
    """Return a fresh, strict schema for the bridge's prompt accounting report."""

    return {
        "type": "object",
        "properties": {
            "system_prompt": {"type": ["string", "null"]},
            "system_mode": {"type": "string", "enum": ["minimal", "none"]},
            "section_order": {"type": "array", "items": {"type": "string"}},
            "user_chars": {"type": "integer", "minimum": 0},
            "total_chars": {"type": "integer", "minimum": 0},
            "truncated": {"const": False},
            "context_text": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "chars": {"type": "integer", "minimum": 0},
                    },
                    "required": ["label", "chars"],
                    "additionalProperties": False,
                },
            },
            "context_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "chars": {"type": "integer", "minimum": 0},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["path", "chars", "sha256", "encoding"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "system_prompt",
            "system_mode",
            "section_order",
            "user_chars",
            "total_chars",
            "truncated",
            "context_text",
            "context_files",
        ],
        "additionalProperties": False,
    }


def _payload_schema() -> dict[str, Any]:
    """Return a fresh, strict schema for the exact outbound Responses payload."""

    return {
        "type": "object",
        "properties": {
            "model": {"type": "string"},
            "input": {"type": "string"},
            "instructions": {"type": "string"},
            "max_output_tokens": {"type": "integer", "minimum": 1},
            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        },
        "required": ["model", "input", "max_output_tokens"],
        "additionalProperties": False,
    }


def _models_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"const": ""},
            "provider": {"type": "string"},
            "model": {"type": "null"},
            "usage": {"type": ["object", "null"]},
            "request_id": {"type": ["string", "null"]},
            "prompt_report": {"type": "null"},
            "models": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "text",
            "provider",
            "model",
            "usage",
            "request_id",
            "prompt_report",
            "models",
        ],
        "additionalProperties": False,
    }


def _preview_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"const": ""},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "usage": {"type": "null"},
            "request_id": {"type": "null"},
            "prompt_report": _prompt_report_schema(),
            "prompt": {"type": "string"},
            "payload": _payload_schema(),
            "network": {"const": False},
        },
        "required": [
            "text",
            "provider",
            "model",
            "usage",
            "request_id",
            "prompt_report",
            "prompt",
            "payload",
            "network",
        ],
        "additionalProperties": False,
    }


def _generate_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "usage": {"type": ["object", "null"]},
            "request_id": {"type": ["string", "null"]},
            "prompt_report": _prompt_report_schema(),
        },
        "required": ["text", "provider", "model", "usage", "request_id", "prompt_report"],
        "additionalProperties": False,
    }


STANDARD_OUTPUT_SCHEMA = _generate_output_schema()


_TRANSPORT_DIAGNOSTICS_ENV = "CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS"


def _is_utf8_encoding(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.lower().replace("-", "").replace("_", "")
    return normalized in {"utf8", "utf8sig"}


def _configure_stdio_utf8() -> None:
    """Configure the process stdio wrappers for UTF-8 before MCP input/output.

    Frozen Windows executables inherit the console code page (often cp1252),
    so relying on ``PYTHONIOENCODING`` is insufficient.  ``reconfigure`` is
    It deliberately leaves newline translation unchanged and passes through the
    wrapper's existing error policy.  A wrapper that cannot be reconfigured is
    accepted only when it already advertises UTF-8; otherwise fail closed
    before consuming any JSON-RPC input.
    """

    failures: list[str] = []
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                # Changing encoding otherwise resets TextIOWrapper.errors to
                # ``strict``; pass the existing value explicitly.  Omitting
                # newline keeps the wrapper's newline translation unchanged.
                errors = getattr(stream, "errors", None)
                kwargs: dict[str, str] = {"encoding": "utf-8"}
                if isinstance(errors, str):
                    kwargs["errors"] = errors
                reconfigure(**kwargs)
                if not _is_utf8_encoding(getattr(stream, "encoding", None)):
                    failures.append(f"{name}: reconfigure did not produce UTF-8")
            except (AttributeError, OSError, TypeError, ValueError):
                failures.append(f"{name}: reconfigure failed")
            continue
        encoding = getattr(stream, "encoding", None)
        if _is_utf8_encoding(encoding):
            continue
        failures.append(f"{name}: UTF-8 reconfigure unsupported")
    if failures:
        raise RuntimeError("UTF-8 stdio configuration unavailable (" + "; ".join(failures) + ")")


def _configure_ssl_cert_file() -> None:
    """Initialize urllib's CA bundle before a standard stdio server request.

    ``provision.resolve_ssl_cert_file`` is the single source of truth for
    explicit overrides and platform defaults.  On POSIX it selects the
    deterministic system bundle (including macOS's ``/etc/ssl/cert.pem``),
    while Windows returns ``None`` so Python keeps its native trust store.
    The resolver's result is always written back because the plugin-specific
    alias intentionally takes precedence when both CA variables are present.
    """

    resolved = resolve_ssl_cert_file()
    if resolved is not None:
        os.environ["SSL_CERT_FILE"] = resolved


TOOL_DEFINITIONS = [
    {
        "name": "creative_models",
        "description": "List models exposed by the configured Responses provider.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "outputSchema": _models_output_schema(),
    },
    {
        "name": "creative_preview",
        "description": "Build and inspect the exact outbound creative prompt without network access.",
        "inputSchema": REQUEST_SCHEMA,
        "outputSchema": _preview_output_schema(),
    },
    {
        "name": "creative_generate",
        "description": "Generate creative text through the configured Responses provider.",
        "inputSchema": REQUEST_SCHEMA,
        "outputSchema": _generate_output_schema(),
    },
]


def _result(request_id: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any], bridge: Bridge) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "invalid request")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "creative-model-bridge", "version": BRIDGE_VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOL_DEFINITIONS})
    if method != "tools/call":
        return _error(request_id, -32601, "method not found")

    params = message.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return _error(request_id, -32602, "tools/call requires a tool name")
    arguments = params.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        return _error(request_id, -32602, "tool arguments must be an object")
    try:
        value = bridge.call(params["name"], arguments or {})
    except BridgeError as error:
        message_text = str(error)
        result: dict[str, Any] = {
            "isError": True,
            "content": [{"type": "text", "text": message_text}],
        }
        diagnostic = getattr(error, "transport_diagnostic", None)
        if getattr(bridge, "transport_diagnostics", False) and diagnostic is not None:
            result["transport_diagnostic"] = diagnostic.as_dict()
        return _result(
            request_id,
            result,
        )
    except Exception:
        # Never send implementation details or configuration values over MCP.
        return _result(
            request_id,
            {"isError": True, "content": [{"type": "text", "text": "creative model bridge failed"}]},
        )
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _result(
        request_id,
        {"isError": False, "structuredContent": value, "content": [{"type": "text", "text": rendered}]},
    )


def main() -> int:
    try:
        _configure_stdio_utf8()
    except RuntimeError as error:
        # Keep the diagnostic ASCII-only so it remains printable even when a
        # hostile wrapper rejected UTF-8 configuration.
        try:
            sys.stderr.write(f"creative-model-bridge: {error}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 1
    if len(sys.argv) > 1 and sys.argv[1] == "provision":
        return provision_main(sys.argv[2:])
    try:
        _configure_ssl_cert_file()
    except ProvisionError as error:
        try:
            sys.stderr.write(f"creative-model-bridge: {error}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 1
    if os.environ.get(_TRANSPORT_DIAGNOSTICS_ENV) == "1":
        bridge = Bridge(transport_diagnostics=True)
    else:
        bridge = Bridge()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        else:
            if not isinstance(message, dict):
                response = _error(None, -32600, "invalid request")
            else:
                response = handle(message, bridge)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
