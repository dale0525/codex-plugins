"""Shared non-MCP transport primitives for the bundled CLI runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
import urllib.error
from typing import Literal


TransportPhase = Literal["models", "responses"]
_SSL_REASONS = frozenset(
    {
        "CERTIFICATE_VERIFY_FAILED",
        "HOSTNAME_MISMATCH",
        "SELF_SIGNED_CERTIFICATE",
        "UNABLE_TO_GET_ISSUER",
        "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    }
)
_SSL_REASON_BY_VERIFY_CODE = {
    18: "SELF_SIGNED_CERTIFICATE",
    20: "UNABLE_TO_GET_ISSUER",
    21: "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    62: "HOSTNAME_MISMATCH",
}


@dataclass(frozen=True)
class TransportDiagnostic:
    """Closed-shape, value-free metadata for opt-in local diagnostics."""

    phase: TransportPhase
    outer_type: str
    reason_type: str | None = None
    errno: int | None = None
    ssl_verify_code: int | None = None
    ssl_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "outer_type": self.outer_type,
            "reason_type": self.reason_type,
            "errno": self.errno,
            "ssl_verify_code": self.ssl_verify_code,
            "ssl_reason": self.ssl_reason,
        }


def _exception_type(value: BaseException) -> str:
    if isinstance(value, ssl.SSLCertVerificationError):
        return "SSLCertVerificationError"
    if isinstance(value, ssl.SSLError):
        return "SSLError"
    if isinstance(value, urllib.error.HTTPError):
        return "HTTPError"
    if isinstance(value, urllib.error.URLError):
        return "URLError"
    if isinstance(value, TimeoutError):
        return "TimeoutError"
    if isinstance(value, OSError):
        return "OSError"
    if isinstance(value, json.JSONDecodeError):
        return "JSONDecodeError"
    return "unknown"


def _exception_chain(value: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException] = [value]
    seen: set[int] = set()
    while pending and len(chain) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        reason = getattr(current, "reason", None)
        if isinstance(reason, BaseException):
            pending.append(reason)
        if isinstance(current.__cause__, BaseException):
            pending.append(current.__cause__)
        if isinstance(current.__context__, BaseException):
            pending.append(current.__context__)
    return chain


def diagnostic_for(error: BaseException, phase: TransportPhase) -> TransportDiagnostic:
    chain = _exception_chain(error)
    reason_type = next(
        (token for item in chain[1:] if (token := _exception_type(item)) != "unknown"),
        None,
    )
    error_number = next(
        (item.errno for item in chain if type(getattr(item, "errno", None)) is int),
        None,
    )
    ssl_error = next(
        (item for item in chain if isinstance(item, ssl.SSLCertVerificationError)),
        None,
    )
    verify_code = getattr(ssl_error, "verify_code", None) if ssl_error is not None else None
    verify_code = verify_code if type(verify_code) is int else None
    ssl_reason = None
    if ssl_error is not None:
        ssl_reason = _SSL_REASON_BY_VERIFY_CODE.get(verify_code, "CERTIFICATE_VERIFY_FAILED")
        if ssl_reason not in _SSL_REASONS:
            ssl_reason = "CERTIFICATE_VERIFY_FAILED"
    return TransportDiagnostic(
        phase=phase,
        outer_type=_exception_type(error),
        reason_type=reason_type,
        errno=error_number,
        ssl_verify_code=verify_code,
        ssl_reason=ssl_reason,
    )

