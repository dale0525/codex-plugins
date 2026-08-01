"""Provider-neutral bridge for OpenAI Responses-compatible creative models.

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


SYSTEM_PROMPT = "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CHARS = 180_000
DEFAULT_MAX_OUTPUT_TOKENS = 8192
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
            "items": {"type": "string", "minLength": 1, "pattern": "^/"},
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


class ConfigError(BridgeError):
    """The local Codex provider configuration is missing or invalid."""


class FileContextError(BridgeError):
    """A context file is not an acceptable regular text file."""


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
        if not codex_home:
            raise ConfigError("CODEX_HOME is not set")
        return Path(codex_home) / "config.toml"

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
        if wire_api != "responses":
            raise ConfigError("creative model provider must use wire_api = responses")
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _open_without_redirects(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class ResponsesClient:
    """Tiny standard-library HTTP client for `/models` and `/responses`."""

    def __init__(self, provider: ProviderConfig, credential: str, opener: Callable[..., Any] | None = None, timeout: float = 60.0) -> None:
        self.provider = provider
        self.credential = credential
        self.opener = opener or _open_without_redirects
        self.timeout = timeout

    def _request(self, path: str, body: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
        url = f"{self.provider.base_url}/{path.lstrip('/')}"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.credential}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        response: Any = None
        try:
            response = self.opener(request, timeout=self.timeout)
            response_status = getattr(response, "status", getattr(response, "code", None))
            if isinstance(response_status, int) and 300 <= response_status < 400:
                raise BridgeError(f"Responses API redirect refused (HTTP {response_status})")
            raw = response.read()
            header_request_id = response.headers.get("x-request-id") if getattr(response, "headers", None) is not None else None
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise BridgeError(f"Responses API redirect refused (HTTP {error.code})") from error
            if error.code == 401:
                raise BridgeError("Responses API rejected the provider credential (401)") from error
            if error.code == 429:
                raise BridgeError("Responses API rate limit reached (429); no retry was attempted") from error
            raise BridgeError(f"Responses API request failed (HTTP {error.code})") from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            if isinstance(error, TimeoutError) or "timed out" in str(error).lower():
                raise BridgeError("Responses API request timed out") from error
            raise BridgeError("Responses API request could not be completed") from error
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError("Responses API returned malformed JSON") from error
        if not isinstance(parsed, dict):
            raise BridgeError("Responses API returned a malformed object")
        request_id = parsed.get("id") if isinstance(parsed.get("id"), str) else header_request_id
        return parsed, request_id

    def models(self) -> tuple[list[str], str | None, dict[str, Any] | None]:
        payload, request_id = self._request("models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise BridgeError("/models returned a malformed model list")
        models: list[str] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise BridgeError("/models returned a malformed model entry")
            models.append(item["id"])
        return models, request_id, payload.get("usage") if isinstance(payload.get("usage"), dict) else None

    def responses(self, body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        return self._request("responses", body)


def _extract_output_text(payload: dict[str, Any]) -> str:
    top_level = payload.get("output_text")
    if isinstance(top_level, str):
        return top_level
    output = payload.get("output")
    if not isinstance(output, list):
        raise BridgeError("Responses API returned no output text")
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        elif isinstance(item.get("text"), str) and item.get("type") in {"output_text", "text"}:
            chunks.append(item["text"])
    if not chunks:
        raise BridgeError("Responses API returned no output text")
    return "".join(chunks)


class Bridge:
    """Stateless implementation of the three Creative Model Bridge tools."""

    def __init__(self, config_path: str | Path | None = None, opener: Callable[..., Any] | None = None, timeout: float = 60.0) -> None:
        self.loader = ConfigLoader(config_path)
        self.opener = opener
        self.timeout = timeout

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
        body: dict[str, Any] = {"model": model, "input": prompt, "max_output_tokens": max_tokens}
        if system_mode == "minimal":
            body["instructions"] = SYSTEM_PROMPT
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
            "prompt": body["input"],
            "payload": body,
            "network": False,
        }

    def creative_generate(self, request: dict[str, Any]) -> dict[str, Any]:
        provider, model, body, report = self._prepare(request)
        client = ResponsesClient(provider, provider.credential(), self.opener, self.timeout)
        payload, request_id = client.responses(body)
        return {
            "text": _extract_output_text(payload),
            "provider": provider.name,
            "model": model,
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            "request_id": request_id,
            "prompt_report": report,
        }

    def creative_models(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        if request not in (None, {}):
            raise BridgeError("creative_models takes no arguments")
        provider = self.loader.load()
        client = ResponsesClient(provider, provider.credential(), self.opener, self.timeout)
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

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "creative_models":
            return self.creative_models(arguments)
        if name == "creative_preview":
            return self.creative_preview(arguments or {})
        if name == "creative_generate":
            return self.creative_generate(arguments or {})
        raise BridgeError(f"unknown tool: {name}")


if __name__ == "__main__":
    print("Import Bridge or run mcp/server.py", file=sys.stderr)
