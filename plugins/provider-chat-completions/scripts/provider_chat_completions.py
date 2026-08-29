#!/usr/bin/env python3
"""One-shot, provider-neutral OpenAI-compatible Chat Completions caller."""

from __future__ import annotations

import json
import http.client
import ipaddress
import math
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import (  # nosec B310 - URL is validated before opening
    Request,
    build_opener,
    HTTPRedirectHandler,
    ProxyHandler,
)

from windows_acl import WindowsAclError, ensure_owner_only

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
OUTPUT_PERMISSION_TIMEOUT_SECONDS = 10.0
MAX_OUTPUT_FILE_PATH_CHARS = 4096
CREDENTIAL_CACHE_DIRECTORY = ".codex-provider"
CREDENTIAL_CACHE_FILE = "credential.json"
CREDENTIAL_CACHE_SCHEMA_VERSION = 1
MAX_CREDENTIAL_CACHE_BYTES = 512 * 1024
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
        or len(value) > MAX_OUTPUT_FILE_PATH_CHARS
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


def _restrict_output_permissions(path: str) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise BridgeError("output", "result_file_permissions_failed") from exc
        return

    username = os.environ.get("USERNAME")
    if not isinstance(username, str) or not username:
        try:
            username = os.getlogin()
        except OSError as exc:
            raise BridgeError("output", "result_file_permissions_failed") from exc
    try:
        subprocess.run(
            [
                "icacls",
                path,
                "/inheritance:r",
                "/grant:r",
                f"{username}:F",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=OUTPUT_PERMISSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError("output", "result_file_permissions_failed") from exc


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
        _restrict_output_permissions(temporary_path)
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
        content = result.get("content")
        if isinstance(content, str):
            manifest["content_chars"] = len(content)
        elif content is not None:
            manifest["content_items"] = len(content) if isinstance(content, list) else 0
    return manifest


def _codex_home(environment: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environment is None else environment
    value = env.get("CODEX_HOME")
    if isinstance(value, str) and value.strip():
        return os.path.expanduser(value)
    return os.path.expanduser("~/.codex")


def _cache_file_is_secure(path: str) -> None:
    import stat

    try:
        parent_lstat = os.lstat(os.path.dirname(path))
        file_lstat = os.lstat(path)
    except OSError as exc:
        raise BridgeError("credential", "credential_cache_unavailable") from exc
    if (
        stat.S_ISLNK(parent_lstat.st_mode)
        or not stat.S_ISDIR(parent_lstat.st_mode)
        or stat.S_ISLNK(file_lstat.st_mode)
        or not stat.S_ISREG(file_lstat.st_mode)
    ):
        raise BridgeError("credential", "credential_cache_invalid")
    if os.name == "nt":
        try:
            ensure_owner_only(os.path.dirname(path))
            ensure_owner_only(path)
        except WindowsAclError as exc:
            raise BridgeError("credential", "credential_cache_permissions") from exc
        return
    try:
        parent_stat = os.stat(os.path.dirname(path))
        file_stat = os.stat(path)
    except OSError as exc:
        raise BridgeError("credential", "credential_cache_unavailable") from exc
    if stat.S_IMODE(parent_stat.st_mode) & 0o077 or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise BridgeError("credential", "credential_cache_permissions")


def _candidate_cache_files(environment: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environment is None else environment
    override = env.get("PROVIDER_CHAT_CREDENTIAL_FILE")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        if not os.path.isabs(override):
            raise BridgeError("credential", "credential_cache_path_invalid")
        return [path]
    plugin_root = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
    cache_root_raw = os.path.join(_codex_home(env), "plugins", "cache")
    try:
        if os.path.islink(cache_root_raw):
            raise BridgeError("credential", "credential_cache_invalid")
    except OSError as exc:
        raise BridgeError("credential", "credential_cache_unavailable") from exc
    cache_root = os.path.realpath(cache_root_raw)
    direct = os.path.join(plugin_root, CREDENTIAL_CACHE_DIRECTORY, CREDENTIAL_CACHE_FILE)
    candidates: List[str] = []
    try:
        if os.path.commonpath([plugin_root, cache_root]) == cache_root and os.path.isfile(direct):
            candidates.append(direct)
    except ValueError:
        pass
    return candidates


def load_cached_provider(environment: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
    candidates = _candidate_cache_files(environment)
    if not candidates:
        raise BridgeError("credential", "credential_cache_missing")
    path = candidates[0]
    _cache_file_is_secure(path)
    try:
        with open(path, "rb") as stream:
            raw = stream.read()
    except OSError as exc:
        raise BridgeError("credential", "credential_cache_unavailable") from exc
    if len(raw) > MAX_CREDENTIAL_CACHE_BYTES:
        raise BridgeError("credential", "credential_cache_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("credential", "credential_cache_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CREDENTIAL_CACHE_SCHEMA_VERSION:
        raise BridgeError("credential", "credential_cache_invalid")
    if not isinstance(value.get("provider"), str) or not value["provider"]:
        raise BridgeError("credential", "credential_cache_invalid")
    if not isinstance(value.get("fingerprint"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["fingerprint"]
    ):
        raise BridgeError("credential", "credential_cache_invalid")
    if any(key in value for key in ("token", "secret", "experimental_bearer_token", "auth")):
        raise BridgeError("credential", "credential_cache_invalid")
    return {
        "base_url": value.get("base_url"),
        "http_headers": value.get("headers", {}),
        "env_http_headers": value.get("env_http_headers", {}),
        "env_key": value.get("env_key"),
        "query_params": value.get("query_params", {}),
        "requires_openai_auth": value.get("requires_openai_auth", False),
    }


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


def _has_credential_headers(headers: Mapping[str, str]) -> bool:
    return any(name.lower() not in {"accept", "content-type", "user-agent"} for name in headers)


def _is_loopback_host(parsed: Any) -> bool:
    host = parsed.hostname
    if not host or host.rstrip(".").lower() == "localhost":
        return bool(host)
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _query_has_credential_name(query: str) -> bool:
    for name, _value in parse_qsl(query, keep_blank_values=True):
        normalized = name.lower().replace("-", "_")
        if any(part in normalized for part in ("api_key", "apikey", "authorization", "password", "secret", "token")):
            return True
    return False


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
        if env_key is not None:
            if not isinstance(env_key, str) or not env_key:
                raise BridgeError("credential", "env_key_invalid")
            token = os.environ.get(env_key)
            if not token:
                raise BridgeError("credential", "env_key_unavailable")
            if "\r" in token or "\n" in token:
                raise BridgeError("credential", "env_key_invalid")
            headers["Authorization"] = "Bearer " + token
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
    if _query_has_credential_name(urlencode(query)):
        raise BridgeError("credential", "credential_in_url_rejected")
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
    try:
        parsed_endpoint = urlsplit(endpoint)
    except ValueError as exc:
        raise BridgeError("config", "base_url_invalid") from exc
    if (
        parsed_endpoint.scheme == "http"
        and _has_credential_headers(headers)
        and not _is_loopback_host(parsed_endpoint)
    ):
        raise BridgeError("credential", "insecure_http_credentials")
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
    provider_resolver: Optional[Callable[[], Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    if not isinstance(request, Mapping):
        raise BridgeError("request", "request_not_object")
    if "cwd" in request:
        raise BridgeError("request", "cwd_override_not_allowed")
    timeout = _validated_timeout(request.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    # Validate the complete request body before touching the credential cache.
    # This keeps malformed input local and cheap.
    _encode_json_body(build_request(request))
    provider = (provider_resolver or load_cached_provider)()
    return post_chat_completion(provider, request, timeout)


def _decode_stdin_json(raw: bytes) -> str:
    """Decode JSON from UTF-8 or the UTF-16 forms emitted by Windows PowerShell."""
    encodings = []
    if raw.startswith(b"\xef\xbb\xbf"):
        encodings.append("utf-8-sig")
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    elif len(raw) >= 4 and len(raw) % 2 == 0:
        # Windows PowerShell 5.1 can pass UTF-16LE without a BOM when a native
        # process is on the receiving end of a pipeline.  JSON punctuation is
        # ASCII, so the zero-byte distribution provides a useful first guess.
        even_zeroes = raw[0::2].count(0)
        odd_zeroes = raw[1::2].count(0)
        threshold = max(1, len(raw) // 4)
        if odd_zeroes >= threshold and odd_zeroes > even_zeroes:
            encodings.append("utf-16-le")
        elif even_zeroes >= threshold and even_zeroes > odd_zeroes:
            encodings.append("utf-16-be")
    encodings.extend(("utf-8", "utf-16-le", "utf-16-be"))

    seen = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        return text
    # Preserve the normal exception boundary for malformed or undecodable
    # input; main() maps either decode or parse failure to invalid_json.
    return raw.decode("utf-8")


def main(arguments: Optional[Sequence[str]] = None) -> int:
    output_file: Optional[str] = None
    try:
        output_file = parse_cli_arguments(sys.argv[1:] if arguments is None else arguments)
        request = json.loads(_decode_stdin_json(sys.stdin.buffer.read()))
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
