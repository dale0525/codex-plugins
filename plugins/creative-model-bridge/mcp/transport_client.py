"""Small standard-library HTTP transport for models and Chat Completions.

The bridge intentionally keeps the provider boundary here.  ``creative_generate``
uses one Chat Completions request and accepts either an SSE response (the normal
path) or one non-streaming JSON object when a provider does not honour the SSE
media type.  No provider response body is included in transport errors.
"""

from __future__ import annotations

import codecs
import json
from typing import Any, Callable, Iterator
import urllib.error
import urllib.request

try:
    from .transport_diagnostics import TransportPhase
except ImportError:
    from transport_diagnostics import TransportPhase


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_STREAM_CHUNK_SIZE = 4096


def _open_without_redirects(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _header(headers: Any, name: str) -> str | None:
    """Read one HTTP header from both email.Message and plain test mappings."""

    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        value = None
    if isinstance(value, str):
        return value
    try:
        items = headers.items()
    except Exception:
        return None
    for key, candidate in items:
        if isinstance(key, str) and key.lower() == name.lower() and isinstance(candidate, str):
            return candidate
    return None


def _read_chunk(response: Any, size: int = _STREAM_CHUNK_SIZE) -> bytes:
    """Read a chunk while supporting tiny response doubles with ``read()`` only."""

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
    raise TypeError("provider response read() did not return bytes")


def _nonempty(value: Any) -> bool:
    return value is not None and value is not False and (not isinstance(value, (str, list, dict)) or bool(value))


def _json_error(payload: Any) -> bool:
    return isinstance(payload, dict) and "error" in payload and _nonempty(payload.get("error"))


def _sse_lines(response: Any) -> Iterator[tuple[str | None, str]]:
    """Yield complete SSE events using an incremental UTF-8 decoder.

    Line endings may be LF or CRLF and a single event may contain multiple data
    lines.  Comments and unknown fields are ignored according to the SSE event
    stream format.  The generator stops at the first dispatched event; callers
    can therefore return immediately after ``[DONE]`` and ignore late frames.
    """

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    text_buffer = ""
    event_name: str | None = None
    data_lines: list[str] = []

    def dispatch() -> tuple[str | None, str] | None:
        nonlocal event_name, data_lines
        if not data_lines and event_name != "error":
            event_name, data_lines = None, []
            return None
        data = "\n".join(data_lines)
        event = (event_name, data)
        event_name, data_lines = None, []
        return event

    while True:
        try:
            chunk = _read_chunk(response)
            if chunk:
                text_buffer += decoder.decode(chunk, final=False)
            else:
                text_buffer += decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise ValueError("provider SSE stream contained malformed UTF-8") from error
        except (TypeError, OSError) as error:
            raise OSError("provider SSE stream could not be read") from error

        # Keep the final unterminated line in the buffer until the next chunk;
        # at EOF it is a valid line and must be parsed before dispatching.
        while True:
            line_end = -1
            terminator_length = 0
            for index, character in enumerate(text_buffer):
                if character == "\n":
                    line_end, terminator_length = index, 1
                    break
                if character == "\r":
                    # A CRLF may be split exactly between two network chunks.
                    # Keep the CR until the next read so it cannot dispatch an
                    # event before the LF arrives.
                    if index + 1 == len(text_buffer) and chunk:
                        break
                    line_end = index
                    terminator_length = 2 if index + 1 < len(text_buffer) and text_buffer[index + 1] == "\n" else 1
                    break
            if line_end < 0:
                break
            line = text_buffer[:line_end]
            text_buffer = text_buffer[line_end + terminator_length :]
            if line == "":
                event = dispatch()
                if event is not None:
                    yield event
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if not separator:
                # SSE field lines without a colon carry an empty value.  Only
                # data/event are relevant to this bridge.
                value = ""
            elif value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)

        if not chunk:
            if text_buffer:
                line = text_buffer
                text_buffer = ""
                if not line.startswith(":"):
                    field, separator, value = line.partition(":")
                    if not separator:
                        value = ""
                    elif value.startswith(" "):
                        value = value[1:]
                    if field == "event":
                        event_name = value
                    elif field == "data":
                        data_lines.append(value)
            event = dispatch()
            if event is not None:
                yield event
            return


def _parse_json_bytes(raw: bytes, error_factory: Callable[..., Exception], label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise error_factory(f"{label} returned malformed JSON") from error
    if not isinstance(parsed, dict):
        raise error_factory(f"{label} returned a malformed object")
    return parsed


class ResponsesClient:
    """Tiny standard-library HTTP client for ``/models`` and ``/chat/completions``.

    The historical class name is retained for embedders that imported it before
    the bridge moved from Responses to Chat Completions.
    """

    def __init__(self, provider: Any, credential: str, opener: Callable[..., Any] | None = None, timeout: float = 60.0, *, phase: TransportPhase = "responses", transport_diagnostics: bool = False, error_factory: Callable[..., Exception], failure_factory: Callable[..., Exception], response_diagnostic: Callable[..., str], user_agent: str) -> None:
        self.provider, self.credential = provider, credential
        self.opener, self.timeout = opener or _open_without_redirects, timeout
        self.phase, self.transport_diagnostics = phase, transport_diagnostics
        self.error_factory, self.failure_factory = error_factory, failure_factory
        self.response_diagnostic, self.user_agent = response_diagnostic, user_agent
        self.last_http_status: int | None = None

    def _failure(self, message: str, error: BaseException) -> Exception:
        return self.failure_factory(message, error, self.phase, self.transport_diagnostics)

    def _request(self, path: str, body: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
        url = f"{self.provider.base_url}/{path.lstrip('/')}"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.credential}", "Accept": "application/json", "User-Agent": self.user_agent}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        response: Any = None
        label = "Responses API"
        try:
            response = self.opener(request, timeout=self.timeout)
            http_status = getattr(response, "status", getattr(response, "code", None))
            self.last_http_status = http_status if isinstance(http_status, int) else None
            if isinstance(http_status, int) and 300 <= http_status < 400:
                raise self.error_factory(f"Responses API redirect refused (HTTP {http_status})")
            raw = response.read()
            header_request_id = _header(getattr(response, "headers", None), "x-request-id")
        except urllib.error.HTTPError as error:
            message = (
                f"{label} redirect refused (HTTP {error.code})" if 300 <= error.code < 400
                else f"{label} rejected the provider credential (401)" if error.code == 401
                else f"{label} rate limit reached (429); no retry was attempted" if error.code == 429
                else f"{label} request failed (HTTP {error.code})"
            )
            raise self._failure(message, error) from error
        except Exception as error:
            if isinstance(error, self.error_factory):
                raise
            if isinstance(error, (TimeoutError, urllib.error.URLError, OSError)):
                timed_out = isinstance(error, TimeoutError) or "timed out" in str(error).lower()
                message = f"{label} request timed out" if timed_out else f"{label} request could not be completed"
                raise self._failure(message, error) from error
            if not self.transport_diagnostics:
                raise
            raise self._failure(f"{label} request could not be completed", error) from error
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        parsed = _parse_json_bytes(raw, self.error_factory, label)
        request_id = parsed.get("id") if isinstance(parsed.get("id"), str) else header_request_id
        return parsed, request_id

    def models(self) -> tuple[list[str], str | None, dict[str, Any] | None]:
        payload, request_id = self._request("models")
        data = payload.get("data")
        if not isinstance(data, list):
            raise self.error_factory("/models returned a malformed model list")
        models: list[str] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise self.error_factory("/models returned a malformed model entry")
            models.append(item["id"])
        return models, request_id, payload.get("usage") if isinstance(payload.get("usage"), dict) else None

    def _chat_request(self, body: dict[str, Any]) -> Any:
        url = f"{self.provider.base_url}/chat/completions"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.credential}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        response: Any = None
        label = "Chat Completions API"
        try:
            response = self.opener(request, timeout=self.timeout)
            http_status = getattr(response, "status", getattr(response, "code", None))
            self.last_http_status = http_status if isinstance(http_status, int) else None
            if isinstance(http_status, int) and 300 <= http_status < 400:
                raise self.error_factory(f"{label} redirect refused (HTTP {http_status})")
            return response
        except urllib.error.HTTPError as error:
            message = (
                f"{label} redirect refused (HTTP {error.code})" if 300 <= error.code < 400
                else f"{label} rejected the provider credential (401)" if error.code == 401
                else f"{label} rate limit reached (429); no retry was attempted" if error.code == 429
                else f"{label} request failed (HTTP {error.code})"
            )
            raise self._failure(message, error) from error
        except Exception as error:
            if isinstance(error, self.error_factory):
                raise
            if isinstance(error, (TimeoutError, urllib.error.URLError, OSError)):
                timed_out = isinstance(error, TimeoutError) or "timed out" in str(error).lower()
                message = f"{label} request timed out" if timed_out else f"{label} request could not be completed"
                raise self._failure(message, error) from error
            if not self.transport_diagnostics:
                raise
            raise self._failure(f"{label} request could not be completed", error) from error

    def _stream_result(self, response: Any) -> tuple[str, dict[str, Any] | None, str | None]:
        text_chunks: list[str] = []
        usage: dict[str, Any] | None = None
        header_request_id = _header(getattr(response, "headers", None), "x-request-id")
        request_id: str | None = None
        saw_content = False
        saw_finish = False
        terminal = False
        try:
            for event_name, data in _sse_lines(response):
                if terminal:
                    continue
                if event_name == "error":
                    raise ValueError("provider SSE event reported an error")
                if data == "[DONE]":
                    terminal = True
                    if not saw_finish or not saw_content:
                        raise ValueError("provider SSE stream ended without completed text")
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError as error:
                    raise ValueError("provider SSE event contained malformed JSON") from error
                if not isinstance(payload, dict):
                    raise ValueError("provider SSE event was not an object")
                if _json_error(payload):
                    raise ValueError("provider SSE event reported an error")
                if request_id is None and isinstance(payload.get("id"), str):
                    request_id = payload["id"]
                if isinstance(payload.get("usage"), dict):
                    usage = payload["usage"]
                choices = payload.get("choices")
                if not isinstance(choices, list):
                    # Some providers send a metadata-only object.  It is safe
                    # to ignore it unless it is explicitly an error.
                    continue
                if not choices:
                    continue  # usage-only tail chunk
                target_choices: list[dict[str, Any]] = []
                if len(choices) == 1 and isinstance(choices[0], dict) and "index" not in choices[0]:
                    # A few compatible providers omit ``index`` for their one
                    # and only choice.  Never apply that shortcut to a multi-
                    # choice response, where an omitted index is ambiguous.
                    target_choices = [choices[0]]
                else:
                    for choice in choices:
                        if isinstance(choice, dict) and type(choice.get("index")) is int and choice["index"] == 0:
                            target_choices.append(choice)
                    if len(target_choices) > 1:
                        raise ValueError("provider SSE event contained duplicate choice index 0")
                if not target_choices:
                    continue
                target = target_choices[0]
                finish_reason = target.get("finish_reason")
                if finish_reason is not None:
                    if not isinstance(finish_reason, str):
                        raise ValueError("provider SSE event contained an invalid finish_reason")
                    saw_finish = True
                delta = target.get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        if content:
                            saw_content = True
                            text_chunks.append(content)
            if not terminal:
                # A normal EOF is valid only after a finish_reason and content;
                # this protects against truncated provider streams.
                if not saw_finish or not saw_content:
                    raise ValueError("provider SSE stream ended before completion")
            return "".join(text_chunks), usage, request_id if request_id is not None else header_request_id
        except self.error_factory:
            raise
        except Exception as error:
            raise self._failure("Chat Completions API returned an invalid SSE response", error) from error
        finally:
            if hasattr(response, "close"):
                response.close()

    def _json_result(self, response: Any) -> tuple[str, dict[str, Any] | None, str | None]:
        label = "Chat Completions API"
        try:
            raw = response.read()
            header_request_id = _header(getattr(response, "headers", None), "x-request-id")
            payload = _parse_json_bytes(raw, self.error_factory, label)
            request_id = payload.get("id") if isinstance(payload.get("id"), str) else header_request_id

            def invalid(prefix: str) -> Exception:
                return self.error_factory(
                    self.response_diagnostic(
                        prefix,
                        payload,
                        request_id=request_id,
                        http_status=self.last_http_status,
                    )
                )

            if _json_error(payload):
                raise invalid(f"{label} returned an error")
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise invalid(f"{label} returned no output text")
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise invalid(f"{label} returned no output text")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                raise invalid(f"{label} returned no output text")
            if any(_nonempty(message.get(key)) for key in ("tool_calls", "function_call", "reasoning_content", "reasoning", "refusal")) and not content:
                raise invalid(f"{label} returned no output text")
            return content, usage, request_id
        except self.error_factory:
            raise
        except Exception as error:
            raise self._failure(f"{label} returned an invalid JSON response", error) from error
        finally:
            if hasattr(response, "close"):
                response.close()

    def chat_completions(self, body: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        response = self._chat_request(body)
        content_type = (_header(getattr(response, "headers", None), "content-type") or "").lower()
        if content_type.startswith("text/event-stream"):
            return self._stream_result(response)
        return self._json_result(response)
