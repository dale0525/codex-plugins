#!/usr/bin/env python3
"""One-shot, provider-neutral OpenAI-compatible Chat Completions caller."""

from __future__ import annotations

import json
import http.client
import math
import ntpath
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
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
CONFIG_REAP_TIMEOUT_SECONDS = 5.0
MAX_DIAGNOSTIC_PATH_CHARS = 2048
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
        diagnostic: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.diagnostic = dict(diagnostic) if diagnostic else None


def failure_result(error: BridgeError) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "stage": error.stage,
        "code": error.code,
        "retryable": error.retryable,
    }
    if error.http_status is not None:
        result["http_status"] = error.http_status
    if error.diagnostic:
        result["diagnostic"] = error.diagnostic
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


def _result_bytes(result: Mapping[str, Any]) -> bytes:
    try:
        output = (
            json.dumps(
                result,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        return output.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        fallback = json.dumps(
            failure_result(BridgeError("protocol", "result_not_serializable")),
            ensure_ascii=True,
            separators=(",", ":"),
        ) + "\n"
        return fallback.encode("utf-8")


def _validated_output_file(value: Any) -> str:
    basename = os.path.basename(value) if isinstance(value, str) else ""
    if (
        not isinstance(value, str)
        or not value
        or not os.path.isabs(value)
        or "\x00" in value
        or basename in {"", ".", ".."}
    ):
        raise BridgeError("request", "output_file_invalid")
    return value


def parse_cli_arguments(arguments: Sequence[str]) -> Optional[str]:
    """Parse optional output capture arguments without changing stdin semantics."""
    if not arguments:
        return None
    if len(arguments) == 2 and arguments[0] == "--output-file":
        return _validated_output_file(arguments[1])
    raise BridgeError("request", "arguments_invalid")


def write_result_file(path: str, result: Mapping[str, Any]) -> int:
    """Atomically persist a complete normalized result with owner-only permissions."""
    path = _validated_output_file(path)
    encoded = _result_bytes(result)
    parent = os.path.dirname(path) or os.curdir
    if not os.path.isdir(parent):
        raise BridgeError("output", "result_file_unavailable")
    descriptor: Optional[int] = None
    temporary_path: Optional[str] = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=parent,
        )
        os.chmod(temporary_path, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        return len(encoded)
    except (OSError, ValueError) as exc:
        raise BridgeError("output", "result_file_write_failed") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def capture_manifest(result: Mapping[str, Any], path: str, byte_count: int) -> Dict[str, Any]:
    """Return bounded metadata for a result that was written to a local file."""
    manifest: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "result_file": path,
        "bytes": byte_count,
    }
    if result.get("ok"):
        manifest["model"] = result.get("model") or ""
        manifest["finish_reason"] = result.get("finish_reason")
        content = result.get("content")
        if isinstance(content, str):
            manifest["content_chars"] = len(content)
        elif content is not None:
            manifest["content_items"] = len(content) if isinstance(content, list) else 0
    return manifest


def resolve_codex_binary(
    environment: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> str:
    """Choose a directly launchable app-server helper for the current platform."""
    env = os.environ if environment is None else environment
    effective_platform = os.name if platform is None else platform
    configured = env.get("PROVIDER_CHAT_CODEX_BIN")
    if isinstance(configured, str) and configured.strip():
        path_module = ntpath if effective_platform == "nt" else os.path
        if not path_module.isabs(configured):
            raise BridgeError("config", "codex_bin_not_absolute")
        return configured

    if effective_platform == "nt":
        # The Windows App Execution Alias named ``codex`` can resolve to a
        # packaged desktop app that a child process is not allowed to launch.
        # Prefer the helper provisioned for plugin app-server calls instead.
        code_home = env.get("CODEX_HOME")
        if not isinstance(code_home, str) or not code_home:
            user_profile = env.get("USERPROFILE")
            if isinstance(user_profile, str) and user_profile:
                code_home = ntpath.join(user_profile, ".codex")
            else:
                code_home = os.path.expanduser("~/.codex")
        helper = ntpath.join(code_home, "plugins", ".plugin-appserver", "codex.exe")
        if not ntpath.isabs(helper):
            raise BridgeError("config", "codex_helper_path_invalid")
        return helper
    return "codex"


_DIAGNOSTIC_REDACTIONS = (
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(\bbearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (
        re.compile(r"(?i)(\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\b(?:sk|rk)-[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
)


def _safe_diagnostic_path(value: str) -> str:
    text = "".join(
        character if character in "\t\r\n" or ord(character) >= 0x20 else "�"
        for character in value
    )
    for pattern, replacement in _DIAGNOSTIC_REDACTIONS:
        text = pattern.sub(replacement, text)
    text = text.strip()
    if len(text) > MAX_DIAGNOSTIC_PATH_CHARS:
        text = text[:MAX_DIAGNOSTIC_PATH_CHARS] + "…"
    return text or "<unknown>"


def _stderr_metadata(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, bytes):
        size = len(value)
    elif isinstance(value, str):
        size = len(value.encode("utf-8", errors="replace"))
    else:
        return None
    return {"present": size > 0, "bytes": size}


def _launch_diagnostic(
    codex_bin: str,
    process: Optional[Any] = None,
    error: Optional[BaseException] = None,
    stderr: Any = None,
) -> Dict[str, Any]:
    diagnostic: Dict[str, Any] = {"executable": _safe_diagnostic_path(str(codex_bin))}
    winerror = getattr(error, "winerror", None)
    if isinstance(winerror, int):
        diagnostic["winerror"] = winerror
    errno = getattr(error, "errno", None)
    if isinstance(errno, int):
        diagnostic["errno"] = errno
    returncode = getattr(process, "returncode", None)
    if isinstance(returncode, int):
        diagnostic["returncode"] = returncode
    stderr_metadata = _stderr_metadata(stderr)
    if stderr_metadata is not None:
        diagnostic["stderr"] = stderr_metadata
    return diagnostic


def _stop_config_process(process: Any) -> Any:
    try:
        if getattr(process, "stdin", None) is not None:
            process.stdin.close()
            process.stdin = None
    except (OSError, ValueError):
        pass
    try:
        process.kill()
    except (AttributeError, OSError, ProcessLookupError):
        pass
    try:
        _, stderr = process.communicate(timeout=CONFIG_REAP_TIMEOUT_SECONDS)
        return stderr
    except subprocess.TimeoutExpired:
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        return None
    except (AttributeError, OSError, ValueError):
        return None


def read_effective_config(cwd: str, codex_bin: Optional[str] = None) -> Mapping[str, Any]:
    """Resolve the effective config without reading config.toml directly."""
    codex_bin = resolve_codex_binary() if codex_bin is None else codex_bin
    requests = [
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "provider-chat-completions",
                    "version": "0.1.3",
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
    process: Optional[Any] = None
    stderr_bytes: Any = None
    try:
        process = subprocess.Popen(
            [codex_bin, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise BridgeError(
            "config",
            "codex_unavailable",
            diagnostic=_launch_diagnostic(codex_bin, process, exc, stderr_bytes),
        ) from exc
    except PermissionError as exc:
        raise BridgeError(
            "config",
            "codex_launch_denied",
            diagnostic=_launch_diagnostic(codex_bin, process, exc, stderr_bytes),
        ) from exc
    except (OSError, ValueError) as exc:
        raise BridgeError(
            "config",
            "config_read_failed",
            diagnostic=_launch_diagnostic(codex_bin, process, exc, stderr_bytes),
        ) from exc

    try:
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
        stdout_bytes, stderr_bytes = process.communicate(timeout=CONFIG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        stderr_bytes = _stop_config_process(process)
        raise BridgeError(
            "config",
            "config_read_timeout",
            retryable=True,
            diagnostic=_launch_diagnostic(codex_bin, process, exc, stderr_bytes),
        ) from exc
    except (OSError, ValueError) as exc:
        stderr_bytes = _stop_config_process(process)
        raise BridgeError(
            "config",
            "config_read_failed",
            diagnostic=_launch_diagnostic(codex_bin, process, exc, stderr_bytes),
        ) from exc

    if process.returncode != 0:
        raise BridgeError(
            "config",
            "config_read_failed",
            diagnostic=_launch_diagnostic(codex_bin, process, stderr=stderr_bytes),
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    diagnostic = _launch_diagnostic(codex_bin, process, stderr=stderr_bytes)
    response = next((item for item in _json_lines(stdout) if item.get("id") == 2), None)
    if response is None:
        raise BridgeError("config", "config_read_no_response", diagnostic=diagnostic)
    if response.get("error") is not None:
        raise BridgeError("config", "config_read_error", diagnostic=diagnostic)
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
        raise BridgeError("config", "config_read_invalid_result", diagnostic=diagnostic)
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
    config = (config_resolver or read_effective_config)(cwd)
    provider = resolve_provider(config)
    return post_chat_completion(provider, request, timeout)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    output_file: Optional[str] = None
    try:
        output_file = parse_cli_arguments(sys.argv[1:] if arguments is None else arguments)
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
    if output_file is not None:
        try:
            byte_count = write_result_file(output_file, result)
            output = _result_bytes(capture_manifest(result, output_file, byte_count))
        except BridgeError as exc:
            result = failure_result(exc)
            output = _result_bytes(result)
    else:
        output = _result_bytes(result)
    sys.stdout.buffer.write(output)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
