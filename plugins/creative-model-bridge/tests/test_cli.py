from __future__ import annotations

import io
import json
import os
import pty
from pathlib import Path
import signal
import subprocess
import termios
import tempfile
import types
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))
import cli  # noqa: E402
from bridge import BridgeError  # noqa: E402


class _FakeBridge:
    def __init__(self, value: dict[str, object] | None = None, delay: float = 0.0) -> None:
        self.value = value or {"text": "成稿", "provider": "p", "model": "m"}
        self.delay = delay

    def call(self, operation: str, arguments: dict[str, object]) -> dict[str, object]:
        if operation == "creative_generate" and self.delay:
            import time

            time.sleep(self.delay)
        return self.value


def _run_in_process(request: dict[str, object], bridge: object) -> tuple[int, list[dict[str, object]], str]:
    stdin = io.StringIO(json.dumps(request, ensure_ascii=False) + "\n")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(cli, "Bridge", return_value=bridge),
        patch.object(cli, "_configure_stdio_utf8"),
        patch.object(cli, "_configure_ssl_cert_file"),
        patch.object(cli.sys, "stdin", stdin),
        patch.object(cli.sys, "stdout", stdout),
        patch.object(cli.sys, "stderr", stderr),
    ):
        code = cli.main(["run"])
    frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    return code, frames, stderr.getvalue()


class CliProtocolTests(unittest.TestCase):
    def test_ready_and_chunked_large_result_round_trip_without_truncation(self) -> None:
        value = {"text": "x" * 70_000, "provider": "p", "model": "m"}
        code, frames, _ = _run_in_process(
            {"protocol": 1, "type": "request", "id": "large", "operation": "creative_generate", "arguments": {"task": "t"}},
            _FakeBridge(value),
        )
        self.assertEqual(code, 0)
        self.assertEqual(frames[0]["type"], "ready")
        metadata = frames[1]
        chunks = [frame for frame in frames[2:] if frame["type"] == "data"]
        self.assertGreater(len(chunks), 10)
        serialized = "".join(str(frame["data"]) for frame in chunks)
        self.assertEqual(len(serialized.encode("utf-8")), metadata["bytes"])
        self.assertEqual(json.loads(serialized), value)
        self.assertEqual(chunks[-1]["done"], True)
        self.assertEqual([chunk["seq"] for chunk in chunks], list(range(len(chunks))))

    def test_safe_error_does_not_echo_request_or_exception_secret(self) -> None:
        secret = "provider-secret-should-not-appear"

        class ErrorBridge:
            def call(self, operation: str, arguments: dict[str, object]) -> dict[str, object]:
                raise BridgeError("safe bridge error")

        code, frames, stderr = _run_in_process(
            {"protocol": 1, "type": "request", "id": "err", "operation": "creative_generate", "arguments": {"task": secret}},
            ErrorBridge(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(frames[1]["ok"], False)
        rendered = json.dumps(frames, ensure_ascii=False)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, stderr)

    def test_operation_can_run_past_normal_exec_poll_window_without_cli_timeout(self) -> None:
        code, frames, _ = _run_in_process(
            {"protocol": 1, "type": "request", "id": "slow", "operation": "creative_generate", "arguments": {"task": "t"}},
            _FakeBridge(delay=0.01),
        )
        self.assertEqual(code, 0)
        self.assertEqual(frames[1]["ok"], True)

    def test_cli_does_not_require_global_mcp_or_profile_arguments(self) -> None:
        request = {"protocol": 1, "type": "request", "id": "preview", "operation": "creative_preview", "arguments": {"task": "t"}}
        code, frames, _ = _run_in_process(request, _FakeBridge({"network": False, "text": ""}))
        self.assertEqual(code, 0)
        self.assertEqual(frames[0]["protocol"], 1)
        self.assertEqual(json.loads("".join(str(frame["data"]) for frame in frames[2:]))["network"], False)

    def test_protocol_and_type_are_strict_before_bridge_construction(self) -> None:
        valid_request = {"protocol": 1, "type": "request", "id": "invalid", "operation": "creative_preview", "arguments": {}}
        invalid_requests = (
            {key: value for key, value in valid_request.items() if key != "protocol"},
            {**valid_request, "protocol": 2},
            {**valid_request, "protocol": True},
            {**valid_request, "protocol": 1.0},
            {**valid_request, "protocol": "1"},
            {**valid_request, "type": "response"},
            {key: value for key, value in valid_request.items() if key != "type"},
            {**valid_request, "type": True},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                stdin = io.StringIO(json.dumps(request) + "\n")
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(cli, "Bridge", return_value=_FakeBridge()) as bridge_factory,
                    patch.object(cli, "_configure_stdio_utf8"),
                    patch.object(cli, "_configure_ssl_cert_file") as ssl_configure,
                    patch.object(cli.sys, "stdin", stdin),
                    patch.object(cli.sys, "stdout", stdout),
                    patch.object(cli.sys, "stderr", stderr),
                ):
                    code = cli.main(["run"])
                frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
                self.assertEqual(code, 0)
                self.assertFalse(bridge_factory.called)
                ssl_configure.assert_not_called()
                self.assertFalse(frames[1]["ok"])
                serialized = "".join(str(frame["data"]) for frame in frames[2:])
                self.assertEqual(json.loads(serialized), {"error": "invalid request envelope"})

    def test_duplicate_request_keys_are_rejected_before_bridge(self) -> None:
        stdin = io.StringIO('{"protocol":1,"protocol":1,"type":"request","operation":"creative_preview"}\n')
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(cli, "Bridge", return_value=_FakeBridge()) as bridge_factory,
            patch.object(cli, "_configure_stdio_utf8"),
            patch.object(cli, "_configure_ssl_cert_file"),
            patch.object(cli.sys, "stdin", stdin),
            patch.object(cli.sys, "stdout", stdout),
            patch.object(cli.sys, "stderr", stderr),
        ):
            self.assertEqual(cli.main(["run"]), 0)
        self.assertFalse(bridge_factory.called)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertFalse(frames[1]["ok"])

    def _spawn_pty(self) -> tuple[subprocess.Popen[str], int, int, list[int]]:
        root = tempfile.TemporaryDirectory(prefix="creative-cli-pty-")
        self.addCleanup(root.cleanup)
        home = Path(root.name) / "codex-home"
        home.mkdir()
        (home / "config.toml").write_text(
            "[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = \"provider\"\n\n"
            "[model_providers.provider]\nbase_url = \"https://provider.test/v1\"\nwire_api = \"responses\"\n",
            encoding="utf-8",
        )
        master, slave = pty.openpty()
        baseline = termios.tcgetattr(slave)
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
        process = subprocess.Popen(
            [sys.executable, "-B", str(PLUGIN_ROOT / "mcp" / "cli.py"), "run"],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            close_fds=True,
        )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process, master, slave, baseline

    @staticmethod
    def _read_ready(process: subprocess.Popen[str]) -> dict[str, object]:
        assert process.stdout is not None
        line = process.stdout.readline()
        return json.loads(line)

    def test_pty_ready_disables_echo_but_keeps_canonical_and_request_is_not_echoed(self) -> None:
        process, master, slave, baseline = self._spawn_pty()
        self.addCleanup(lambda: os.close(master))
        self.addCleanup(lambda: os.close(slave))
        ready = self._read_ready(process)
        self.assertEqual(ready["input_mode"], "tty")
        self.assertFalse(ready["input_echo"])
        changed = termios.tcgetattr(slave)
        self.assertEqual(changed[3] & termios.ICANON, baseline[3] & termios.ICANON | termios.ICANON)
        self.assertEqual(changed[3] & (termios.ECHO | termios.ECHONL), 0)
        request = {"protocol": 1, "type": "request", "id": "pty", "operation": "creative_preview", "arguments": {"task": "pty-secret"}}
        os.write(master, (json.dumps(request) + "\n").encode())
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertNotIn("pty-secret", stdout)
        self.assertEqual(termios.tcgetattr(slave), baseline)

    def test_pty_signal_restores_terminal_mode(self) -> None:
        process, master, slave, baseline = self._spawn_pty()
        self.addCleanup(lambda: os.close(master))
        self.addCleanup(lambda: os.close(slave))
        ready = self._read_ready(process)
        self.assertEqual(ready["input_mode"], "tty")
        process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130)
        self.assertEqual(termios.tcgetattr(slave), baseline)

    def test_pipe_eof_fails_before_ready_and_pipe_line_is_cached(self) -> None:
        environment = os.environ.copy()
        with tempfile.TemporaryDirectory(prefix="creative-cli-pipe-") as temporary:
            home = Path(temporary)
            environment["CODEX_HOME"] = str(home)
            eof = subprocess.run(
                [sys.executable, "-B", str(PLUGIN_ROOT / "mcp" / "cli.py"), "run"],
                input="",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            self.assertEqual(eof.returncode, 1)
            self.assertEqual(eof.stdout, "")
            self.assertIn("EOF before ready", eof.stderr)

    def test_windows_console_mode_mock_preserves_line_input(self) -> None:
        calls: list[tuple[int, int]] = []

        class FakeKernel:
            def GetConsoleMode(self, _handle: int, pointer: object) -> int:
                pointer.value = 0x0006  # ENABLE_LINE_INPUT + ENABLE_ECHO_INPUT
                return 1

            def SetConsoleMode(self, handle: int, mode: int) -> int:
                calls.append((handle, mode))
                return 1

        class FakeCtypes:
            windll = types.SimpleNamespace(kernel32=FakeKernel())

            @staticmethod
            def c_uint32() -> object:
                return types.SimpleNamespace(value=0)

            @staticmethod
            def byref(value: object) -> object:
                return value

        fake_msvcrt = types.SimpleNamespace(get_osfhandle=lambda fd: fd + 100)
        gate = cli._InputGate()
        with patch.dict(sys.modules, {"ctypes": FakeCtypes, "msvcrt": fake_msvcrt}):
            gate._prepare_windows(9)
            gate.restore()
        self.assertEqual(calls, [(109, 0x0002), (109, 0x0006)])


if __name__ == "__main__":
    unittest.main()
