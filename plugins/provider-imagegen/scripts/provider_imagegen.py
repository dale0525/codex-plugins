#!/usr/bin/env python3
"""CLI-only raster image generation and editing through the active Codex provider.

The transport deliberately uses Python's standard library.  If Pillow is
available it is used only to inspect the finished PNG; a standard-library
alpha parser remains available as a fallback.  The script reads the provider
credential cache populated by Codex Sync.  No provider response body or
authorization header is written to diagnostics.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zlib

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_OUTPUT = "output/imagegen/output.png"
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_BASE64_IMAGE_CHARS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
MAX_PROMPT_CHARS = 100_000
MAX_BATCH_JOBS = 500
MAX_OUTPUT_PATH_CHARS = 4096
MAX_PNG_PIXELS = 20_000_000
MAX_PNG_DECODED_BYTES = 128 * 1024 * 1024
MAX_EDIT_IMAGES = 16
MAX_EDIT_INPUT_BYTES = 50 * 1024 * 1024
MAX_MULTIPART_FILES = MAX_EDIT_IMAGES + 1
CREDENTIAL_CACHE_DIRECTORY = ".codex-provider"
CREDENTIAL_CACHE_FILE = "credential.json"
CREDENTIAL_CACHE_SCHEMA_VERSION = 1
MAX_CREDENTIAL_CACHE_BYTES = 512 * 1024

ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
ALLOWED_BACKGROUNDS = {"transparent", "opaque", "auto"}
ALLOWED_OUTPUT_FORMATS = {"png", "jpeg", "webp"}
ALLOWED_INPUT_FIDELITIES = {"low", "high"}
HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
SIZE_PATTERN = re.compile(r"([1-9][0-9]*)x([1-9][0-9]*)$")
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


class ImagegenError(Exception):
    """A safe, user-facing failure boundary."""

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


def failure_result(error: ImagegenError) -> Dict[str, Any]:
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


def _json_reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise ImagegenError("request", "request_body_invalid") from exc


def _codex_home(environment: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environment is None else environment
    value = env.get("CODEX_HOME")
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return Path(os.path.expanduser("~/.codex"))


def _is_reparse_point(metadata: os.stat_result) -> bool:
    """Return whether Windows marked this path as a reparse point (for example, a junction)."""
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _canonical_cache_root(raw_root: Path) -> Path:
    """Resolve the cache root while rejecting a redirect at the root itself."""
    normalized = Path(os.path.normpath(os.fspath(raw_root)))
    root = normalized.resolve()
    expected = normalized.parent.resolve() / normalized.name
    if os.path.normcase(os.path.normpath(os.fspath(root))) != os.path.normcase(
        os.path.normpath(os.fspath(expected))
    ):
        raise ImagegenError("credential", "credential_cache_invalid")
    return root


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        candidate = os.path.normcase(os.path.normpath(os.fspath(path.resolve())))
        boundary = os.path.normcase(os.path.normpath(os.fspath(root)))
        return os.path.commonpath([candidate, boundary]) == boundary
    except (OSError, ValueError):
        return False


def _cache_file_is_regular(path: Path, cache_root: Optional[Path] = None) -> None:
    try:
        import stat

        current = path.parent
        while True:
            metadata = current.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or not stat.S_ISDIR(metadata.st_mode)
            ):
                raise ImagegenError("credential", "credential_cache_invalid")
            if cache_root is None:
                break
            if not _path_is_within(current, cache_root):
                raise ImagegenError("credential", "credential_cache_invalid")
            if os.path.normcase(os.path.normpath(os.fspath(current.resolve()))) == os.path.normcase(
                os.path.normpath(os.fspath(cache_root))
            ):
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        file_lstat = path.lstat()
    except OSError as exc:
        raise ImagegenError("credential", "credential_cache_unavailable") from exc
    if (
        stat.S_ISLNK(file_lstat.st_mode)
        or _is_reparse_point(file_lstat)
        or not stat.S_ISREG(file_lstat.st_mode)
    ):
        raise ImagegenError("credential", "credential_cache_invalid")


def _stable_cache_file(plugin_root: Path, cache_root: Path) -> Optional[Path]:
    plugin_directory = plugin_root.parent
    marketplace_directory = plugin_directory.parent
    try:
        marketplace_directory.relative_to(cache_root)
    except (ValueError, OSError):
        return None
    if not plugin_directory.name:
        return None
    return (
        marketplace_directory
        / CREDENTIAL_CACHE_DIRECTORY
        / plugin_directory.name
        / CREDENTIAL_CACHE_FILE
    )


def _candidate_cache_files(environment: Optional[Mapping[str, str]] = None) -> List[Path]:
    env = os.environ if environment is None else environment
    override_name = "PROVIDER_IMAGEGEN_CREDENTIAL_FILE"
    override = env.get(override_name)
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ImagegenError("credential", "credential_cache_path_invalid")
        return [path]

    plugin_root = Path(__file__).resolve().parents[1]
    cache_root_raw = _codex_home(env) / "plugins" / "cache"
    try:
        if cache_root_raw.is_symlink():
            raise ImagegenError("credential", "credential_cache_invalid")
        cache_root_metadata = cache_root_raw.lstat()
        if _is_reparse_point(cache_root_metadata):
            raise ImagegenError("credential", "credential_cache_invalid")
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ImagegenError("credential", "credential_cache_unavailable") from exc
    cache_root = _canonical_cache_root(cache_root_raw)
    direct = plugin_root / CREDENTIAL_CACHE_DIRECTORY / CREDENTIAL_CACHE_FILE
    try:
        plugin_root.relative_to(cache_root.resolve())
    except (ValueError, OSError):
        direct = None
    candidates: List[Path] = []
    if direct is not None and direct.exists():
        candidates.append(direct)
    stable = _stable_cache_file(plugin_root, cache_root)
    if stable is not None and stable.exists():
        candidates.append(stable)
    return candidates


def load_cached_provider(environment: Optional[Mapping[str, str]] = None) -> Mapping[str, Any]:
    candidates = _candidate_cache_files(environment)
    if not candidates:
        raise ImagegenError("credential", "credential_cache_missing")
    path = candidates[0]
    env = os.environ if environment is None else environment
    cache_root = None
    if not env.get("PROVIDER_IMAGEGEN_CREDENTIAL_FILE"):
        cache_root = _canonical_cache_root(_codex_home(env) / "plugins" / "cache")
    _cache_file_is_regular(path, cache_root)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ImagegenError("credential", "credential_cache_unavailable") from exc
    if len(raw) > MAX_CREDENTIAL_CACHE_BYTES:
        raise ImagegenError("credential", "credential_cache_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImagegenError("credential", "credential_cache_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CREDENTIAL_CACHE_SCHEMA_VERSION:
        raise ImagegenError("credential", "credential_cache_invalid")
    provider: Dict[str, Any] = {
        "base_url": value.get("base_url"),
        "http_headers": value.get("headers", {}),
        "env_http_headers": value.get("env_http_headers", {}),
        "env_key": value.get("env_key"),
        "query_params": value.get("query_params", {}),
        "requires_openai_auth": value.get("requires_openai_auth", False),
    }
    if not isinstance(value.get("provider"), str) or not value["provider"]:
        raise ImagegenError("credential", "credential_cache_invalid")
    if not isinstance(value.get("fingerprint"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["fingerprint"]
    ):
        raise ImagegenError("credential", "credential_cache_invalid")
    if any(key in value for key in ("token", "secret", "experimental_bearer_token", "auth")):
        raise ImagegenError("credential", "credential_cache_invalid")
    return provider


def _header_map(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ImagegenError("credential", "headers_invalid")
    result: Dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not HEADER_NAME_PATTERN.fullmatch(name) or not isinstance(header_value, str):
            raise ImagegenError("credential", "headers_invalid")
        lowered = name.lower()
        if (
            lowered in FORBIDDEN_REQUEST_HEADERS
            or lowered.startswith("proxy-")
            or lowered in {item.lower() for item in result}
            or "\r" in name
            or "\n" in name
            or "\r" in header_value
            or "\n" in header_value
        ):
            raise ImagegenError("credential", "headers_invalid")
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
            raise ImagegenError("credential", "env_headers_invalid")
        for header_name, env_name in environment_headers.items():
            if (
                not isinstance(header_name, str)
                or not HEADER_NAME_PATTERN.fullmatch(header_name)
                or not isinstance(env_name, str)
                or not env_name
            ):
                raise ImagegenError("credential", "env_headers_invalid")
            value = os.environ.get(env_name)
            if value is None:
                raise ImagegenError("credential", "env_header_unavailable")
            lowered = header_name.lower()
            if (
                lowered in FORBIDDEN_REQUEST_HEADERS
                or lowered.startswith("proxy-")
                or _header_exists(headers, header_name)
                or "\r" in value
                or "\n" in value
            ):
                raise ImagegenError("credential", "env_header_invalid")
            headers[header_name] = value

    if not _header_exists(headers, "Authorization"):
        env_key = provider.get("env_key")
        if env_key is not None:
            if not isinstance(env_key, str) or not env_key:
                raise ImagegenError("credential", "env_key_invalid")
            token = os.environ.get(env_key)
            if not token:
                raise ImagegenError("credential", "env_key_unavailable")
            if "\r" in token or "\n" in token:
                raise ImagegenError("credential", "env_key_invalid")
            headers["Authorization"] = "Bearer " + token
    if provider.get("requires_openai_auth") and not _header_exists(headers, "Authorization"):
        raise ImagegenError("credential", "credential_unavailable")
    return headers


def build_endpoint(provider: Mapping[str, Any], operation: str) -> str:
    if operation not in {"generations", "edits"}:
        raise ImagegenError("request", "operation_invalid")
    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise ImagegenError("config", "base_url_missing")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ImagegenError("config", "base_url_invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or any(character.isspace() for character in base_url)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in base_url)
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ImagegenError("config", "base_url_invalid")

    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/images/generations", "/images/edits"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path += "/images/" + operation
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    query_params = provider.get("query_params")
    if query_params is not None:
        if not isinstance(query_params, dict):
            raise ImagegenError("config", "query_params_invalid")
        for key, value in query_params.items():
            if isinstance(value, list):
                query.extend((str(key), str(item)) for item in value)
            elif isinstance(value, (str, int, float, bool)):
                query.append((str(key), str(value).lower() if isinstance(value, bool) else str(value)))
            else:
                raise ImagegenError("config", "query_params_invalid")
    try:
        return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))
    except (UnicodeError, ValueError) as exc:
        raise ImagegenError("config", "base_url_invalid") from exc


def _retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or status >= 500


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise HTTPError(request.full_url, code, "redirect_not_allowed", headers, fp)


def _normalized_hostname(value: str) -> str:
    try:
        return value.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ImagegenError("output", "image_url_invalid") from exc


def _url_port(parsed: Any) -> int:
    try:
        port = parsed.port
    except ValueError as exc:
        raise ImagegenError("output", "image_url_invalid") from exc
    if port is None:
        return 443 if parsed.scheme == "https" else 80
    return port


def _same_provider_origin(provider: Mapping[str, Any], parsed: Any) -> bool:
    base_url = provider.get("base_url")
    if not isinstance(base_url, str):
        return False
    try:
        base = urlsplit(base_url)
        base_host = base.hostname
        target_host = parsed.hostname
        if not base_host or not target_host:
            return False
        return (
            base.scheme == parsed.scheme
            and _normalized_hostname(base_host) == _normalized_hostname(target_host)
            and _url_port(base) == _url_port(parsed)
        )
    except ImagegenError:
        return False
    except ValueError:
        return False


def _validate_download_host(provider: Mapping[str, Any], parsed: Any) -> None:
    """Reject provider-returned image URLs that can target private networks.

    A user-configured provider's exact origin is allowed so local gateways can
    return a local URL. Other hosts must resolve exclusively to globally
    routable addresses; redirects remain disabled below.
    """
    if _same_provider_origin(provider, parsed):
        return
    host = parsed.hostname
    if not host:
        raise ImagegenError("output", "image_url_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise ImagegenError("output", "image_url_not_public")
        return
    try:
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(host, _url_port(parsed), type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ImagegenError("output", "image_url_dns_failed", retryable=True) from exc
    if not addresses or any(not item.is_global for item in addresses):
        raise ImagegenError("output", "image_url_not_public")


def _is_loopback_host(parsed: Any) -> bool:
    host = parsed.hostname
    if not host:
        return False
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _has_credential_headers(headers: Mapping[str, str]) -> bool:
    for name in headers:
        lowered = name.lower()
        if lowered not in {"accept", "content-type", "user-agent"}:
            return True
    return False


def _query_has_credential_name(query: str) -> bool:
    for name, _value in parse_qsl(query, keep_blank_values=True):
        lowered = name.lower().replace("-", "_")
        if any(token in lowered for token in ("api_key", "apikey", "authorization", "password", "secret", "token")):
            return True
    return False


class ImageClient:
    def __init__(self, provider: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> None:
        self.provider = provider
        self.headers = dict(headers)
        self.timeout = timeout

    def _post(self, operation: str, body: bytes, content_type: str) -> Mapping[str, Any]:
        endpoint = build_endpoint(self.provider, operation)
        headers = dict(self.headers)
        headers.setdefault("Content-Type", content_type)
        headers.setdefault("Accept", "application/json")
        try:
            parsed_endpoint = urlsplit(endpoint)
        except ValueError as exc:
            raise ImagegenError("config", "base_url_invalid") from exc
        if _query_has_credential_name(parsed_endpoint.query):
            raise ImagegenError("credential", "credential_in_url_rejected")
        if (
            parsed_endpoint.scheme == "http"
            and _has_credential_headers(headers)
            and not _is_loopback_host(parsed_endpoint)
        ):
            raise ImagegenError("credential", "insecure_http_credentials")
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            opener = build_opener(NoRedirectHandler(), ProxyHandler({}))
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
        except HTTPError as exc:
            status = int(exc.code)
            exc.close()
            code = "redirect_not_allowed" if 300 <= status < 400 else "http_error"
            raise ImagegenError("http", code, _retryable_status(status), status) from exc
        except http.client.InvalidURL as exc:
            raise ImagegenError("config", "base_url_invalid") from exc
        except ssl.SSLError as exc:
            raise ImagegenError("transport", "tls_failed") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ImagegenError("transport", "timeout", retryable=True) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise ImagegenError("transport", "timeout", retryable=True) from exc
            if isinstance(reason, (ssl.SSLError, ssl.CertificateError)):
                raise ImagegenError("transport", "tls_failed") from exc
            raise ImagegenError("transport", "connection_failed", retryable=True) from exc
        except (http.client.HTTPException, OSError, ValueError) as exc:
            raise ImagegenError("transport", "connection_failed", retryable=True) from exc
        if status < 200 or status >= 300:
            raise ImagegenError("http", "http_error", _retryable_status(status), status)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ImagegenError("protocol", "response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_json_reject_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ImagegenError("protocol", "invalid_json") from exc
        if not isinstance(value, dict):
            raise ImagegenError("protocol", "response_not_object")
        if value.get("error") is not None:
            raise ImagegenError("protocol", "provider_error")
        return value

    def post_json(self, operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post(operation, _json_bytes(payload), "application/json")

    def post_multipart(self, operation: str, fields: Mapping[str, Any], files: Sequence[Tuple[str, Path]]) -> Mapping[str, Any]:
        body, content_type = encode_multipart(fields, files)
        return self._post(operation, body, content_type)

    def download(self, url: str) -> bytes:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ImagegenError("output", "image_url_invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(character.isspace() for character in url)
        ):
            raise ImagegenError("output", "image_url_invalid")
        _url_port(parsed)
        if not _same_provider_origin(self.provider, parsed) and parsed.scheme != "https":
            raise ImagegenError("output", "cross_origin_image_url_requires_https")
        _validate_download_host(self.provider, parsed)
        request = Request(url, headers={"Accept": "image/*"}, method="GET")
        try:
            opener = build_opener(NoRedirectHandler(), ProxyHandler({}))
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_IMAGE_BYTES + 1)
                status = int(response.status)
        except HTTPError as exc:
            status = int(exc.code)
            exc.close()
            code = "redirect_not_allowed" if 300 <= status < 400 else "image_download_http_error"
            raise ImagegenError("output", code, _retryable_status(status), status) from exc
        except http.client.InvalidURL as exc:
            raise ImagegenError("output", "image_url_invalid") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ImagegenError("output", "image_download_timeout", retryable=True) from exc
        except (URLError, OSError, ValueError, ssl.SSLError) as exc:
            raise ImagegenError("output", "image_download_failed", retryable=True) from exc
        if status < 200 or status >= 300:
            raise ImagegenError("output", "image_download_http_error", _retryable_status(status), status)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ImagegenError("output", "image_too_large")
        return raw


def _validated_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImagegenError("request", "timeout_invalid")
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ImagegenError("request", "timeout_invalid") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 900:
        raise ImagegenError("request", "timeout_invalid")
    return timeout


def _read_prompt(prompt: Optional[str], prompt_file: Optional[str]) -> str:
    if prompt and prompt_file:
        raise ImagegenError("request", "prompt_sources_conflict")
    if prompt_file:
        try:
            value = Path(prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise ImagegenError("request", "prompt_file_unavailable") from exc
    elif prompt is not None:
        value = prompt
    else:
        raise ImagegenError("request", "prompt_required")
    value = value.strip()
    if not value:
        raise ImagegenError("request", "prompt_required")
    if len(value) > MAX_PROMPT_CHARS:
        raise ImagegenError("request", "prompt_too_long")
    return value


def _structured_prompt(args: argparse.Namespace) -> str:
    prompt = _read_prompt(getattr(args, "prompt", None), getattr(args, "prompt_file", None))
    labels = (
        ("Use case", getattr(args, "use_case", None)),
        ("Asset type", getattr(args, "asset_type", None)),
        ("Style/medium", getattr(args, "style", None)),
        ("Composition/framing", getattr(args, "composition", None)),
        ("Lighting/mood", getattr(args, "lighting", None)),
        ("Constraints", getattr(args, "constraints", None)),
        ("Avoid", getattr(args, "avoid", None)),
    )
    extras = [f"{label}: {value.strip()}" for label, value in labels if isinstance(value, str) and value.strip()]
    if extras:
        prompt = prompt + "\n\n" + "\n".join(extras)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ImagegenError("request", "prompt_too_long")
    return prompt


def _normalize_model(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(char) < 0x20 for char in value):
        raise ImagegenError("request", "model_invalid")
    return value.strip()


def _normalize_size(value: Any) -> str:
    if value == "auto":
        return "auto"
    if not isinstance(value, str):
        raise ImagegenError("request", "size_invalid")
    match = SIZE_PATTERN.fullmatch(value)
    if match is None:
        raise ImagegenError("request", "size_invalid")
    width, height = int(match.group(1)), int(match.group(2))
    if width > 3840 or height > 3840 or width * height > 8_294_400:
        raise ImagegenError("request", "size_invalid")
    return f"{width}x{height}"


def _normalize_choice(value: Any, allowed: Iterable[str], code: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or value not in set(allowed):
        raise ImagegenError("request", code)
    return value


def _normalize_n(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise ImagegenError("request", "n_invalid")
    return value


def build_parameters(options: Mapping[str, Any], *, edit: bool = False) -> Dict[str, Any]:
    model = _normalize_model(options.get("model", DEFAULT_MODEL))
    size = _normalize_size(options.get("size", DEFAULT_SIZE))
    quality = _normalize_choice(options.get("quality", DEFAULT_QUALITY), ALLOWED_QUALITIES, "quality_invalid")
    background = _normalize_choice(options.get("background"), ALLOWED_BACKGROUNDS, "background_invalid")
    output_format = _normalize_choice(options.get("output_format", DEFAULT_OUTPUT_FORMAT), ALLOWED_OUTPUT_FORMATS, "output_format_invalid")
    if quality is None:
        raise ImagegenError("request", "quality_invalid")
    if output_format is None:
        raise ImagegenError("request", "output_format_invalid")
    count = _normalize_n(options.get("n", 1))
    if background == "transparent" and output_format != "png":
        raise ImagegenError("request", "transparent_requires_png")
    compression = options.get("output_compression")
    if compression is not None:
        if isinstance(compression, bool) or not isinstance(compression, int) or not 0 <= compression <= 100:
            raise ImagegenError("request", "output_compression_invalid")
        if output_format == "png":
            raise ImagegenError("request", "output_compression_invalid")
    moderation = _normalize_choice(options.get("moderation"), {"auto", "low"}, "moderation_invalid")
    input_fidelity = _normalize_choice(options.get("input_fidelity"), ALLOWED_INPUT_FIDELITIES, "input_fidelity_invalid")
    if input_fidelity is not None and not edit:
        raise ImagegenError("request", "input_fidelity_edit_only")
    if input_fidelity is not None and model == DEFAULT_MODEL:
        raise ImagegenError("request", "input_fidelity_unsupported_for_model")
    payload: Dict[str, Any] = {
        "model": model,
        "size": size,
        "quality": quality,
        "n": count,
        "output_format": output_format,
    }
    if background is not None:
        payload["background"] = background
    if compression is not None:
        payload["output_compression"] = compression
    if moderation is not None:
        payload["moderation"] = moderation
    if input_fidelity is not None:
        payload["input_fidelity"] = input_fidelity
    return payload


def _validate_path_string(value: Any, code: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > MAX_OUTPUT_PATH_CHARS:
        raise ImagegenError("output", code)
    path = Path(value).expanduser()
    if path.name in {"", ".", ".."}:
        raise ImagegenError("output", code)
    return path.resolve()


def _extension_for_format(output_format: str) -> str:
    return ".jpg" if output_format == "jpeg" else "." + output_format


def _validate_output_suffix(path: Path, output_format: str) -> Path:
    expected = _extension_for_format(output_format)
    if not path.suffix:
        return path.with_suffix(expected)
    suffix = path.suffix.lower()
    accepted = {expected}
    if output_format == "jpeg":
        accepted.add(".jpeg")
    if suffix not in accepted:
        raise ImagegenError("output", "output_extension_mismatch")
    return path


def prepare_output_paths(
    output: Optional[str],
    output_dir: Optional[str],
    output_format: str,
    count: int,
    force: bool,
    *,
    create_parent: bool,
    default_name: str = "output",
) -> List[Path]:
    if output and output_dir:
        raise ImagegenError("output", "output_targets_conflict")
    if count > 1 and output:
        raise ImagegenError("output", "multiple_outputs_require_directory")
    if output_dir:
        directory = _validate_path_string(output_dir, "output_directory_invalid")
        if create_parent:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ImagegenError("output", "output_directory_unavailable") from exc
        elif directory.exists() and not directory.is_dir():
            raise ImagegenError("output", "output_directory_unavailable")
        paths = [directory / f"{default_name}_{index}{_extension_for_format(output_format)}" for index in range(1, count + 1)]
        if any(item.exists() for item in paths) and not force:
            raise ImagegenError("output", "output_exists")
        return paths
    path = _validate_path_string(output or DEFAULT_OUTPUT, "output_path_invalid")
    path = _validate_output_suffix(path, output_format)
    if create_parent:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ImagegenError("output", "output_directory_unavailable") from exc
    paths = [path]
    if any(item.exists() for item in paths) and not force:
        raise ImagegenError("output", "output_exists")
    return paths


def encode_multipart(fields: Mapping[str, Any], files: Sequence[Tuple[str, Path]]) -> Tuple[bytes, str]:
    if len(files) > MAX_MULTIPART_FILES:
        raise ImagegenError("request", "too_many_input_images")
    boundary = "----provider-imagegen-" + secrets.token_hex(16)
    chunks: List[bytes] = []
    total_file_bytes = 0
    for name, value in fields.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ImagegenError("request", "multipart_field_invalid")
        text = str(value)
        if any(ord(character) < 0x20 and character not in "\r\n\t" for character in text):
            raise ImagegenError("request", "multipart_field_invalid")
        try:
            encoded_text = text.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ImagegenError("request", "multipart_field_invalid") from exc
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                encoded_text,
                b"\r\n",
            ]
        )
    for field_name, path in files:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[\])?", field_name):
            raise ImagegenError("request", "multipart_field_invalid")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ImagegenError("request", "input_image_unavailable") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise ImagegenError("request", "input_image_too_large")
        total_file_bytes += len(data)
        if total_file_bytes > MAX_EDIT_INPUT_BYTES:
            raise ImagegenError("request", "input_images_too_large")
        filename = "".join(
            character if 0x20 <= ord(character) < 0x7F and character not in {'"', "\\"} else "_"
            for character in path.name
        )
        filename = filename.encode("ascii", "replace").decode("ascii") or "image"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("ascii"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _png_chunks(data: bytes) -> List[Tuple[bytes, bytes]]:
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImagegenError("output", "png_invalid")
    chunks: List[Tuple[bytes, bytes]] = []
    offset = 8
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ImagegenError("output", "png_invalid")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ImagegenError("output", "png_invalid")
        value = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(kind + value) & 0xFFFFFFFF != expected_crc:
            raise ImagegenError("output", "png_invalid")
        chunks.append((kind, value))
        offset = end
        if kind == b"IEND":
            if length != 0 or offset != len(data):
                raise ImagegenError("output", "png_invalid")
            saw_iend = True
            break
    if not saw_iend:
        raise ImagegenError("output", "png_invalid")
    return chunks


def _paeth(a: int, b: int, c: int) -> int:
    estimate = a + b - c
    pa, pb, pc = abs(estimate - a), abs(estimate - b), abs(estimate - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def _png_header(data: bytes) -> Tuple[List[Tuple[bytes, bytes]], int, int, int, int, int]:
    chunks = _png_chunks(data)
    if not chunks or chunks[0][0] != b"IHDR" or sum(kind == b"IHDR" for kind, _value in chunks) != 1:
        raise ImagegenError("output", "png_invalid")
    header = chunks[0][1]
    if len(header) != 13:
        raise ImagegenError("output", "png_invalid")
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", header)
    legal_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if (
        width <= 0
        or height <= 0
        or width * height > MAX_PNG_PIXELS
        or color_type not in legal_depths
        or depth not in legal_depths[color_type]
        or compression != 0
        or filter_method != 0
        or interlace not in {0, 1}
    ):
        raise ImagegenError("output", "png_invalid")
    if color_type == 3:
        palette = next((value for kind, value in chunks if kind == b"PLTE"), None)
        if palette is None or not palette or len(palette) % 3 != 0 or len(palette) > 768:
            raise ImagegenError("output", "png_invalid")
    if not any(kind == b"IDAT" for kind, _value in chunks):
        raise ImagegenError("output", "png_invalid")
    return chunks, width, height, depth, color_type, interlace


def _png_scanlines(data: bytes) -> Tuple[int, int, int, int, bytes]:
    chunks, width, height, depth, color_type, interlace = _png_header(data)
    if interlace != 0:
        raise ImagegenError("output", "png_unverifiable")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * depth + 7) // 8
    expected = height * (row_bytes + 1)
    if expected > MAX_PNG_DECODED_BYTES:
        raise ImagegenError("output", "png_unverifiable")
    compressed = b"".join(value for kind, value in chunks if kind == b"IDAT")
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(compressed, expected + 1)
        if len(decoded) > expected or decompressor.unconsumed_tail:
            raise ImagegenError("output", "png_unverifiable")
        decoded += decompressor.flush()
    except ImagegenError:
        raise
    except zlib.error as exc:
        raise ImagegenError("output", "png_invalid") from exc
    if not decompressor.eof or decompressor.unused_data or len(decoded) != expected:
        raise ImagegenError("output", "png_invalid")
    offset = 0
    for _ in range(height):
        if decoded[offset] > 4:
            raise ImagegenError("output", "png_invalid")
        offset += row_bytes + 1
    return width, height, depth, color_type, decoded


def _verify_png_alpha_without_pillow(data: bytes) -> Tuple[int, int]:
    width, height, depth, color_type, decoded = _png_scanlines(data)
    if depth != 8 or color_type not in {4, 6}:
        raise ImagegenError("output", "png_alpha_unverifiable")
    channels = {4: 2, 6: 4}[color_type]
    stride = width * channels
    previous = bytearray(stride)
    has_transparent = False
    offset = 0
    for _ in range(height):
        filter_type = decoded[offset]
        encoded = decoded[offset + 1 : offset + 1 + stride]
        offset += stride + 1
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                reconstructed = value
            elif filter_type == 1:
                reconstructed = (value + left) & 0xFF
            elif filter_type == 2:
                reconstructed = (value + up) & 0xFF
            elif filter_type == 3:
                reconstructed = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                reconstructed = (value + _paeth(left, up, up_left)) & 0xFF
            else:
                raise ImagegenError("output", "png_invalid")
            row[index] = reconstructed
        if any(alpha < 255 for alpha in row[channels - 1 :: channels]):
            has_transparent = True
        previous = row
    if not has_transparent:
        raise ImagegenError("output", "transparent_alpha_missing")
    return width, height


def verify_transparent_png(data: bytes) -> Tuple[int, int]:
    """Require a valid PNG with an actually non-opaque alpha channel."""
    width, height = _png_dimensions(data)
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return _verify_png_alpha_without_pillow(data)
    try:
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise ImagegenError("output", "png_invalid")
            image.load()
            if "A" in image.getbands():
                alpha = image.getchannel("A")
            elif "transparency" in image.info:
                alpha = image.convert("RGBA").getchannel("A")
            else:
                raise ImagegenError("output", "transparent_alpha_missing")
            extrema = alpha.getextrema()
            if not extrema or extrema[0] >= 255:
                raise ImagegenError("output", "transparent_alpha_missing")
    except ImagegenError:
        raise
    except Exception as exc:
        raise ImagegenError("output", "png_invalid") from exc
    return width, height


def _png_dimensions(data: bytes) -> Tuple[int, int]:
    _chunks, width, height, _depth, _color_type, _interlace = _png_header(data)
    return width, height


_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _validate_jpeg(data: bytes) -> None:
    if len(data) > MAX_IMAGE_BYTES or len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ImagegenError("output", "image_format_mismatch")
    index = 2
    saw_frame = False
    saw_scan = False
    saw_entropy = False
    while index < len(data):
        if data[index] != 0xFF:
            if not saw_scan:
                raise ImagegenError("output", "image_format_mismatch")
            saw_entropy = True
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            raise ImagegenError("output", "image_format_mismatch")
        marker = data[index]
        index += 1
        if marker == 0x00:
            if not saw_scan:
                raise ImagegenError("output", "image_format_mismatch")
            continue
        if marker == 0xD9:
            if index != len(data) or not saw_frame or not saw_scan or not saw_entropy:
                raise ImagegenError("output", "image_format_mismatch")
            return
        if marker == 0xD8 or marker in range(0xD0, 0xD8) or marker == 0x01:
            if marker == 0xD8 or (marker in range(0xD0, 0xD8) and not saw_scan):
                raise ImagegenError("output", "image_format_mismatch")
            continue
        if index + 2 > len(data):
            raise ImagegenError("output", "image_format_mismatch")
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2 or index + segment_length > len(data):
            raise ImagegenError("output", "image_format_mismatch")
        segment = data[index + 2 : index + segment_length]
        index += segment_length
        if marker in _JPEG_SOF_MARKERS:
            if len(segment) < 6:
                raise ImagegenError("output", "image_format_mismatch")
            height, width, components = struct.unpack(">HHB", segment[1:6])
            if width <= 0 or height <= 0 or width * height > MAX_PNG_PIXELS or components <= 0:
                raise ImagegenError("output", "image_format_mismatch")
            saw_frame = True
        elif marker == 0xDA:
            if not saw_frame or len(segment) < 2:
                raise ImagegenError("output", "image_format_mismatch")
            saw_scan = True
    raise ImagegenError("output", "image_format_mismatch")


def _validate_webp(data: bytes) -> None:
    if len(data) > MAX_IMAGE_BYTES or len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImagegenError("output", "image_format_mismatch")
    declared_size = struct.unpack("<I", data[4:8])[0]
    if declared_size != len(data) - 8:
        raise ImagegenError("output", "image_format_mismatch")
    offset = 12
    image_chunks = {b"VP8 ", b"VP8L", b"VP8X"}
    saw_image = False
    dimensions: Optional[Tuple[int, int]] = None
    canvas_dimensions: Optional[Tuple[int, int]] = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise ImagegenError("output", "image_format_mismatch")
        kind = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        end = offset + 8 + size + (size & 1)
        if end > len(data):
            raise ImagegenError("output", "image_format_mismatch")
        if kind in image_chunks:
            if size == 0:
                raise ImagegenError("output", "image_format_mismatch")
            payload = data[offset + 8 : offset + 8 + size]
            if kind == b"VP8X":
                if size < 10:
                    raise ImagegenError("output", "image_format_mismatch")
                width = 1 + int.from_bytes(payload[4:7], "little")
                height = 1 + int.from_bytes(payload[7:10], "little")
                canvas_dimensions = (width, height)
                dimensions = canvas_dimensions
            elif kind == b"VP8 ":
                if size <= 10 or payload[3:6] != b"\x9d\x01\x2a":
                    raise ImagegenError("output", "image_format_mismatch")
                width = struct.unpack("<H", payload[6:8])[0] & 0x3FFF
                height = struct.unpack("<H", payload[8:10])[0] & 0x3FFF
                if canvas_dimensions is None:
                    dimensions = (width, height)
                saw_image = True
            elif kind == b"VP8L":
                if size <= 5 or payload[0] != 0x2F:
                    raise ImagegenError("output", "image_format_mismatch")
                bits = int.from_bytes(payload[1:5], "little")
                if canvas_dimensions is None:
                    dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
                saw_image = True
        elif kind == b"ANMF":
            if size < 16:
                raise ImagegenError("output", "image_format_mismatch")
            saw_image = True
        offset = end
    if offset != len(data) or not saw_image or dimensions is None:
        raise ImagegenError("output", "image_format_mismatch")
    if dimensions[0] <= 0 or dimensions[1] <= 0 or dimensions[0] * dimensions[1] > MAX_PNG_PIXELS:
        raise ImagegenError("output", "image_format_mismatch")


def validate_image_bytes(data: bytes, output_format: str, transparent: bool) -> Dict[str, Any]:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImagegenError("output", "image_too_large")
    if output_format == "png":
        width, height, _depth, _color_type, _decoded = _png_scanlines(data)
        if transparent:
            verify_transparent_png(data)
        return {"format": "png", "width": width, "height": height, "transparent": transparent}
    if output_format == "jpeg":
        _validate_jpeg(data)
    if output_format == "webp":
        _validate_webp(data)
    if transparent:
        raise ImagegenError("output", "transparent_requires_png")
    return {"format": output_format, "transparent": False}


def _decode_response_images(response: Mapping[str, Any], client: ImageClient, expected: int) -> List[bytes]:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise ImagegenError("protocol", "image_data_missing")
    if len(data) < expected:
        raise ImagegenError("protocol", "image_data_count_mismatch")
    output: List[bytes] = []
    for item in data[:expected]:
        if not isinstance(item, dict):
            raise ImagegenError("protocol", "image_data_invalid")
        encoded = item.get("b64_json")
        if isinstance(encoded, str):
            if len(encoded) > MAX_BASE64_IMAGE_CHARS:
                raise ImagegenError("output", "image_too_large")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ImagegenError("protocol", "image_base64_invalid") from exc
        else:
            url = item.get("url")
            if not isinstance(url, str):
                raise ImagegenError("protocol", "image_data_invalid")
            image_bytes = client.download(url)
        if not image_bytes:
            raise ImagegenError("protocol", "image_data_empty")
        output.append(image_bytes)
    return output


def _write_atomic(path: Path, data: bytes, force: bool) -> int:
    if path.exists() and not force:
        raise ImagegenError("output", "output_exists")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".provider-imagegen-", suffix=".tmp", dir=str(parent))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if force:
                os.replace(temporary, str(path))
                temporary = ""
            else:
                try:
                    os.link(temporary, str(path))
                except FileExistsError as exc:
                    raise ImagegenError("output", "output_exists") from exc
                except OSError as exc:
                    raise ImagegenError("output", "output_write_failed") from exc
                os.unlink(temporary)
                temporary = ""
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    except ImagegenError:
        raise
    except OSError as exc:
        raise ImagegenError("output", "output_write_failed") from exc
    return len(data)


def _client(timeout: float) -> ImageClient:
    provider = load_cached_provider()
    headers = resolve_headers(provider)
    return ImageClient(provider, headers, timeout)


def _generation_options(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "model": args.model,
        "size": args.size,
        "quality": args.quality,
        "n": args.n,
        "background": args.background,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "moderation": args.moderation,
    }


def _edit_options(args: argparse.Namespace) -> Dict[str, Any]:
    options = _generation_options(args)
    options["input_fidelity"] = args.input_fidelity
    return options


def _success_result(
    model: str,
    output_paths: Sequence[Path],
    image_metadata: Sequence[Mapping[str, Any]],
    *,
    dry_run: bool = False,
    request: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "model": model,
            "request": request,
            "outputs": [str(path) for path in output_paths],
        }
    files = []
    for path, metadata in zip(output_paths, image_metadata):
        item: Dict[str, Any] = {"path": str(path), "bytes": path.stat().st_size}
        item.update(metadata)
        files.append(item)
    result: Dict[str, Any] = {"ok": True, "model": model, "files": files}
    if any(item.get("transparent") for item in image_metadata):
        result["transparent_verified"] = True
    return result


def run_generate(
    prompt: str,
    options: Mapping[str, Any],
    output_paths: Sequence[Path],
    *,
    client: Optional[ImageClient],
    force: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    prompt = _read_prompt(prompt, None)
    payload = build_parameters(options)
    payload["prompt"] = prompt
    transparent = payload.get("background") == "transparent"
    if dry_run:
        return _success_result(payload["model"], output_paths, [], dry_run=True, request=payload)
    if client is None:
        raise ImagegenError("runtime", "client_unavailable")
    response = client.post_json("generations", payload)
    image_bytes = _decode_response_images(response, client, len(output_paths))
    metadata = [validate_image_bytes(value, payload["output_format"], transparent) for value in image_bytes]
    for path, value in zip(output_paths, image_bytes):
        _write_atomic(path, value, force)
    return _success_result(payload["model"], output_paths, metadata)


def run_edit(
    prompt: str,
    options: Mapping[str, Any],
    output_paths: Sequence[Path],
    images: Sequence[Path],
    mask: Optional[Path],
    *,
    client: Optional[ImageClient],
    force: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    prompt = _read_prompt(prompt, None)
    payload = build_parameters(options, edit=True)
    transparent = payload.get("background") == "transparent"
    if dry_run:
        request = dict(payload)
        request["prompt"] = prompt
        request["images"] = [str(path) for path in images]
        if mask is not None:
            request["mask"] = str(mask)
        return _success_result(payload["model"], output_paths, [], dry_run=True, request=request)
    if client is None:
        raise ImagegenError("runtime", "client_unavailable")
    fields = dict(payload)
    fields["prompt"] = prompt
    files: List[Tuple[str, Path]] = [("image[]", path) for path in images]
    if mask is not None:
        files.append(("mask", mask))
    response = client.post_multipart("edits", fields, files)
    image_bytes = _decode_response_images(response, client, len(output_paths))
    metadata = [validate_image_bytes(value, payload["output_format"], transparent) for value in image_bytes]
    for path, value in zip(output_paths, image_bytes):
        _write_atomic(path, value, force)
    return _success_result(payload["model"], output_paths, metadata)


def _input_paths(values: Sequence[str], *, mask: Optional[str] = None) -> Tuple[List[Path], Optional[Path]]:
    if len(values) > MAX_EDIT_IMAGES:
        raise ImagegenError("request", "too_many_input_images")
    images: List[Path] = []
    total_bytes = 0
    for value in values:
        path = _validate_path_string(value, "input_image_invalid")
        if not path.is_file():
            raise ImagegenError("request", "input_image_unavailable")
        try:
            size = path.stat().st_size
            if size > MAX_IMAGE_BYTES:
                raise ImagegenError("request", "input_image_too_large")
            total_bytes += size
            if total_bytes > MAX_EDIT_INPUT_BYTES:
                raise ImagegenError("request", "input_images_too_large")
        except OSError as exc:
            raise ImagegenError("request", "input_image_unavailable") from exc
        images.append(path)
    if not images:
        raise ImagegenError("request", "input_image_required")
    mask_path: Optional[Path] = None
    if mask is not None:
        mask_path = _validate_path_string(mask, "mask_invalid")
        if not mask_path.is_file():
            raise ImagegenError("request", "mask_unavailable")
        try:
            mask_size = mask_path.stat().st_size
            if mask_size > MAX_IMAGE_BYTES or total_bytes + mask_size > MAX_EDIT_INPUT_BYTES:
                raise ImagegenError("request", "input_images_too_large")
        except OSError as exc:
            raise ImagegenError("request", "mask_unavailable") from exc
    return images, mask_path


def execute_generate(args: argparse.Namespace) -> Dict[str, Any]:
    prompt = _structured_prompt(args)
    payload = build_parameters(_generation_options(args))
    paths = prepare_output_paths(
        args.out,
        args.out_dir,
        payload["output_format"],
        payload["n"],
        args.force,
        create_parent=not args.dry_run,
    )
    client = None if args.dry_run else _client(_validated_timeout(args.timeout))
    return run_generate(prompt, _generation_options(args), paths, client=client, force=args.force, dry_run=args.dry_run)


def execute_edit(args: argparse.Namespace) -> Dict[str, Any]:
    prompt = _structured_prompt(args)
    images, mask = _input_paths(args.image, mask=args.mask)
    payload = build_parameters(_edit_options(args), edit=True)
    paths = prepare_output_paths(
        args.out,
        args.out_dir,
        payload["output_format"],
        payload["n"],
        args.force,
        create_parent=not args.dry_run,
    )
    client = None if args.dry_run else _client(_validated_timeout(args.timeout))
    return run_edit(
        prompt,
        _edit_options(args),
        paths,
        images,
        mask,
        client=client,
        force=args.force,
        dry_run=args.dry_run,
    )


def _batch_options(job: Mapping[str, Any]) -> Dict[str, Any]:
    options = {
        "model": job.get("model", DEFAULT_MODEL),
        "size": job.get("size", DEFAULT_SIZE),
        "quality": job.get("quality", DEFAULT_QUALITY),
        "n": job.get("n", 1),
        "background": job.get("background"),
        "output_format": job.get("output_format", DEFAULT_OUTPUT_FORMAT),
        "output_compression": job.get("output_compression"),
        "moderation": job.get("moderation"),
    }
    return options


def _batch_output_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or len(value) > MAX_OUTPUT_PATH_CHARS
    ):
        raise ImagegenError("output", "batch_output_name_invalid")
    candidate = Path(value)
    if candidate.name != value or candidate.name in {"", ".", ".."}:
        raise ImagegenError("output", "batch_output_name_invalid")
    return value


def execute_batch(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = _validate_path_string(args.input, "batch_input_invalid")
    if not input_path.is_file():
        raise ImagegenError("request", "batch_input_unavailable")
    try:
        lines = input_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ImagegenError("request", "batch_input_unavailable") from exc
    jobs: List[Mapping[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ImagegenError("request", "batch_json_invalid") from exc
        if not isinstance(job, dict) or not isinstance(job.get("prompt"), str) or not job.get("prompt", "").strip():
            raise ImagegenError("request", "batch_job_invalid")
        jobs.append(job)
    if not jobs:
        raise ImagegenError("request", "batch_empty")
    if len(jobs) > MAX_BATCH_JOBS:
        raise ImagegenError("request", "batch_too_large")
    directory = _validate_path_string(args.out_dir, "output_directory_invalid")
    if directory.exists() and not directory.is_dir():
        raise ImagegenError("output", "output_directory_unavailable")
    prepared: List[Tuple[str, Dict[str, Any], Path]] = []
    seen_paths: Set[str] = set()
    for index, job in enumerate(jobs, start=1):
        options = _batch_options(job)
        payload = build_parameters(options)
        prompt = _read_prompt(job["prompt"], None)
        if payload["n"] != 1:
            raise ImagegenError("request", "batch_n_must_be_one")
        requested_name = _batch_output_name(job.get("out"))
        if requested_name is None:
            filename = f"image_{index}{_extension_for_format(payload['output_format'])}"
        else:
            filename = requested_name
        path = _validate_output_suffix(directory / filename, payload["output_format"])
        key = os.path.normcase(str(path))
        if key in seen_paths:
            raise ImagegenError("output", "batch_output_duplicate")
        seen_paths.add(key)
        if path.exists() and not args.force:
            raise ImagegenError("output", "output_exists")
        prepared.append((prompt, options, path))

    if not args.dry_run:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ImagegenError("output", "output_directory_unavailable") from exc
    client = None if args.dry_run else _client(_validated_timeout(args.timeout))
    results: List[Mapping[str, Any]] = []
    for prompt, options, path in prepared:
        try:
            result = run_generate(
                prompt,
                options,
                [path],
                client=client,
                force=args.force,
                dry_run=args.dry_run,
            )
        except ImagegenError as error:
            completed_files = [
                file_item
                for completed_result in results
                for file_item in completed_result.get("files", [])
            ]
            if not completed_files:
                raise
            diagnostic = dict(error.diagnostic or {})
            diagnostic["completed_files"] = completed_files
            raise ImagegenError(
                error.stage,
                error.code,
                error.retryable,
                error.http_status,
                diagnostic,
            ) from error
        results.append(result)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "jobs": results}
    files = [file_item for result in results for file_item in result.get("files", [])]
    return {"ok": True, "jobs": len(jobs), "files": files}


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--background", choices=sorted(ALLOWED_BACKGROUNDS))
    parser.add_argument("--output-format", choices=sorted(ALLOWED_OUTPUT_FORMATS), default=DEFAULT_OUTPUT_FORMAT)
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation", choices=["auto", "low"])
    parser.add_argument("--out")
    parser.add_argument("--out-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--use-case")
    parser.add_argument("--asset-type")
    parser.add_argument("--style")
    parser.add_argument("--composition")
    parser.add_argument("--lighting")
    parser.add_argument("--constraints")
    parser.add_argument("--avoid")


def parse_arguments(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or edit images through the active Codex provider.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    _add_common_options(generate)

    edit = subparsers.add_parser("edit")
    _add_common_options(edit)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--input-fidelity", choices=sorted(ALLOWED_INPUT_FIDELITIES))

    batch = subparsers.add_parser("generate-batch")
    batch.add_argument("--input", required=True)
    batch.add_argument("--out-dir", required=True)
    batch.add_argument("--force", action="store_true")
    batch.add_argument("--dry-run", action="store_true")
    batch.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(arguments)


def execute(args: argparse.Namespace) -> Dict[str, Any]:
    if args.command == "generate":
        return execute_generate(args)
    if args.command == "edit":
        return execute_edit(args)
    if args.command == "generate-batch":
        return execute_batch(args)
    raise ImagegenError("request", "command_invalid")


def main(arguments: Optional[Sequence[str]] = None) -> int:
    try:
        result = execute(parse_arguments(arguments))
    except ImagegenError as error:
        result = failure_result(error)
    except Exception:
        result = failure_result(ImagegenError("runtime", "unexpected_error"))
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
