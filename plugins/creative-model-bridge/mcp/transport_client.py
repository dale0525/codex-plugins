"""Small HTTP transport separated from the bridge's prompt and extraction code."""

from __future__ import annotations

import json
from typing import Any, Callable
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


def _open_without_redirects(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class ResponsesClient:
    """Tiny standard-library HTTP client for `/models` and `/responses`."""

    def __init__(self, provider: Any, credential: str, opener: Callable[..., Any] | None = None, timeout: float = 60.0, *, phase: TransportPhase = "responses", transport_diagnostics: bool = False, error_factory: Callable[..., Exception], failure_factory: Callable[..., Exception], response_diagnostic: Callable[..., str], user_agent: str) -> None:
        self.provider, self.credential = provider, credential
        self.opener, self.timeout = opener or _open_without_redirects, timeout
        self.phase, self.transport_diagnostics = phase, transport_diagnostics
        self.error_factory, self.failure_factory = error_factory, failure_factory
        self.response_diagnostic, self.user_agent = response_diagnostic, user_agent
        self.last_http_status: int | None = None

    def _request(self, path: str, body: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
        url = f"{self.provider.base_url}/{path.lstrip('/')}"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.credential}", "Accept": "application/json", "User-Agent": self.user_agent}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
        response: Any = None
        try:
            response = self.opener(request, timeout=self.timeout)
            http_status = getattr(response, "status", getattr(response, "code", None))
            self.last_http_status = http_status if isinstance(http_status, int) else None
            if isinstance(http_status, int) and 300 <= http_status < 400:
                raise self.error_factory(f"Responses API redirect refused (HTTP {http_status})")
            raw = response.read()
            header_request_id = response.headers.get("x-request-id") if getattr(response, "headers", None) is not None else None
        except urllib.error.HTTPError as error:
            message = (f"Responses API redirect refused (HTTP {error.code})" if 300 <= error.code < 400 else "Responses API rejected the provider credential (401)" if error.code == 401 else "Responses API rate limit reached (429); no retry was attempted" if error.code == 429 else f"Responses API request failed (HTTP {error.code})")
            raise self._failure(message, error) from error
        except Exception as error:
            if isinstance(error, self.error_factory):
                raise
            if isinstance(error, (TimeoutError, urllib.error.URLError, OSError)):
                timed_out = isinstance(error, TimeoutError) or "timed out" in str(error).lower()
                message = "Responses API request timed out" if timed_out else "Responses API request could not be completed"
                raise self._failure(message, error) from error
            if not self.transport_diagnostics:
                raise
            raise self._failure("Responses API request could not be completed", error) from error
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise self._failure("Responses API returned malformed JSON", error) from error
        if not isinstance(parsed, dict):
            raise self.error_factory(self.response_diagnostic("Responses API returned a malformed object", parsed, request_id=header_request_id, http_status=http_status if isinstance(http_status, int) else None))
        request_id = parsed.get("id") if isinstance(parsed.get("id"), str) else header_request_id
        return parsed, request_id

    def _failure(self, message: str, error: BaseException) -> Exception:
        return self.failure_factory(message, error, self.phase, self.transport_diagnostics)

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

    def responses(self, body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        return self._request("responses", body)
