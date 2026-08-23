#!/usr/bin/env python3
"""One-shot, provider-neutral OpenAI-compatible Chat Completions caller."""

from __future__ import annotations

import json
import http.client
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import (  # nosec B310 - URL is validated before opening
    Request,
    build_opener,
    HTTPRedirectHandler,
    ProxyHandler,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
CONFIG_TIMEOUT_SECONDS = 30.0
CONFIG_STARTUP_GRACE_SECONDS = 0.5
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
FORBIDDEN_REQUEST_HEADERS = {
    "connection",
    "content-length",
    "host",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class BridgeError(Exception):
    def __init__(
        self,
        stage: str,
        code: str,
        retryable: bool = False,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


def failure_result(error: BridgeError) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "stage": error.stage,
        "code": error.code,
        "retryable": error.retryable,
    }
    if error.http_status is not None:
        result["http_status"] = error.http_status
    return result


def _json_lines(text: str) -> Sequence[Mapping[str, Any]]:
    messages = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            messages.append(value)
    return messages


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _validated_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError("request", "timeout_invalid")
    try:
        timeout = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise BridgeError("request", "timeout_invalid") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 900:
        raise BridgeError("request", "timeout_invalid")
    return timeout


def _encode_json_body(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return text.encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise BridgeError("request", "request_body_invalid") from exc


def read_effective_config(cwd: str, codex_bin: str = "codex") -> Mapping[str, Any]:
    """Resolve the effective config without reading config.toml directly."""
    requests = [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "provider-chat-completions",
                    "version": "0.1.0",
                }
            },
        },
        {"method": "initialized", "params": {}},
        {
            "id": 2,
            "method": "config/read",
            "params": {"cwd": cwd, "includeLayers": False},
        },
    ]
    payload = "\n".join(json.dumps(item, ensure_ascii=True) for item in requests) + "\n"
    try:
        process = subprocess.Popen(
            [codex_bin, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.flush()
        # app-server needs a short turn to publish the response before EOF.
        time.sleep(CONFIG_STARTUP_GRACE_SECONDS)
        process.stdin.close()
        # Python 3.9's communicate() flushes a closed stdin handle unless it is
        # detached first. Keep communicate() for concurrent stdout draining:
        # config/read can exceed a pipe buffer and deadlock a plain wait().
        process.stdin = None
        stdout_bytes, _ = process.communicate(timeout=CONFIG_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise BridgeError("config", "codex_unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise BridgeError("config", "config_read_timeout", retryable=True) from exc
    except (OSError, ValueError) as exc:
        raise BridgeError("config", "config_read_failed") from exc

    if process.returncode != 0:
        raise BridgeError("config", "config_read_failed")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    response = next((item for item in _json_lines(stdout) if item.get("id") == 2), None)
    if response is None:
        raise BridgeError("config", "config_read_no_response")
    if response.get("error") is not None:
        raise BridgeError("config", "config_read_error")
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
        raise BridgeError("config", "config_read_invalid_result")
    return result["config"]


def resolve_provider(config: Mapping[str, Any]) -> Mapping[str, Any]:
    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider_name, str) or not provider_name:
        raise BridgeError("config", "provider_not_selected")
    if not isinstance(providers, dict) or not isinstance(providers.get(provider_name), dict):
        raise BridgeError("config", "provider_definition_missing")
    return providers[provider_name]


def _header_map(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BridgeError("credential", "headers_invalid")
    result: Dict[str, str] = {}
    for name, header_value in value.items():
        if (
            not isinstance(name, str)
            or not HEADER_NAME_PATTERN.fullmatch(name)
            or not isinstance(header_value, str)
        ):
            raise BridgeError("credential", "headers_invalid")
        lowered = name.lower()
        if (
            lowered in FORBIDDEN_REQUEST_HEADERS
            or lowered.startswith("proxy-")
            or name.lower() in {key.lower() for key in result}
            or "\r" in name
            or "\n" in name
            or "\r" in header_value
            or "\n" in header_value
        ):
            raise BridgeError("credential", "headers_invalid")
        result[name] = header_value
    return result


def _header_exists(headers: Mapping[str, str], name: str) -> bool:
    wanted = name.lower()
    return any(key.lower() == wanted for key in headers)


def resolve_headers(provider: Mapping[str, Any]) -> Dict[str, str]:
    headers = _header_map(provider.get("http_headers"))
    environment_headers = provider.get("env_http_headers")
    if environment_headers is not None:
        if not isinstance(environment_headers, dict):
            raise BridgeError("credential", "env_headers_invalid")
        for header_name, env_name in environment_headers.items():
            if (
                not isinstance(header_name, str)
                or not HEADER_NAME_PATTERN.fullmatch(header_name)
                or not isinstance(env_name, str)
                or not env_name
            ):
                raise BridgeError("credential", "env_headers_invalid")
            value = os.environ.get(env_name)
            if value is None:
                raise BridgeError("credential", "env_header_unavailable")
            lowered = header_name.lower()
            if (
                lowered in FORBIDDEN_REQUEST_HEADERS
                or lowered.startswith("proxy-")
                or any(header.lower() == lowered for header in headers)
                or "\r" in value
                or "\n" in value
            ):
                raise BridgeError("credential", "env_header_invalid")
            headers[header_name] = value

    if not _header_exists(headers, "Authorization"):
        env_key = provider.get("env_key")
        token: Optional[str] = None
        if env_key is not None:
            if not isinstance(env_key, str) or not env_key:
                raise BridgeError("credential", "env_key_invalid")
            token = os.environ.get(env_key)
            if not token:
                raise BridgeError("credential", "env_key_unavailable")
        else:
            configured_token = provider.get("experimental_bearer_token")
            if configured_token is not None:
                if not isinstance(configured_token, str) or not configured_token:
                    raise BridgeError("credential", "bearer_token_invalid")
                token = configured_token
        if token is not None:
            if "\r" in token or "\n" in token:
                raise BridgeError("credential", "bearer_token_invalid")
            headers["Authorization"] = "Bearer " + token

    auth = provider.get("auth")
    if auth is not None:
        # The app-server contract does not expose a safe, portable command-backed
        # auth protocol. Never execute an arbitrary configured command here.
        raise BridgeError("credential", "auth_source_unsupported")
    if provider.get("requires_openai_auth") and not _header_exists(headers, "Authorization"):
        raise BridgeError("credential", "credential_unavailable")
    return headers


def build_endpoint(provider: Mapping[str, Any]) -> str:
    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise BridgeError("config", "base_url_missing")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise BridgeError("config", "base_url_invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or any(character.isspace() for character in base_url)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in base_url)
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise BridgeError("config", "base_url_invalid")
    if parsed.username is not None or parsed.password is not None:
        raise BridgeError("config", "base_url_contains_credentials")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"

    query_params = provider.get("query_params")
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    if query_params is not None:
        if not isinstance(query_params, dict):
            raise BridgeError("config", "query_params_invalid")
        for key, value in query_params.items():
            if isinstance(value, list):
                query.extend((str(key), str(item)) for item in value)
            elif isinstance(value, (str, int, float, bool)):
                query.append((str(key), str(value).lower() if isinstance(value, bool) else str(value)))
            else:
                raise BridgeError("config", "query_params_invalid")
    try:
        return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))
    except (UnicodeError, ValueError) as exc:
        raise BridgeError("config", "base_url_invalid") from exc


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise HTTPError(request.full_url, code, "redirect_not_allowed", headers, fp)


def build_request(request: Mapping[str, Any]) -> Dict[str, Any]:
    model = request.get("model")
    messages = request.get("messages")
    if not isinstance(model, str) or not model.strip():
        raise BridgeError("request", "model_required")
    if not isinstance(messages, list) or not messages:
        raise BridgeError("request", "messages_required")
    parameters = request.get("parameters", {})
    if not isinstance(parameters, dict):
        raise BridgeError("request", "parameters_invalid")
    protected = {"model", "messages"}
    if any(key in parameters for key in protected):
        raise BridgeError("request", "protected_parameter")
    if parameters.get("stream") not in (None, False):
        raise BridgeError("request", "streaming_not_supported")
    body = dict(parameters)
    body["model"] = model
    body["messages"] = messages
    body["stream"] = False
    return body


def _retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or status >= 500


def post_chat_completion(
    provider: Mapping[str, Any],
    request: Mapping[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    timeout = _validated_timeout(timeout_seconds)
    body = build_request(request)
    encoded = _encode_json_body(body)
    endpoint = build_endpoint(provider)
    headers = resolve_headers(provider)
    if not _header_exists(headers, "Content-Type"):
        headers["Content-Type"] = "application/json"
    if not _header_exists(headers, "Accept"):
        headers["Accept"] = "application/json"
    # Do not let credential-bearing requests inherit HTTP(S)_PROXY settings;
    # that would send the provider credential to an unconfigured intermediary.
    try:
        http_request = Request(endpoint, data=encoded, headers=headers, method="POST")
        client = build_opener(NoRedirectHandler(), ProxyHandler({}))
        with client.open(http_request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = int(response.status)
    except http.client.InvalidURL as exc:
        raise BridgeError("config", "base_url_invalid") from exc
    except HTTPError as exc:
        code = "redirect_not_allowed" if 300 <= exc.code < 400 else "http_error"
        status = exc.code
        exc.close()
        raise BridgeError("http", code, _retryable_status(status), status) from exc
    except ssl.SSLError as exc:
        raise BridgeError("transport", "tls_failed") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise BridgeError("transport", "timeout", retryable=True) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise BridgeError("transport", "timeout", retryable=True) from exc
        if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
            raise BridgeError("transport", "tls_failed") from exc
        raise BridgeError("transport", "connection_failed", retryable=True) from exc
    except http.client.HTTPException as exc:
        raise BridgeError("transport", "connection_failed", retryable=True) from exc
    except ValueError as exc:
        raise BridgeError("transport", "connection_failed") from exc
    except OSError as exc:
        raise BridgeError("transport", "connection_failed", retryable=True) from exc
    if status < 200 or status >= 300:
        raise BridgeError("http", "http_error", _retryable_status(status), status)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BridgeError("protocol", "response_too_large")
    try:
        response_json = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BridgeError("protocol", "invalid_json") from exc
    return normalize_response(response_json)


def normalize_response(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        raise BridgeError("protocol", "response_not_object")
    if response.get("error") is not None:
        raise BridgeError("protocol", "provider_error")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise BridgeError("protocol", "choices_missing")
    choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise BridgeError("protocol", "message_missing")
    content = message.get("content")
    if content is None and not message.get("tool_calls"):
        raise BridgeError("protocol", "content_missing")
    if content is not None and not isinstance(content, (str, list)):
        raise BridgeError("protocol", "content_invalid")
    result: Dict[str, Any] = {
        "ok": True,
        "model": response.get("model") or "",
        "content": content,
        "finish_reason": choice.get("finish_reason"),
    }
    if isinstance(response.get("usage"), dict):
        result["usage"] = response["usage"]
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    return result


def process_request(
    request: Mapping[str, Any],
    config_resolver: Optional[Callable[[str], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    if not isinstance(request, Mapping):
        raise BridgeError("request", "request_not_object")
    if "cwd" in request:
        raise BridgeError("request", "cwd_override_not_allowed")
    timeout = _validated_timeout(request.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    # Validate the complete request body before starting app-server or touching
    # provider configuration. This keeps malformed input local and cheap.
    _encode_json_body(build_request(request))
    cwd = os.getcwd()
    config = (config_resolver or (lambda path: read_effective_config(path, os.environ.get("PROVIDER_CHAT_CODEX_BIN", "codex"))))(cwd)
    provider = resolve_provider(config)
    return post_chat_completion(provider, request, timeout)


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(request, dict):
            raise BridgeError("request", "request_not_object")
        result = process_request(request)
    except BridgeError as exc:
        result = failure_result(exc)
    except (json.JSONDecodeError, UnicodeDecodeError):
        result = failure_result(BridgeError("request", "invalid_json"))
    except Exception:
        result = failure_result(BridgeError("runtime", "unexpected_error"))
    try:
        output = json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ) + "\n"
    except (TypeError, ValueError, UnicodeEncodeError):
        output = json.dumps(
            failure_result(BridgeError("protocol", "result_not_serializable")),
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"
    sys.stdout.buffer.write(output.encode("utf-8"))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
