"""Provider-neutral bridge for OpenAI-compatible Chat Completions models.

The module deliberately uses only Python's standard library.  Keeping the
transport small makes the outbound boundary easy to audit and lets the same
code run from a copied plugin cache without a repository checkout.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import sys
import tomllib
from typing import Any, Callable, Iterable
import unicodedata
import urllib.error
import urllib.request

try:
    from .core import TransportDiagnostic, TransportPhase, diagnostic_for
    from .transport_client import ResponsesClient
except ImportError:  # direct mcp/ path execution
    from core import TransportDiagnostic, TransportPhase, diagnostic_for
    from transport_client import ResponsesClient


SYSTEM_PROMPT = "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"
BRIDGE_VERSION = "0.2.0"
USER_AGENT = f"creative-model-bridge/{BRIDGE_VERSION}"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CHARS = 180_000
DEFAULT_MAX_OUTPUT_TOKENS = 60000
_GLOB_MARKERS = re.compile(r"[*?\[\]{}]")
_BINARY_MAGICS = (
    b"\x89PNG\r\n\x1a\n",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"%PDF-",
    b"GIF87a",
    b"GIF89a",
    b"\xff\xd8\xff",
    b"\x1f\x8b\x08",
    b"SQLite format 3\x00",
    b"\x7fELF",
)
_COMMON_CJK = set("的一是在不了有我他她它你们这个和与为也就都而从对要会能可以还上着下到说看用天人中文简体繁體日本語韩国어故事场景角色第")
REQUEST_FIELDS = {
    "task",
    "model",
    "context_text",
    "context_files",
    "constraints",
    "output_spec",
    "max_output_tokens",
    "temperature",
    "system_mode",
}
REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "context_text": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "minLength": 1},
                    "text": {"type": "string"},
                },
                "required": ["label", "text"],
                "additionalProperties": False,
            },
        },
        "context_files": {
            "type": "array",
            "description": "Ordered host-OS absolute paths to regular text files; runtime validation uses os.path.isabs.",
            "items": {
                "type": "string",
                "minLength": 1,
                "description": "Absolute path according to the host OS (POSIX, Windows drive, or UNC syntax).",
            },
        },
        "constraints": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
        "output_spec": {
            "oneOf": [
                {"type": "string"},
                {"type": "object"},
                {"type": "array"},
                {"type": "null"},
            ]
        },
        "max_output_tokens": {"type": "integer", "minimum": 1},
        "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        "system_mode": {"type": "string", "enum": ["minimal", "none"]},
    },
    "required": ["task"],
    "additionalProperties": False,
}


class BridgeError(Exception):
    """A safe, user-facing bridge error.

    Error messages never include provider configuration values or response
    bodies.  That property is important because bearer tokens can be present
    in development-only configuration fields.
    """

    def __init__(self, message: str, *, transport_diagnostic: TransportDiagnostic | None = None) -> None:
        super().__init__(message); self.transport_diagnostic = transport_diagnostic


class ConfigError(BridgeError):
    """The local Codex provider configuration is missing or invalid."""


class FileContextError(BridgeError):
    """A context file is not an acceptable regular text file."""


def _transport_error(message: str, error: BaseException, phase: TransportPhase, enabled: bool) -> BridgeError:
    return BridgeError(message, transport_diagnostic=diagnostic_for(error, phase) if enabled else None)


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    default_model: str | None
    base_url: str
    wire_api: str
    env_key: str | None
    experimental_bearer_token: str | None

    def credential(self) -> str:
        """Resolve a credential without exposing it in an exception or result."""

        if self.env_key:
            value = os.environ.get(self.env_key)
            if value:
                return value
            fixed_channel = os.environ.get("CREATIVE_MODEL_API_KEY")
            if fixed_channel:
                return fixed_channel
            raise ConfigError("provider credential is not available in the configured environment")
        fixed_channel = os.environ.get("CREATIVE_MODEL_API_KEY")
        if fixed_channel:
            return fixed_channel
        if self.experimental_bearer_token:
            return self.experimental_bearer_token
        raise ConfigError("provider credential is not configured")


@dataclass(frozen=True)
class ContextFile:
    path: str
    text: str
    chars: int
    sha256: str
    encoding: str

    def report(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "chars": self.chars,
            "sha256": self.sha256,
            "encoding": self.encoding,
        }


@dataclass(frozen=True)
class TextBlock:
    label: str
    text: str


class ConfigLoader:
    """Read only the provider values owned by this plugin."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path is not None else None

    def _path(self) -> Path:
        if self.config_path is not None:
            return self.config_path
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home) / "config.toml"
        return Path.home() / ".codex" / "config.toml"

    def load(self) -> ProviderConfig:
        path = self._path()
        try:
            with path.open("rb") as handle:
                config = tomllib.load(handle)
        except FileNotFoundError as error:
            raise ConfigError("Codex config.toml was not found") from error
        except OSError as error:
            raise ConfigError("Codex config.toml could not be read") from error
        except tomllib.TOMLDecodeError as error:
            raise ConfigError("Codex config.toml is not valid TOML") from error

        shell_policy = config.get("shell_environment_policy")
        shell_set = shell_policy.get("set") if isinstance(shell_policy, dict) else None
        provider_name = shell_set.get("CREATIVE_MODEL_PROVIDER") if isinstance(shell_set, dict) else None
        default_model = shell_set.get("CREATIVE_MODEL_DEFAULT") if isinstance(shell_set, dict) else None
        if not isinstance(provider_name, str) or not provider_name:
            raise ConfigError("CREATIVE_MODEL_PROVIDER is not configured")
        if default_model is not None and (not isinstance(default_model, str) or not default_model):
            raise ConfigError("CREATIVE_MODEL_DEFAULT must be a non-empty string")

        providers = config.get("model_providers")
        provider = providers.get(provider_name) if isinstance(providers, dict) else None
        if not isinstance(provider, dict):
            raise ConfigError("configured creative model provider was not found")
        base_url = provider.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ConfigError("configured creative model provider has no base_url")
        wire_api = provider.get("wire_api")
        if wire_api not in {"responses", "chat_completions"}:
            raise ConfigError("creative model provider must use wire_api = responses or chat_completions")
        env_key = provider.get("env_key")
        if env_key is not None and (not isinstance(env_key, str) or not env_key):
            raise ConfigError("provider env_key must be a non-empty string")
        bearer = provider.get("experimental_bearer_token")
        if bearer is not None and (not isinstance(bearer, str) or not bearer):
            raise ConfigError("provider experimental_bearer_token must be a non-empty string")
        return ProviderConfig(
            name=provider_name,
            default_model=default_model,
            base_url=base_url.rstrip("/"),
            wire_api=wire_api,
            env_key=env_key,
            experimental_bearer_token=bearer,
        )


def _looks_like_text(text: str) -> bool:
    """Reject obvious binary payloads while permitting normal control spacing."""

    if "\x00" in text or "\ufffd" in text:
        return False
    allowed_controls = {"\n", "\r", "\t", "\f", "\b"}
    return all(
        unicodedata.category(char) not in {"Cc", "Cs"} or char in allowed_controls
        for char in text
    )


def _looks_like_binary(raw: bytes) -> bool:
    if any(raw.startswith(magic) for magic in _BINARY_MAGICS):
        return True
    if len(raw) >= 32:
        high_bytes = sum(byte >= 0x80 for byte in raw)
        if high_bytes / len(raw) > 0.25 and len(set(raw)) <= 4:
            return True
    return False


def _legacy_confident(raw: bytes, text: str) -> bool:
    """Accept legacy text only with a visible linguistic signal.

    Latin-1/CP1252 are intentionally not candidates: they decode every byte
    and therefore cannot distinguish binary data.  The East Asian codecs below
    are accepted only when they produce printable, non-ASCII text and the raw
    bytes are not a tiny repeated binary pattern.
    """

    if not text or not _looks_like_text(text):
        return False
    non_ascii = sum(ord(char) > 127 for char in text)
    if non_ascii == 0:
        return False
    if len(raw) >= 32 and len(set(raw)) <= 4:
        return False
    printable = sum(char.isprintable() or char in "\n\r\t\f\b" for char in text)
    return printable / len(text) >= 0.95


def _legacy_score(text: str) -> int:
    """Score visible script/frequency signals for deterministic codec choice."""

    hangul = sum("\uac00" <= char <= "\ud7a3" for char in text)
    kana = sum("\u3040" <= char <= "\u30ff" for char in text)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    common = sum(char in _COMMON_CJK for char in text)
    length = max(len(text), 1)
    script_bonus = 0
    if hangul >= 2 and hangul / length >= 0.8:
        script_bonus += hangul * 100
    if kana / length >= 0.35:
        script_bonus += kana * 100
        if common == 0 and cjk and kana < 3:
            script_bonus -= kana * 100
    return script_bonus + common * 20 + cjk * 2


def _decode_file(raw: bytes, path: Path) -> tuple[str, str]:
    if _looks_like_binary(raw):
        raise FileContextError(f"context file has a binary signature: {path}")
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            text = raw.decode("utf-8-sig")
            if _looks_like_text(text):
                return text, "utf-8-sig"
            raise UnicodeDecodeError("utf-8", raw, 0, len(raw), "binary control characters")
        except UnicodeDecodeError as error:
            raise FileContextError(f"context file is not valid UTF-8: {path}") from error
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
            if _looks_like_text(text):
                return text, "utf-16"
            raise UnicodeDecodeError("utf-16", raw, 0, len(raw), "binary control characters")
        except UnicodeDecodeError as error:
            raise FileContextError(f"context file is not valid UTF-16: {path}") from error

    try:
        text = raw.decode("utf-8")
        if _looks_like_text(text):
            return text, "utf-8"
    except UnicodeDecodeError:
        pass
    else:
        # A successfully decoded UTF-8 stream with disallowed Unicode control
        # characters is binary-like; do not reinterpret it as legacy text.
        raise FileContextError(f"context file contains Unicode control characters: {path}")

    # Legacy candidates are strict-decoded and require a linguistic signal;
    # no single-byte codec is used as an unconditional fallback.
    candidates: list[tuple[int, int, str, str]] = []
    for rank, encoding in enumerate((
        "gb18030",
        "big5",
        "shift_jis",
        "euc_jp",
        "euc_kr",
    )):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _legacy_confident(raw, text):
            candidates.append((_legacy_score(text), rank, encoding, text))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        _, _, encoding, text = candidates[0]
        return text, encoding
    raise FileContextError(f"context file is not recognized as regular text: {path}")


def read_context_files(paths: Iterable[str] | None) -> list[ContextFile]:
    """Read ordered, absolute, bounded regular text files."""

    if paths is None:
        return []
    if isinstance(paths, (str, bytes)):
        raise FileContextError("context_files must be an ordered array of absolute paths")
    try:
        requested = list(paths)
    except TypeError as error:
        raise FileContextError("context_files must be an ordered array of absolute paths") from error

    total_chars = 0
    result: list[ContextFile] = []
    for value in requested:
        if not isinstance(value, str) or not value:
            raise FileContextError("each context file path must be a non-empty absolute string")
        if not os.path.isabs(value):
            raise FileContextError("context file paths must be absolute")
        if _GLOB_MARKERS.search(value):
            raise FileContextError("context file paths may not contain glob patterns")
        candidate = Path(value)
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate, flags)
        except OSError as error:
            raise FileContextError("context file could not be opened safely") from error
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise FileContextError(f"context path is not a regular file: {candidate}")
            if file_stat.st_size > MAX_FILE_BYTES:
                raise FileContextError(f"context file exceeds the 2 MiB limit: {candidate}")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                raw = handle.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise FileContextError(f"context file exceeds the 2 MiB limit: {candidate}")
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                resolved = candidate.absolute()
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        text, encoding = _decode_file(raw, resolved)
        chars = len(text)
        total_chars += chars
        if total_chars > MAX_TOTAL_CHARS:
            raise FileContextError("decoded context exceeds the 180000-character total limit")
        result.append(
            ContextFile(
                path=str(resolved),
                text=text,
                chars=chars,
                sha256=hashlib.sha256(raw).hexdigest(),
                encoding=encoding,
            )
        )
    return result


def _text_blocks(value: Any) -> list[TextBlock]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BridgeError("context_text must be an ordered array of labeled blocks")
    blocks: list[TextBlock] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"label", "text"}
            or not isinstance(item.get("label"), str)
            or not isinstance(item.get("text"), str)
        ):
            raise BridgeError("each context_text block needs string label and text")
        if not item["label"]:
            raise BridgeError("context_text block labels must not be empty")
        blocks.append(TextBlock(label=item["label"], text=item["text"]))
    return blocks


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise BridgeError(f"{field} must be a string or ordered array of strings")
    return value


def _output_spec(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raise BridgeError("output_spec must be a string or JSON object")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BridgeError(f"{field} must be a finite number")
    return float(value)


def _request_values(request: dict[str, Any]) -> tuple[str, str | None, list[TextBlock], list[str], str | None, int, float | None, str]:
    unknown = set(request) - REQUEST_FIELDS
    if unknown:
        raise BridgeError(f"unsupported request field(s): {sorted(unknown)}")
    task = request.get("task")
    if not isinstance(task, str) or not task:
        raise BridgeError("task is required and must be a non-empty string")
    model = request.get("model")
    if model is not None and (not isinstance(model, str) or not model):
        raise BridgeError("model must be a non-empty string when provided")
    blocks = _text_blocks(request.get("context_text"))
    files = request.get("context_files")
    if files is not None and not isinstance(files, list):
        raise FileContextError("context_files must be an ordered array of absolute paths")
    constraints = _string_list(request.get("constraints"), "constraints")
    output_spec = _output_spec(request.get("output_spec"))
    max_tokens = request.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise BridgeError("max_output_tokens must be a positive integer")
    temperature = request.get("temperature")
    if temperature is not None:
        temperature = _finite_number(temperature, "temperature")
        if not 0 <= temperature <= 2:
            raise BridgeError("temperature must be between 0 and 2")
    system_mode = request.get("system_mode", "minimal")
    if system_mode not in {"minimal", "none"}:
        raise BridgeError("system_mode must be minimal or none")
    return task, model, blocks, files or [], output_spec, max_tokens, temperature, system_mode


def build_prompt(task: str, blocks: list[TextBlock], files: list[ContextFile], constraints: list[str], output_spec: str | None) -> str:
    sections = [f"任务:\n{task}"]
    if constraints:
        sections.append("约束:\n" + "\n".join(f"- {item}" for item in constraints))
    if output_spec is not None:
        sections.append(f"输出规格:\n{output_spec}")
    if blocks:
        sections.append("上下文文字:\n" + "\n\n".join(f"[{block.label}]\n{block.text}" for block in blocks))
    if files:
        sections.append("上下文文件:\n" + "\n\n".join(f"[{item.path}]\n{item.text}" for item in files))
    return "\n\n".join(sections)


_TEXT_TYPES = frozenset({"output_text", "text", "text_delta", "output_text_delta", "message", "assistant", "completion", "output"})
_NON_TEXT_TYPES = frozenset({
    "input_text", "input_image", "reasoning", "refusal", "tool", "tool_call", "tool_result", "tool_output",
    "function", "function_call", "function_call_output", "function_output", "function_result", "computer_call", "file_search_call", "web_search_call",
})
_REJECTED_TYPES = _NON_TEXT_TYPES | {
    "system", "developer", "user", "function_result", "summary", "analysis", "reasoning_content"
}
_KNOWN_RESPONSE_STATUSES = frozenset({"completed", "incomplete", "failed", "in_progress", "queued", "cancelled"})
_BLOCKED_RESPONSE_STATUSES = frozenset({"failed", "cancelled", "queued", "in_progress"})
_SAFE_TYPE_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SAFE_FIELD_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
_UUID_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SAFE_FIELDS = frozenset({
    "id", "object", "created_at", "status", "output", "output_text", "content", "choices", "response", "result", "data",
    "type", "role", "text", "value", "message", "delta", "assistant", "model", "usage", "annotations", "summary", "parts",
    "incomplete_details", "error", "code", "name", "reason", "details", "length", "items", "item", "input", "created",
})
_MAX_SHAPE_ITEMS = 8
_MAX_SHAPE_FIELDS = 32
_MAX_SHAPE_DEPTH = 5
_MAX_DIAGNOSTIC_CHARS = 12000
_SHAPE_FIELDS = ("output", "content", "choices", "response", "result", "data", "error", "incomplete_details")


def _json_kind(value: Any) -> str:
    return {
        type(None): "null", bool: "boolean", str: "string", int: "integer",
        float: "number", list: "array", dict: "object",
    }.get(type(value), "other")


def _field_names(value: dict[str, Any]) -> list[str]:
    safe: set[str] = set()
    unknown = 0
    for key in value:
        if isinstance(key, str) and key in _SAFE_FIELDS and _SAFE_FIELD_VALUE.fullmatch(key):
            safe.add(key)
        else:
            unknown += 1
    names = sorted(safe)[:_MAX_SHAPE_FIELDS]
    if len(safe) > _MAX_SHAPE_FIELDS:
        names.append("<fields_truncated>")
    if unknown:
        names.append(f"<unknown_fields:{min(unknown, 9999)}>")
    return names


def _shape(value: Any, *, include_items: bool = False, depth: int = 0) -> dict[str, Any]:
    kind = _json_kind(value)
    if kind in {"object", "array"} and depth >= _MAX_SHAPE_DEPTH:
        return {"type": kind, "truncated": True}
    if kind == "object":
        result: dict[str, Any] = {"type": kind, "fields": _field_names(value)}
        declared = value.get("type")
        normalized = declared.lower() if isinstance(declared, str) else ""
        if isinstance(declared, str) and _SAFE_TYPE_VALUE.fullmatch(declared) and normalized in _TEXT_TYPES | _NON_TEXT_TYPES:
            result["declared_type"] = normalized
        for field in _SHAPE_FIELDS:
            if field in value:
                result[field] = _shape(value[field], include_items=True, depth=depth + 1)
        return result
    if kind == "array":
        result: dict[str, Any] = {"type": kind, "length": len(value)}
        if include_items:
            result["items"] = [_shape(item, include_items=True, depth=depth + 1) for item in value[:_MAX_SHAPE_ITEMS]]
            if len(value) > _MAX_SHAPE_ITEMS:
                result["items_truncated"] = True
        return result
    return {"type": kind}


def _response_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": _json_kind(payload)}
    report: dict[str, Any] = {"type": "object", "top_level_fields": _field_names(payload)}
    for field in _SHAPE_FIELDS:
        if field in payload:
            report[field] = _shape(payload[field], include_items=field in {"output", "content", "choices"})
    return report


def _response_status(payload: Any, depth: int = 0) -> str | None:
    if depth >= _MAX_SHAPE_DEPTH:
        return None
    if isinstance(payload, list):
        for item in payload[:_MAX_SHAPE_ITEMS]:
            status = _response_status(item, depth + 1)
            if status is not None:
                return status
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if isinstance(status, str) and status in _KNOWN_RESPONSE_STATUSES:
        return status
    for field in _SHAPE_FIELDS:
        nested = payload.get(field)
        status = _response_status(nested, depth + 1)
        if status is not None:
            return status
    return None


def _response_diagnostic(prefix: str, payload: Any, *, request_id: str | None, http_status: int | None) -> str:
    response_status = _response_status(payload)
    metadata = {
        "http_status": http_status if isinstance(http_status, int) else None,
        "response_status": response_status,
        "response_shape": _response_shape(payload),
    }
    metadata.update(_request_id_metadata(request_id))
    rendered = f"{prefix}; " + json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rendered if len(rendered) <= _MAX_DIAGNOSTIC_CHARS else rendered[:_MAX_DIAGNOSTIC_CHARS - 27] + "...<diagnostic_truncated>"


def _request_id_metadata(value: Any) -> dict[str, str | None]:
    if isinstance(value, str) and _UUID_REQUEST_ID.fullmatch(value):
        return {"request_id": value}
    if isinstance(value, str):
        return {"request_id_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
    return {"request_id": None}


def _marker_has_content(value: Any, exempt: frozenset[str] = frozenset()) -> bool:
    for key, marker in value.items():
        if not isinstance(key, str):
            continue
        normalized = key.lower()
        if normalized in exempt:
            continue
        if (
            normalized in _REJECTED_TYPES
            or normalized.startswith(("tool", "function", "refusal", "reasoning", "analysis"))
        ) and _has_value(marker):
            return True
    return False


def _has_value(value: Any) -> bool:
    return value is not None and value is not False and (not isinstance(value, (str, list, dict)) or bool(value))


def _response_blocks_text(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    return (isinstance(status, str) and status in _BLOCKED_RESPONSE_STATUSES) or (
        "error" in payload and _has_value(payload["error"])
    )


def _identity_allowed(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    if "role" in value and value.get("role") != "assistant":
        return False
    marker = value.get("type")
    if "type" in value and not isinstance(marker, str):
        return False
    if isinstance(marker, str):
        marker = marker.lower()
        if marker in _REJECTED_TYPES or any(token in marker for token in ("tool", "function", "refusal", "reasoning")):
            return False
    return True


def _node_allowed(value: Any) -> bool:
    if not _identity_allowed(value):
        return False
    if not isinstance(value, dict):
        return True
    return not _response_blocks_text(value) and not _marker_has_content(value)


def _allows_text_type(value: Any) -> bool:
    return value is None or isinstance(value, str) and value.lower() in _TEXT_TYPES


def _text_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return [value["value"]]
    return []


def _content_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_content_text(item))
        return chunks
    if not isinstance(value, dict) or not _node_allowed(value) or not _allows_text_type(value.get("type")):
        return []
    if "output_text" in value:
        chunks = _direct_text(value["output_text"])
        if chunks:
            return chunks
    chunks = _text_value(value.get("text"))
    if chunks:
        return chunks
    if isinstance(value.get("value"), str):
        return [value["value"]]
    return _content_text(value["content"]) if "content" in value else []


def _output_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_output_text(item))
        return chunks
    if not isinstance(value, dict) or not _node_allowed(value) or not _allows_text_type(value.get("type")):
        return []
    if "output_text" in value:
        chunks = _direct_text(value["output_text"])
        if chunks:
            return chunks
    chunks = _text_value(value.get("text"))
    if chunks:
        return chunks
    if isinstance(value.get("value"), str):
        return [value["value"]]
    if "content" in value:
        chunks = _content_text(value["content"])
        if chunks:
            return chunks
    for field in ("message", "delta", "assistant"):
        nested = value.get(field)
        if isinstance(nested, dict):
            chunks = _output_text(nested)
            if chunks:
                return chunks
    return []


def _direct_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            chunks.extend(_direct_text(item))
        return chunks
    return _output_text(value) if isinstance(value, dict) else []


def _first_nonempty_group(groups: Iterable[list[str]]) -> list[str]:
    return next((group for group in groups if any(chunk != "" for chunk in group)), [])


def _first_choice_text(choices: Any) -> list[str]:
    if not isinstance(choices, list):
        return []
    for choice in choices:
        chunks = _output_text(choice)
        if any(chunk != "" for chunk in chunks):
            return chunks
    return []


def _payload_text(payload: dict[str, Any], *, envelope: bool = False) -> list[str]:
    envelope_markers = frozenset({"reasoning", "tools", "user"}) if envelope else frozenset()
    if not _identity_allowed(payload) or _response_blocks_text(payload) or _marker_has_content(payload, envelope_markers):
        return []
    groups: list[list[str]] = []
    if "output_text" in payload:
        groups.append(_direct_text(payload["output_text"]))
    if "output" in payload:
        groups.append(_output_text(payload["output"]))
    if "choices" in payload:
        groups.append(_first_choice_text(payload["choices"]))
    if "content" in payload:
        groups.append(_content_text(payload["content"]))
    for field in ("text", "message", "delta"):
        if field in payload:
            value = payload[field]
            groups.append(_direct_text(value) if field == "text" else _output_text(value))
    for field in ("response", "result", "data"):
        nested = payload.get(field)
        groups.append(
            _payload_text(nested, envelope=True)
            if isinstance(nested, dict)
            else _output_text(nested)
            if isinstance(nested, list)
            else []
        )
    return _first_nonempty_group(groups)


def _extract_output_text(payload: dict[str, Any], *, request_id: str | None = None, http_status: int | None = None) -> str:
    if isinstance(payload, dict) and _response_blocks_text(payload):
        raise BridgeError(_response_diagnostic("Responses API returned no output text", payload, request_id=request_id, http_status=http_status))
    chunks = _payload_text(payload, envelope=True) if isinstance(payload, dict) else []
    if chunks:
        return "".join(chunks)
    raise BridgeError(_response_diagnostic("Responses API returned no output text", payload, request_id=request_id, http_status=http_status))


class Bridge:
    """Stateless implementation of the three Creative Model Bridge tools."""

    def __init__(self, config_path: str | Path | None = None, opener: Callable[..., Any] | None = None, timeout: float = 60.0, *, transport_diagnostics: bool = False) -> None:
        self.loader = ConfigLoader(config_path)
        self.opener = opener
        self.timeout = timeout
        self.transport_diagnostics = transport_diagnostics

    def _prepare(self, request: dict[str, Any]) -> tuple[ProviderConfig, str, dict[str, Any], dict[str, Any]]:
        if not isinstance(request, dict):
            raise BridgeError("tool arguments must be an object")
        provider = self.loader.load()
        task, explicit_model, blocks, file_paths, output_spec, max_tokens, temperature, system_mode = _request_values(request)
        model = explicit_model or provider.default_model
        if not model:
            raise ConfigError("CREATIVE_MODEL_DEFAULT is not configured and no model was requested")
        files = read_context_files(file_paths)
        constraints = _string_list(request.get("constraints"), "constraints")
        prompt = build_prompt(task, blocks, files, constraints, output_spec)
        if len(prompt) > MAX_TOTAL_CHARS:
            raise BridgeError("assembled user prompt exceeds the 180000-character limit")
        messages: list[dict[str, str]] = []
        if system_mode == "minimal":
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if temperature is not None:
            body["temperature"] = temperature
        section_order = ["task", "constraints", "output_spec", "context_text", "context_files"]
        system_prompt = SYSTEM_PROMPT if system_mode == "minimal" else None
        report = {
            "system_prompt": system_prompt,
            "system_mode": system_mode,
            "section_order": section_order,
            "user_chars": len(prompt),
            "total_chars": len(prompt) + (len(system_prompt) if system_prompt else 0),
            "truncated": False,
            "context_text": [{"label": block.label, "chars": len(block.text)} for block in blocks],
            "context_files": [item.report() for item in files],
        }
        return provider, model, body, report

    def creative_preview(self, request: dict[str, Any]) -> dict[str, Any]:
        provider, model, body, report = self._prepare(request)
        return {
            "text": "",
            "provider": provider.name,
            "model": model,
            "usage": None,
            "request_id": None,
            "prompt_report": report,
            "prompt": body["messages"][-1]["content"],
            "payload": body,
            "network": False,
        }

    def creative_generate(self, request: dict[str, Any]) -> dict[str, Any]:
        provider, model, body, report = self._prepare(request)
        client = self._client(provider, "responses")
        text, usage, request_id = client.chat_completions(body)
        return {
            "text": text,
            "provider": provider.name,
            "model": model,
            "usage": usage,
            "request_id": request_id,
            "prompt_report": report,
        }

    def creative_models(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if request not in (None, {}):
            raise BridgeError("creative_models takes no arguments")
        provider = self.loader.load()
        client = self._client(provider, "models")
        models, request_id, usage = client.models()
        return {
            "text": "",
            "provider": provider.name,
            "model": None,
            "usage": usage,
            "request_id": request_id,
            "prompt_report": None,
            "models": models,
        }

    def _client(self, provider: ProviderConfig, phase: TransportPhase) -> ResponsesClient:
        return ResponsesClient(provider, provider.credential(), self.opener, self.timeout, phase=phase, transport_diagnostics=self.transport_diagnostics, error_factory=BridgeError, failure_factory=_transport_error, response_diagnostic=_response_diagnostic, user_agent=USER_AGENT)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "creative_models":
            return self.creative_models(arguments)
        if name == "creative_preview":
            return self.creative_preview(arguments or {})
        if name == "creative_generate":
            return self.creative_generate(arguments or {})
        raise BridgeError(f"unknown tool: {name}")


if __name__ == "__main__":
    print("Import Bridge or run mcp/cli.py", file=sys.stderr)
