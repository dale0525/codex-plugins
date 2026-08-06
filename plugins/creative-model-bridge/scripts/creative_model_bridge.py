#!/usr/bin/env python3
"""One-shot Creative Model Bridge.

The process accepts one JSON request on stdin, performs one OpenAI-compatible
Chat Completions request, and writes one JSON result on stdout.  It deliberately
uses only the Python standard library so the script is easy to audit and runs
inside the plugin's Pixi environment without a launcher or a resident service.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Iterator


VERSION = "0.2.0"
SYSTEM_PROMPT = "你是创意文字写作者。严格依据用户提供的任务与材料创作；只输出成稿，不解释过程。"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_CHARS = 180_000
DEFAULT_MAX_TOKENS = 60_000
REQUEST_FIELDS = frozenset(
    {
        "task",
        "model",
        "system_mode",
        "context_text",
        "context_files",
        "constraints",
        "output_spec",
        "temperature",
        "max_tokens",
        "max_output_tokens",
    }
)
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
_COMMON_HANGUL = set("한국어이다은는을를에하하고")


class BridgeError(Exception):
    """Safe error suitable for the JSON result and stderr."""


class ConfigError(BridgeError):
    pass


class FileContextError(BridgeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    default_model: str | None
    env_key: str | None
    experimental_bearer_token: str | None

    def credential(self) -> str:
        if self.env_key:
            configured = os.environ.get(self.env_key)
            if configured:
                return configured
            fixed = os.environ.get("CREATIVE_MODEL_API_KEY")
            if fixed:
                return fixed
            raise ConfigError("provider credential is not available in the configured environment")
        fixed = os.environ.get("CREATIVE_MODEL_API_KEY")
        if fixed:
            return fixed
        if self.experimental_bearer_token:
            return self.experimental_bearer_token
        raise ConfigError("provider credential is not configured")


@dataclass(frozen=True)
class ContextFile:
    path: str
    text: str
    encoding: str
    chars: int


def _config_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _base_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("configured creative model provider has no base_url")
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("provider base_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ConfigError("provider base_url may not contain credentials, query, or fragment")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("provider base_url contains invalid control characters")
    return value


def load_provider(config_path: str | Path | None = None) -> Provider:
    """Load the selected provider from the existing Codex config shape."""

    path = _config_path(config_path)
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError("Codex config.toml was not found") from error
    except OSError as error:
        raise ConfigError("Codex config.toml could not be read") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("Codex config.toml is not valid TOML") from error

    shell = config.get("shell_environment_policy")
    values = shell.get("set") if isinstance(shell, dict) else None
    provider_name = values.get("CREATIVE_MODEL_PROVIDER") if isinstance(values, dict) else None
    default_model = values.get("CREATIVE_MODEL_DEFAULT") if isinstance(values, dict) else None
    if not isinstance(provider_name, str) or not provider_name:
        raise ConfigError("CREATIVE_MODEL_PROVIDER is not configured")
    if default_model is not None and (not isinstance(default_model, str) or not default_model):
        raise ConfigError("CREATIVE_MODEL_DEFAULT must be a non-empty string")

    providers = config.get("model_providers")
    selected = providers.get(provider_name) if isinstance(providers, dict) else None
    if not isinstance(selected, dict):
        raise ConfigError("configured creative model provider was not found")
    wire_api = selected.get("wire_api", "chat_completions")
    if wire_api not in {"responses", "chat_completions"}:
        raise ConfigError("creative model provider must use wire_api = responses or chat_completions")
    env_key = selected.get("env_key")
    if env_key is not None and (not isinstance(env_key, str) or not env_key):
        raise ConfigError("provider env_key must be a non-empty string")
    bearer = selected.get("experimental_bearer_token")
    if bearer is not None and (not isinstance(bearer, str) or not bearer):
        raise ConfigError("provider experimental_bearer_token must be a non-empty string")
    return Provider(
        name=provider_name,
        base_url=_base_url(selected.get("base_url")),
        default_model=default_model,
        env_key=env_key,
        experimental_bearer_token=bearer,
    )


def _looks_like_text(text: str) -> bool:
    if "\x00" in text or "\ufffd" in text:
        return False
    allowed = {"\n", "\r", "\t", "\f", "\b"}
    return all(unicodedata.category(char) not in {"Cc", "Cs"} or char in allowed for char in text)


def _looks_like_binary(raw: bytes) -> bool:
    if any(raw.startswith(magic) for magic in _BINARY_MAGICS):
        return True
    if len(raw) >= 32 and sum(byte >= 0x80 for byte in raw) / len(raw) > 0.25 and len(set(raw)) <= 4:
        return True
    return False


def _legacy_confident(raw: bytes, text: str) -> bool:
    if not text or not _looks_like_text(text):
        return False
    non_ascii = sum(ord(char) > 127 for char in text)
    if non_ascii < 2 or (len(raw) >= 32 and len(set(raw)) <= 4):
        return False
    printable = sum(char.isprintable() or char in "\n\r\t\f\b" for char in text)
    return printable / len(text) >= 0.95


def _legacy_score(text: str) -> int:
    hangul = sum("\uac00" <= char <= "\ud7a3" for char in text)
    kana = sum("\u3040" <= char <= "\u30ff" for char in text)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    common = sum(char in _COMMON_CJK for char in text)
    length = max(len(text), 1)
    score = 0
    hangul_common = sum(char in _COMMON_HANGUL for char in text)
    if hangul >= 2 and hangul / length >= 0.8:
        # An East Asian decoder can turn Chinese bytes into arbitrary Hangul
        # syllables.  Require a small Korean lexical signal before rewarding
        # that candidate; real Korean samples such as ``한국어`` pass it.
        score += hangul * 100 if hangul_common >= 2 else -hangul * 100
    if kana / length >= 0.35:
        score += kana * 100
        if common == 0 and cjk and kana < 3:
            score -= kana * 100
    return score + common * 20 + cjk * 2


def decode_text(raw: bytes, path: Path) -> tuple[str, str]:
    """Decode UTF-8/BOM text and a small, deterministic legacy set."""

    if any(raw.startswith(magic) for magic in _BINARY_MAGICS):
        raise FileContextError(f"context file has a binary signature: {path}")
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise FileContextError(f"context file is not valid UTF-8: {path}") from error
        if not _looks_like_text(text):
            raise FileContextError(f"context file contains binary control characters: {path}")
        return text, "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError as error:
            raise FileContextError(f"context file is not valid UTF-16: {path}") from error
        if not _looks_like_text(text):
            raise FileContextError(f"context file contains binary control characters: {path}")
        return text, "utf-16"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        if not _looks_like_text(text):
            raise FileContextError(f"context file contains Unicode control characters: {path}")
        return text, "utf-8"
    if _looks_like_binary(raw):
        raise FileContextError(f"context file is not recognized as regular text: {path}")

    candidates: list[tuple[int, int, str, str]] = []
    for rank, encoding in enumerate(("gb18030", "big5", "shift_jis", "euc_jp", "euc_kr")):
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _legacy_confident(raw, candidate):
            candidates.append((_legacy_score(candidate), rank, encoding, candidate))
    if candidates:
        candidates.sort(key=lambda item: (-item[0], item[1]))
        _, _, encoding, text = candidates[0]
        return text, encoding
    raise FileContextError(f"context file is not recognized as regular text: {path}")


def read_context_files(paths: Any) -> list[ContextFile]:
    if paths is None:
        return []
    if not isinstance(paths, list):
        raise FileContextError("context_files must be an ordered array of absolute paths")
    files: list[ContextFile] = []
    total_chars = 0
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path or not os.path.isabs(raw_path):
            raise FileContextError("each context file path must be a non-empty absolute string")
        if any(ord(char) < 32 or ord(char) == 127 for char in raw_path):
            raise FileContextError("context file paths may not contain control characters")
        if _GLOB_MARKERS.search(raw_path):
            raise FileContextError("context file paths may not contain glob patterns")
        candidate = Path(raw_path)
        try:
            initial = os.lstat(candidate)
        except OSError as error:
            raise FileContextError("context file could not be opened safely") from error
        if stat.S_ISLNK(initial.st_mode):
            raise FileContextError("context file symlinks are not allowed")
        if not stat.S_ISREG(initial.st_mode):
            raise FileContextError(f"context path is not a regular file: {candidate}")
        if initial.st_size > MAX_FILE_BYTES:
            raise FileContextError(f"context file exceeds the 2 MiB limit: {candidate}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(candidate, flags)
            actual = os.fstat(descriptor)
            if not stat.S_ISREG(actual.st_mode) or actual.st_size > MAX_FILE_BYTES:
                raise FileContextError("context file changed or exceeds the 2 MiB limit")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = None
                raw = handle.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            raise FileContextError("context file could not be opened safely") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > MAX_FILE_BYTES:
            raise FileContextError("context file exceeds the 2 MiB limit")
        resolved = candidate.resolve(strict=False)
        text, encoding = decode_text(raw, resolved)
        total_chars += len(text)
        if total_chars > MAX_TOTAL_CHARS:
            raise FileContextError("decoded context exceeds the 180000-character total limit")
        files.append(
            ContextFile(
                path=str(resolved),
                text=text,
                encoding=encoding,
                chars=len(text),
            )
        )
    return files


def _context_blocks(value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BridgeError("context_text must be an ordered array")
    blocks: list[tuple[str, str]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            blocks.append((f"context-{index}", item))
        elif isinstance(item, dict) and set(item) == {"label", "text"} and isinstance(item.get("label"), str) and isinstance(item.get("text"), str) and item["label"]:
            blocks.append((item["label"], item["text"]))
        else:
            raise BridgeError("context_text items must be strings or {label, text} objects")
    return blocks


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise BridgeError(f"{field} must be a string or ordered array of strings")


def _output_spec(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raise BridgeError("output_spec must be a string, object, array, or null")


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BridgeError(f"{field} must be a finite number")
    return float(value)


def build_prompt(request: dict[str, Any], files: list[ContextFile]) -> str:
    task = request.get("task")
    if not isinstance(task, str) or not task:
        raise BridgeError("task is required and must be a non-empty string")
    sections = [f"任务:\n{task}"]
    constraints = _string_list(request.get("constraints"), "constraints")
    if constraints:
        sections.append("约束:\n" + "\n".join(f"- {item}" for item in constraints))
    output_spec = _output_spec(request.get("output_spec"))
    if output_spec is not None:
        sections.append(f"输出规格:\n{output_spec}")
    blocks = _context_blocks(request.get("context_text"))
    if blocks:
        sections.append("上下文文字:\n" + "\n\n".join(f"[{label}]\n{text}" for label, text in blocks))
    if files:
        rendered = []
        for item in files:
            rendered.append(f"--- BEGIN FILE: {item.path} ---\n{item.text}\n--- END FILE: {item.path} ---")
        sections.append("上下文文件:\n" + "\n\n".join(rendered))
    prompt = "\n\n".join(sections)
    if len(prompt) > MAX_TOTAL_CHARS:
        raise BridgeError("assembled user prompt exceeds the 180000-character limit")
    return prompt


def build_payload(request: dict[str, Any], provider: Provider, files: list[ContextFile] | None = None) -> tuple[dict[str, Any], str, list[ContextFile]]:
    if not isinstance(request, dict):
        raise BridgeError("stdin request must be a JSON object")
    unknown = set(request) - REQUEST_FIELDS
    if unknown:
        raise BridgeError(f"unsupported request field(s): {sorted(unknown)}")
    model = request.get("model", provider.default_model)
    if not isinstance(model, str) or not model:
        raise ConfigError("CREATIVE_MODEL_DEFAULT is not configured and no model was requested")
    system_mode = request.get("system_mode", "minimal")
    if system_mode not in {"minimal", "none"}:
        raise BridgeError("system_mode must be minimal or none")
    if files is None:
        files = read_context_files(request.get("context_files"))
    prompt = build_prompt(request, files)
    max_tokens_value = request.get("max_tokens", request.get("max_output_tokens", DEFAULT_MAX_TOKENS))
    if "max_tokens" in request and "max_output_tokens" in request:
        raise BridgeError("provide only one of max_tokens and max_output_tokens")
    if isinstance(max_tokens_value, bool) or not isinstance(max_tokens_value, int) or max_tokens_value <= 0:
        raise BridgeError("max_tokens must be a positive integer")
    temperature = request.get("temperature")
    if temperature is not None:
        temperature = _number(temperature, "temperature")
        if not 0 <= temperature <= 2:
            raise BridgeError("temperature must be between 0 and 2")
    messages: list[dict[str, str]] = []
    if system_mode == "minimal":
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens_value,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    return payload, model, files


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        value = None
    if isinstance(value, str):
        return value
    try:
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == name.lower() and isinstance(candidate, str):
                return candidate
    except Exception:
        pass
    return None


def _read_chunk(response: Any, size: int = 8192) -> bytes:
    try:
        raw = response.read(size)
    except TypeError:
        raw = response.read()
    if raw is None:
        return b""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    raise BridgeError("provider stream could not be read")


def _sse_events(response: Any) -> Iterator[tuple[str | None, str]]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    buffer = ""
    event_name: str | None = None
    data_lines: list[str] = []

    def dispatch() -> tuple[str | None, str] | None:
        nonlocal event_name, data_lines
        if not data_lines and event_name != "error":
            event_name, data_lines = None, []
            return None
        event = (event_name, "\n".join(data_lines))
        event_name, data_lines = None, []
        return event

    def consume(line: str) -> tuple[str | None, str] | None:
        nonlocal event_name, data_lines
        if line == "":
            return dispatch()
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value if separator else "")
        return None

    while True:
        chunk = _read_chunk(response)
        try:
            buffer += decoder.decode(chunk, final=not chunk)
        except UnicodeDecodeError as error:
            raise BridgeError("provider SSE stream contained malformed UTF-8") from error
        while True:
            match = re.search(r"\r\n|\n|\r", buffer)
            if not match:
                break
            # A CRLF can be split at the network chunk boundary.  Keep a
            # trailing CR until the next read so the following LF cannot be
            # mistaken for a blank event delimiter and dispatch data lines
            # before the complete SSE event has arrived.
            if match.group() == "\r" and match.end() == len(buffer) and chunk:
                break
            line = buffer[: match.start()]
            buffer = buffer[match.end() :]
            event = consume(line)
            if event is not None:
                yield event
        if not chunk:
            if buffer:
                event = consume(buffer)
                if event is not None:
                    yield event
                buffer = ""
            event = dispatch()
            if event is not None:
                yield event
            return


def parse_stream(response: Any) -> dict[str, Any]:
    reasoning: list[str] = []
    output: list[str] = []
    usage: dict[str, Any] | None = None
    request_id = _header(getattr(response, "headers", None), "x-request-id")
    response_model: str | None = None
    saw_finish = False
    saw_payload = False
    done = False
    try:
        for event_name, data in _sse_events(response):
            if event_name == "error":
                raise BridgeError("provider stream reported an error")
            if data == "[DONE]":
                done = True
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as error:
                raise BridgeError("provider SSE event contained malformed JSON") from error
            if not isinstance(payload, dict):
                raise BridgeError("provider SSE event was not an object")
            if payload.get("error"):
                raise BridgeError("provider stream reported an error")
            saw_payload = True
            if isinstance(payload.get("id"), str) and request_id is None:
                request_id = payload["id"]
            if isinstance(payload.get("model"), str):
                response_model = payload["model"]
            if isinstance(payload.get("usage"), dict):
                usage = payload["usage"]
            choices = payload.get("choices")
            if choices is None:
                continue
            if not isinstance(choices, list):
                raise BridgeError("provider SSE event contained malformed choices")
            if not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                raise BridgeError("provider SSE event contained malformed choice")
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None:
                if not isinstance(finish_reason, str):
                    raise BridgeError("provider SSE event contained invalid finish_reason")
                saw_finish = True
            delta = choice.get("delta")
            if delta is None:
                continue
            if not isinstance(delta, dict):
                raise BridgeError("provider SSE event contained malformed delta")
            for key, target in (("reasoning_content", reasoning), ("reasoning", reasoning), ("content", output)):
                if key not in delta:
                    continue
                value = delta[key]
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise BridgeError("provider SSE event contained non-text delta content")
                target.append(value)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if not done or not saw_payload or not saw_finish:
        raise BridgeError("provider SSE stream ended before completion")
    return {
        "reasoning": "".join(reasoning),
        "output": "".join(output),
        "usage": usage,
        "request_id": request_id,
        "response_model": response_model,
    }


def generate(request: dict[str, Any], *, config_path: str | Path | None = None, opener: Callable[..., Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    provider = load_provider(config_path)
    payload, model, files = build_payload(request, provider)
    credential = provider.credential()
    url = provider.base_url + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    http_request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {credential}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": f"creative-model-bridge/{VERSION}",
        },
        method="POST",
    )
    open_fn = opener or _open
    response: Any = None
    try:
        response = open_fn(http_request, timeout)
        status = getattr(response, "status", getattr(response, "code", None))
        if isinstance(status, int) and status >= 300:
            raise BridgeError(f"Chat Completions API request failed (HTTP {status})")
        result = parse_stream(response)
    except BridgeError:
        raise
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            message = f"Chat Completions API redirect refused (HTTP {error.code})"
        elif error.code == 401:
            message = "Chat Completions API rejected the provider credential (401)"
        elif error.code == 429:
            message = "Chat Completions API rate limit reached (429); no retry was attempted"
        else:
            message = f"Chat Completions API request failed (HTTP {error.code})"
        raise BridgeError(message) from error
    except TimeoutError as error:
        raise BridgeError("Chat Completions API request timed out") from error
    except (urllib.error.URLError, OSError) as error:
        raise BridgeError("Chat Completions API request could not be completed") from error
    except Exception as error:
        raise BridgeError("Chat Completions API request could not be completed") from error
    finally:
        if response is not None and not getattr(response, "_cmb_closed", False):
            # parse_stream closes normal streams; close failed/short responses.
            close = getattr(response, "close", None)
            if callable(close):
                close()
    result.update({"model": model, "provider": provider.name})
    result["context_files"] = [
        {"path": item.path, "chars": item.chars, "encoding": item.encoding}
        for item in files
    ]
    return result


def _stdin_text() -> str:
    try:
        stream = getattr(sys.stdin, "buffer")
    except AttributeError:
        stream = sys.stdin
    raw = stream.read()
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BridgeError("stdin request is not valid UTF-8 JSON") from error
    return raw


def _write_result(value: dict[str, Any]) -> None:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        encoded = rendered.encode("utf-8")
    except UnicodeEncodeError:
        # A provider could return an escaped lone surrogate.  Escape that rare
        # value rather than allowing output encoding to abort the one-object
        # protocol.
        rendered = json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"
        encoded = rendered.encode("ascii")
    binary = getattr(sys.stdout, "buffer", None)
    if binary is not None:
        binary.write(encoded)
        binary.flush()
        return
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="strict")
        except (OSError, ValueError):
            pass
    sys.stdout.write(rendered)
    sys.stdout.flush()


def main() -> int:
    try:
        raw = _stdin_text()
        if not raw.strip():
            raise BridgeError("stdin request is empty")
        try:
            request = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BridgeError("stdin request is not valid JSON") from error
        result = generate(request)
        exit_code = 0
    except BridgeError as error:
        result = {"reasoning": "", "output": "", "error": str(error)}
        exit_code = 1
    except Exception:
        result = {"reasoning": "", "output": "", "error": "creative model bridge failed"}
        exit_code = 1
    _write_result(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
