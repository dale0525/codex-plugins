from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))

import server  # noqa: E402


def _cp1252_stream(
    raw: bytes = b"", *, newline: str | None = "", errors: str = "backslashreplace"
) -> tuple[io.BytesIO, io.TextIOWrapper]:
    buffer = io.BytesIO(raw)
    return buffer, io.TextIOWrapper(buffer, encoding="cp1252", errors=errors, newline=newline)


class _FakeBridge:
    def call(self, name: str, arguments: dict[str, object]) -> dict[str, str]:
        return {"tool": name, "text": "中文响应"}


class _Diagnostic:
    def as_dict(self) -> dict[str, object]:
        return {
            "phase": "responses",
            "outer_type": "URLError",
            "reason_type": "OSError",
            "errno": 111,
            "ssl_verify_code": None,
            "ssl_reason": None,
        }


class _ErrorBridge:
    def __init__(self, *, transport_diagnostics: bool) -> None:
        self.transport_diagnostics = transport_diagnostics

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, str]:
        raise server.BridgeError(
            "provider request failed",
            transport_diagnostic=_Diagnostic(),
        )


class ServerStdioTests(unittest.TestCase):
    def test_cp1252_wrapper_is_red_before_boundary_reconfiguration(self) -> None:
        raw, stream = _cp1252_stream(errors="strict")
        with self.assertRaises(UnicodeEncodeError):
            stream.write("中文")
            stream.flush()
        stream.detach()
        self.assertEqual(raw.getvalue(), b"")

    def test_source_entry_reconfigures_hostile_stdio_and_emits_utf8_json(self) -> None:
        _stdin_raw, stdin = _cp1252_stream(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"demo","arguments":{}}}\n'
        )
        stdout_raw, stdout = _cp1252_stream()
        stderr_raw, stderr = _cp1252_stream()
        with patch.dict(os.environ, {}, clear=True):
            with (
                patch.object(server.sys, "stdin", stdin),
                patch.object(server.sys, "stdout", stdout),
                patch.object(server.sys, "stderr", stderr),
                patch.object(server.sys, "argv", ["server.py"]),
                patch.object(server, "Bridge", _FakeBridge),
            ):
                result = server.main()
                server.sys.stderr.write("错误\n")
                server.sys.stderr.flush()
        self.assertEqual(result, 0)
        self.assertEqual(stdin.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual(stdout.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual(stderr.encoding.lower().replace("-", ""), "utf8")
        self.assertEqual((stdout.newlines, stderr.newlines), (None, None))
        self.assertEqual((stdin.errors, stdout.errors, stderr.errors), ("backslashreplace",) * 3)
        expected = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": False,
                "structuredContent": {"tool": "demo", "text": "中文响应"},
                "content": [{"type": "text", "text": '{"tool":"demo","text":"中文响应"}'}],
            },
        }
        expected_wire = (json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.assertEqual(stdout_raw.getvalue(), expected_wire)
        self.assertEqual(stderr_raw.getvalue(), "错误\n".encode("utf-8"))
        stdin.detach()
        stdout.detach()
        stderr.detach()

    def test_frozen_provision_entry_configures_stdio_before_dispatch(self) -> None:
        streams = [_cp1252_stream()[1] for _ in range(3)]

        def provision(args: list[str]) -> int:
            self.assertEqual(args, ["status"])
            self.assertTrue(all(stream.encoding.lower().replace("-", "") == "utf8" for stream in streams))
            return 0

        with (
            patch.object(server.sys, "stdin", streams[0]),
            patch.object(server.sys, "stdout", streams[1]),
            patch.object(server.sys, "stderr", streams[2]),
            patch.object(server.sys, "argv", ["bridge.exe", "provision", "status"]),
            patch.object(server.sys, "frozen", True, create=True),
            patch.object(server, "provision_main", side_effect=provision) as provision_main,
        ):
            result = server.main()
        self.assertEqual(result, 0)
        provision_main.assert_called_once_with(["status"])
        for stream in streams:
            stream.detach()

    def test_unsupported_wrapper_fails_closed_without_consuming_input(self) -> None:
        class Unsupported:
            encoding = "cp1252"

            def __iter__(self):
                raise AssertionError("stdin must not be consumed")

        with (
            patch.object(server.sys, "stdin", Unsupported()),
            patch.object(server.sys, "stdout", Unsupported()),
            patch.object(server.sys, "stderr", Unsupported()),
        ):
            with self.assertRaises(RuntimeError):
                server._configure_stdio_utf8()

    def test_transport_diagnostics_flag_is_exact_opt_in_at_main_boundary(self) -> None:
        flag = "CREATIVE_MODEL_BRIDGE_TEST_TRANSPORT_DIAGNOSTICS"
        for value, expected in ((None, {}), ("1", {"transport_diagnostics": True}), ("0", {}), ("true", {})):
            captured: list[dict[str, object]] = []

            class CapturingBridge:
                def __init__(self, **kwargs: object) -> None:
                    captured.append(kwargs)

            environment = os.environ.copy()
            environment.pop(flag, None)
            if value is not None:
                environment[flag] = value
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(server, "_configure_stdio_utf8"),
                patch.object(server, "Bridge", CapturingBridge),
                patch.object(server.sys, "stdin", io.StringIO("")),
                patch.object(server.sys, "argv", ["server.py"]),
            ):
                self.assertEqual(server.main(), 0)
            self.assertEqual(captured, [expected])

    def test_transport_diagnostic_is_only_added_when_enabled_and_never_leaks_secret(self) -> None:
        request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "demo", "arguments": {}}}
        for enabled, expected in ((False, False), (True, True)):
            response = server.handle(request, _ErrorBridge(transport_diagnostics=enabled))
            result = response["result"]
            if expected:
                self.assertEqual(result["transport_diagnostic"]["reason_type"], "OSError")
            else:
                self.assertNotIn("transport_diagnostic", result)
            rendered = json.dumps(response, ensure_ascii=False)
            self.assertNotIn("api-secret-value", rendered)

    def test_noop_reconfigure_is_rejected_for_all_stdio_streams_before_input(self) -> None:
        class LyingWrapper:
            encoding = "cp1252"
            errors = "backslashreplace"

            def __init__(self) -> None:
                self.consumed = False

            def reconfigure(self, **kwargs: str) -> None:
                return None

            def __iter__(self):
                self.consumed = True
                return iter(())

            def write(self, text: str) -> int:
                return len(text)

            def flush(self) -> None:
                return None

        streams = [LyingWrapper() for _ in range(3)]
        with (
            patch.object(server.sys, "stdin", streams[0]),
            patch.object(server.sys, "stdout", streams[1]),
            patch.object(server.sys, "stderr", streams[2]),
            patch.object(server.sys, "argv", ["server.py"]),
        ):
            self.assertEqual(server.main(), 1)
        self.assertTrue(all(not stream.consumed for stream in streams))


if __name__ == "__main__":
    unittest.main()
