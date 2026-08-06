#!/usr/bin/env python3
"""One-shot stdin/stdout runtime for Creative Model Bridge.

The one-shot CLI is the only runtime surface. It announces readiness, accepts
one request envelope, and returns a bounded sequence of JSON-lines frames:
the process announces readiness, accepts one request envelope, and returns a
bounded sequence of JSON-lines frames.  The data frames contain a serialized
Bridge result, so callers can reassemble and validate the exact JSON bytes
without depending on shell output limits.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Iterable

# A copied plugin cache contains this file and its sibling modules.  Keeping
# the sibling directory first makes the executable independent of a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # direct script/frozen launch
    from bridge import Bridge, BridgeError  # type: ignore  # noqa: E402
except ImportError:  # package import in tests or embedding applications
    from .bridge import Bridge, BridgeError  # noqa: E402


PROTOCOL_VERSION = 1
MAX_CHUNK_BYTES = 4096
_MODE_NAMES = frozenset({"run", "cli", "exec"})


class _InputGateError(RuntimeError):
    """Input could not be made safe before the ready frame."""


class _InputGate:
    """Make stdin non-echoing for interactive sessions and pre-read pipes.

    The gate owns terminal state and signal handlers for exactly one request.
    All exits, including exceptions and signal-triggered interruptions, pass
    through ``__exit__`` so the caller's terminal mode is restored exactly.
    """

    def __init__(self) -> None:
        self.mode = "pipe"
        self.input_echo = False
        self._line: str | None = None
        self._fd: int | None = None
        self._saved_attrs: object | None = None
        self._restore_console: tuple[int, int] | None = None
        self._handlers: dict[int, Any] = {}
        self._restored = False
        self._entered = False

    @staticmethod
    def _fileno(stream: Any) -> int | None:
        try:
            fd = stream.fileno()
        except (AttributeError, OSError, ValueError):
            return None
        return fd if isinstance(fd, int) and fd >= 0 else None

    def _is_tty(self) -> bool:
        fd = self._fileno(sys.stdin)
        if fd is None:
            return False
        try:
            return bool(os.isatty(fd))
        except OSError:
            return False

    def _install_signal_handlers(self) -> None:
        # Signal APIs are only available in the main thread.  The CLI itself
        # runs there; tests may invoke it from a worker, where restoration is
        # still provided by the context manager without handler installation.
        if threading.current_thread() is not threading.main_thread():
            return
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                self._handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._signal_handler)
            except (OSError, RuntimeError, ValueError):
                # A handler is an additional safety net, not a reason to
                # reject a non-interactive invocation.
                self._handlers.pop(signum, None)

    def _restore_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            self._handlers.clear()
            return
        for signum, handler in self._handlers.items():
            try:
                signal.signal(signum, handler)
            except (OSError, RuntimeError, ValueError):
                pass
        self._handlers.clear()

    def _signal_handler(self, signum: int, _frame: Any) -> None:
        # Restore synchronously before propagating the conventional signal
        # outcome.  __exit__ repeats restoration idempotently.
        self.restore()
        if signum == getattr(signal, "SIGINT", object()):
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    def _prepare_posix(self, fd: int) -> None:
        import termios

        try:
            attrs = termios.tcgetattr(fd)
            changed = list(attrs)
            self._fd = fd
            self._saved_attrs = attrs
            # ICANON remains enabled so write_stdin sends one complete line;
            # only local echo (including newline echo) is disabled.
            changed[3] = (changed[3] | termios.ICANON) & ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(fd, termios.TCSANOW, changed)
        except (OSError, ValueError, AttributeError) as error:
            raise _InputGateError("interactive input echo control unavailable") from error

    def _prepare_windows(self, fd: int) -> None:
        try:
            import ctypes
            import msvcrt

            kernel32 = ctypes.windll.kernel32
            handle = msvcrt.get_osfhandle(fd)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                raise OSError("GetConsoleMode failed")
            old_mode = int(mode.value)
            self._fd = fd
            self._restore_console = (fd, old_mode)
            new_mode = (old_mode | 0x0002) & ~0x0004  # ENABLE_LINE_INPUT, no ECHO_INPUT
            if not kernel32.SetConsoleMode(handle, new_mode):
                raise OSError("SetConsoleMode failed")
        except (OSError, ValueError, AttributeError, ImportError) as error:
            raise _InputGateError("interactive input echo control unavailable") from error

    def _prepare_tty(self) -> None:
        fd = self._fileno(sys.stdin)
        if fd is None:
            raise _InputGateError("interactive stdin descriptor unavailable")
        self._install_signal_handlers()
        if os.name == "nt":
            self._prepare_windows(fd)
        else:
            self._prepare_posix(fd)
        self.mode = "tty"

    def _read_pipe_line(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except KeyboardInterrupt:
                raise
            except OSError as error:
                raise _InputGateError("stdin was unavailable before ready") from error
            if line == "":
                raise _InputGateError("stdin reached EOF before ready")
            if line.strip():
                self._line = line
                return

    def __enter__(self) -> "_InputGate":
        self._entered = True
        try:
            if self._is_tty():
                self._prepare_tty()
            else:
                self._read_pipe_line()
        except BaseException:
            self.restore()
            raise
        return self

    def consume_line(self) -> str | None:
        if self._line is not None:
            line, self._line = self._line, None
            return line
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            raise
        return line if line != "" else None

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        if self._saved_attrs is not None and self._fd is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSANOW, self._saved_attrs)
            except (OSError, ValueError, AttributeError):
                pass
        if self._restore_console is not None:
            fd, old_mode = self._restore_console
            try:
                import ctypes
                import msvcrt

                msvcrt_fd = msvcrt.get_osfhandle(fd)
                ctypes.windll.kernel32.SetConsoleMode(msvcrt_fd, old_mode)
            except (OSError, ValueError, AttributeError, ImportError):
                pass
        self._restore_signal_handlers()

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.restore()


def _is_utf8_encoding(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.lower().replace("-", "").replace("_", "") in {"utf8", "utf8sig"}


def _configure_stdio_utf8() -> None:
    """Use UTF-8 wrappers before reading or writing protocol frames."""

    failures: list[str] = []
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                errors = getattr(stream, "errors", None)
                kwargs: dict[str, str] = {"encoding": "utf-8"}
                if isinstance(errors, str):
                    kwargs["errors"] = errors
                reconfigure(**kwargs)
                if not _is_utf8_encoding(getattr(stream, "encoding", None)):
                    failures.append(f"{name}: reconfigure did not produce UTF-8")
            except (AttributeError, OSError, TypeError, ValueError):
                failures.append(f"{name}: reconfigure failed")
            continue
        if not _is_utf8_encoding(getattr(stream, "encoding", None)):
            failures.append(f"{name}: UTF-8 reconfigure unsupported")
    if failures:
        raise RuntimeError("UTF-8 stdio configuration unavailable (" + "; ".join(failures) + ")")


def _write_frame(frame: dict[str, Any]) -> None:
    """Write one compact NDJSON frame and flush immediately."""

    rendered = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(rendered + "\n")
    sys.stdout.flush()


def _safe_id(value: Any) -> Any:
    """Keep ordinary JSON-RPC-like IDs while avoiding object-shaped metadata."""

    if isinstance(value, float) and not math.isfinite(value):
        return "request-1"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return "request-1"


def _serialized_chunks(raw: bytes, limit: int = MAX_CHUNK_BYTES) -> list[str]:
    """Split UTF-8 JSON bytes on code-point boundaries into bounded chunks."""

    if not raw:
        return [""]
    chunks: list[str] = []
    offset = 0
    while offset < len(raw):
        end = min(offset + limit, len(raw))
        while end > offset:
            try:
                piece = raw[offset:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        else:  # JSON was encoded as UTF-8, so this is unreachable.
            raise ValueError("serialized bridge result is not valid UTF-8")
        chunks.append(piece)
        offset = end
    return chunks


def _emit_value(request_id: Any, value: Any, *, ok: bool, error_message: str | None = None) -> None:
    """Emit metadata followed by integrity-checked serialized result chunks."""

    body = value if ok else {"error": error_message or "creative model bridge failed"}
    try:
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        ok = False
        body = {"error": "creative model bridge returned an unserializable result"}
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    chunks = _serialized_chunks(raw)
    _write_frame(
        {
            "protocol": PROTOCOL_VERSION,
            "v": PROTOCOL_VERSION,
            "type": "response",
            "id": request_id,
            "ok": ok,
            "sha256": digest,
            "bytes": len(raw),
            "chunks": len(chunks),
        }
    )
    for sequence, chunk in enumerate(chunks):
        chunk_bytes = chunk.encode("utf-8")
        _write_frame(
            {
                "protocol": PROTOCOL_VERSION,
                "v": PROTOCOL_VERSION,
                "type": "data",
                "id": request_id,
                "seq": sequence,
                "data": chunk,
                "chunk_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
                "sha256": digest,
                "done": sequence == len(chunks) - 1,
            }
        )


def _emit_error(request_id: Any, message: str) -> None:
    # ``message`` originates from BridgeError's safe public surface.  The
    # generic fallback is intentionally constant so exception text/credentials
    # never reach stdout or stderr.
    _emit_value(request_id, None, ok=False, error_message=message)


def _strict_request_json(line: str) -> Any:
    """Parse one request without silently accepting duplicate keys/constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate request key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid request number {value}")

    return json.loads(line, object_pairs_hook=pairs, parse_constant=reject_constant)


def _request_parts(message: dict[str, Any]) -> tuple[Any, str, dict[str, Any]]:
    """Accept the v1 envelope and a small compatibility subset of direct JSON."""

    if type(message.get("protocol")) is not int or message.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("request protocol must be integer version 1")
    if message.get("type") != "request":
        raise ValueError("request type must be request")
    request: Any = message
    if message.get("type") == "request" and isinstance(message.get("request"), dict):
        request = message["request"]
    if isinstance(request, dict) and isinstance(request.get("request"), dict):
        request = request["request"]
    if not isinstance(request, dict):
        raise ValueError("request envelope must be an object")
    request_id = _safe_id(request.get("id", message.get("id", "request-1")))
    operation = request.get("operation", request.get("op", request.get("name")))
    if operation is None and isinstance(request.get("params"), dict):
        operation = request["params"].get("name")
        arguments = request["params"].get("arguments", {})
    else:
        arguments = request.get("arguments", request.get("args", {}))
    if not isinstance(operation, str) or not operation:
        raise ValueError("request operation is required")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("request arguments must be an object")
    return request_id, operation, arguments


def _configure_ssl_cert_file() -> None:
    """Validate an explicit CA override without requiring a provisioner."""

    selected = os.environ.get("CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE") or os.environ.get("SSL_CERT_FILE")
    if selected is None:
        return
    path = Path(selected).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.R_OK) or path.stat().st_size <= 0:
        raise RuntimeError("SSL_CERT_FILE must be an absolute readable non-empty regular file")
    os.environ["SSL_CERT_FILE"] = str(path)


def _cli_main() -> int:
    try:
        _configure_stdio_utf8()
    except RuntimeError as error:
        # Keep this ASCII and secret-free; no request has been consumed yet.
        try:
            sys.stderr.write(f"creative-model-bridge: {error}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 1

    try:
        with _InputGate() as gate:
            # The ready frame is emitted only after the input contract is
            # established.  A pipe has already been pre-read; a TTY has local
            # echo disabled while canonical line input remains enabled.
            _write_frame(
                {
                    "protocol": PROTOCOL_VERSION,
                    "v": PROTOCOL_VERSION,
                    "type": "ready",
                    "framing": "ndjson",
                    "encoding": "utf-8",
                    "max_chunk_bytes": MAX_CHUNK_BYTES,
                    "input_echo": gate.input_echo,
                    "input_mode": gate.mode,
                }
            )
            raw_line = gate.consume_line()
            if raw_line is None:
                return 0
            line = raw_line.strip()
            if not line:
                # TTY input is canonical, so an empty line can be entered
                # interactively.  Treat it as the same safe invalid envelope
                # response as a pre-read pipe line would be.
                _emit_error("request-1", "invalid request envelope")
                return 0
            try:
                message = _strict_request_json(line)
                if not isinstance(message, dict):
                    raise ValueError("request envelope must be an object")
                request_id, operation, arguments = _request_parts(message)
            except (json.JSONDecodeError, ValueError, TypeError):
                _emit_error("request-1", "invalid request envelope")
                return 0
            try:
                _configure_ssl_cert_file()
                bridge = Bridge()
                value = bridge.call(operation, arguments)
            except BridgeError as error:
                _emit_error(request_id, str(error))
                return 0
            except Exception:
                _emit_error(request_id, "creative model bridge failed")
                return 0
            _emit_value(request_id, value, ok=True)
            return 0
    except _InputGateError as error:
        try:
            sys.stderr.write(f"creative-model-bridge: {error}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 1
    except KeyboardInterrupt:
        return 130


def main(argv: Iterable[str] | None = None) -> int:
    """Dispatch migration or the new one-shot protocol."""

    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args and not args[0].startswith("-") else None
    if mode == "migrate":
        try:
            from migrate import main as migrate_main  # type: ignore
        except ImportError:  # pragma: no cover - package import
            from .migrate import main as migrate_main
        return migrate_main(args[1:])
    if mode in _MODE_NAMES:
        return _cli_main()
    # No mode is the one-shot route as well. A cache launcher supplies `run`;
    # direct embedding may omit it.
    return _cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
