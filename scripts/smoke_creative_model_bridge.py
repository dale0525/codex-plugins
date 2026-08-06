#!/usr/bin/env python3
"""Smoke-test the CMB-EXEC-2 launcher cache/run/install path.

The smoke test intentionally uses a local override and never sends a provider
request. Provider SSE/verbatim behavior is covered by the plugin's mock HTTP
tests; this script proves release-binary framing and legacy cleanup wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


MAX_CHUNK_BYTES = 4096
MAX_DIAGNOSTIC_STREAM_BYTES = 4096
_MIN_RETURN_CODE = -(2**31)
_MAX_DIAGNOSTIC_INT = 2**31 - 1

_PHASES = frozenset({"startup", "cache", "preview", "migration", "internal"})
_REASONS = frozenset(
    {
        "binary_missing",
        "launcher_missing",
        "spawn_failed",
        "timeout",
        "output_decode_failed",
        "invalid_returncode",
        "process_exit",
        "unexpected_stdout",
        "malformed_ndjson",
        "ready_missing",
        "input_gate_mismatch",
        "response_frames_missing",
        "response_metadata_mismatch",
        "chunk_sequence_mismatch",
        "chunk_completion_mismatch",
        "chunk_identity_mismatch",
        "result_digest_mismatch",
        "chunk_digest_mismatch",
        "result_json_malformed",
        "bridge_error",
        "result_not_object",
        "cache_pointer_missing",
        "cache_pointer_marker_mismatch",
        "cache_pointer_identity_mismatch",
        "cache_object_digest_mismatch",
        "preview_parity_mismatch",
        "migration_report_malformed",
        "migration_assertion_failed",
        "unexpected_exception",
    }
)
_LAUNCHERS = frozenset({"posix_bootstrap", "git_bash_bootstrap", "windows_powershell", "unknown"})
_ACTIONS = frozenset({"cache", "run", "install"})
_ACTIONS_WITH_UNKNOWN = _ACTIONS | {"unknown"}
_EXCEPTIONS = frozenset({"file_not_found", "permission", "timeout", "unicode", "os_error", "other"})


def _enum(value: object, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError("invalid diagnostic enum")
    return value


def _optional_int(value: object, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("invalid diagnostic integer")
    return value


def _stream_count(value: object) -> tuple[int, bool]:
    if type(value) is bytes:
        size = len(value)
    elif type(value) is str:
        size = len(value.encode("utf-8", errors="replace"))
    else:
        size = 0
    return min(size, MAX_DIAGNOSTIC_STREAM_BYTES), size > MAX_DIAGNOSTIC_STREAM_BYTES


def _exception_kind(error: BaseException) -> str:
    if isinstance(error, FileNotFoundError):
        return "file_not_found"
    if isinstance(error, PermissionError):
        return "permission"
    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(error, UnicodeError):
        return "unicode"
    if isinstance(error, OSError):
        return "os_error"
    return "other"


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    launcher: str
    action: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_capped: bool
    stderr_capped: bool


class SmokeFailure(RuntimeError):
    """A failure whose rendered form is restricted to approved metadata."""

    def __init__(
        self,
        phase: str,
        reason: str,
        *,
        launcher: str = "unknown",
        action: str = "unknown",
        exception: str | None = None,
        timeout_seconds: int | None = None,
        returncode: int | None = None,
        stdout_bytes: int | None = None,
        stderr_bytes: int | None = None,
        stdout_capped: bool = False,
        stderr_capped: bool = False,
    ) -> None:
        self.phase = _enum(phase, _PHASES)
        self.reason = _enum(reason, _REASONS)
        self.launcher = _enum(launcher, _LAUNCHERS)
        self.action = _enum(action, _ACTIONS_WITH_UNKNOWN)
        self.exception = None if exception is None else _enum(exception, _EXCEPTIONS)
        self.timeout_seconds = _optional_int(
            timeout_seconds,
            minimum=0,
            maximum=_MAX_DIAGNOSTIC_INT,
        )
        self.returncode = _optional_int(
            returncode,
            minimum=_MIN_RETURN_CODE,
            maximum=_MAX_DIAGNOSTIC_INT,
        )
        self.stdout_bytes = _optional_int(
            stdout_bytes,
            minimum=0,
            maximum=MAX_DIAGNOSTIC_STREAM_BYTES,
        )
        self.stderr_bytes = _optional_int(
            stderr_bytes,
            minimum=0,
            maximum=MAX_DIAGNOSTIC_STREAM_BYTES,
        )
        if type(stdout_capped) is not bool or type(stderr_capped) is not bool:
            raise ValueError("invalid diagnostic flag")
        if stdout_capped and (stdout_bytes is None or stdout_bytes != MAX_DIAGNOSTIC_STREAM_BYTES):
            raise ValueError("invalid diagnostic flag")
        if stderr_capped and (stderr_bytes is None or stderr_bytes != MAX_DIAGNOSTIC_STREAM_BYTES):
            raise ValueError("invalid diagnostic flag")
        self.stdout_capped = stdout_capped
        self.stderr_capped = stderr_capped
        super().__init__(self.render())

    def render(self) -> str:
        fields = [
            f"phase={self.phase}",
            f"reason={self.reason}",
            f"launcher={self.launcher}",
            f"action={self.action}",
        ]
        if self.exception is not None:
            fields.append(f"exception={self.exception}")
        if self.timeout_seconds is not None:
            fields.append(f"timeout_seconds={self.timeout_seconds}")
        if self.returncode is not None:
            fields.append(f"returncode={self.returncode}")
        if self.stdout_bytes is not None:
            fields.append(f"stdout_bytes={self.stdout_bytes}")
        if self.stderr_bytes is not None:
            fields.append(f"stderr_bytes={self.stderr_bytes}")
        if self.stdout_capped:
            fields.append("stdout_capped=true")
        if self.stderr_capped:
            fields.append("stderr_capped=true")
        return " ".join(fields)


def _failure(
    phase: str,
    reason: str,
    *,
    observation: ProcessObservation | None = None,
    launcher: str = "unknown",
    action: str = "unknown",
    exception: str | None = None,
    timeout_seconds: int | None = None,
    returncode: int | None = None,
) -> SmokeFailure:
    if observation is None:
        return SmokeFailure(
            phase,
            reason,
            launcher=launcher,
            action=action,
            exception=exception,
            timeout_seconds=timeout_seconds,
            returncode=returncode,
        )
    return SmokeFailure(
        phase,
        reason,
        launcher=observation.launcher,
        action=observation.action,
        exception=exception,
        timeout_seconds=timeout_seconds,
        returncode=returncode,
        stdout_bytes=observation.stdout_bytes,
        stderr_bytes=observation.stderr_bytes,
        stdout_capped=observation.stdout_capped,
        stderr_capped=observation.stderr_capped,
    )


def _phase(name: str) -> None:
    _enum(name, _PHASES)
    print(f"creative-model-bridge smoke phase: {name}", file=sys.stderr, flush=True)


def _bootstrap_basename(value: object) -> bool:
    if type(value) is not str:
        return False
    return value.replace("\\", "/").rsplit("/", 1)[-1] == "bootstrap.sh"


def _provision_basename(value: object) -> bool:
    if type(value) is not str:
        return False
    return value.replace("\\", "/").rsplit("/", 1)[-1] == "provision.ps1"


def _classify_command(command: object) -> tuple[str, str]:
    """Classify only the exact internal launcher shapes; never return tokens."""

    if type(command) is not list or any(type(item) is not str for item in command):
        return "unknown", "unknown"
    if len(command) == 2 and _bootstrap_basename(command[0]) and command[1] in _ACTIONS:
        return "posix_bootstrap", command[1]
    if (
        len(command) == 3
        and command[0] in {"bash", "bash.exe"}
        and _bootstrap_basename(command[1])
        and command[2] in _ACTIONS
    ):
        return "git_bash_bootstrap", command[2]
    if (
        len(command) == 8
        and command[0] == "powershell.exe"
        and command[1] == "-NoProfile"
        and command[2] == "-NonInteractive"
        and command[3] == "-ExecutionPolicy"
        and command[4] == "Bypass"
        and command[5] == "-File"
        and _provision_basename(command[6])
        and command[7] in _ACTIONS
    ):
        return "windows_powershell", command[7]
    return "unknown", "unknown"


def _process_observation(command: object, result: object) -> ProcessObservation:
    launcher, action = _classify_command(command)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    stdout_bytes, stdout_capped = _stream_count(stdout)
    stderr_bytes, stderr_capped = _stream_count(stderr)
    return ProcessObservation(
        launcher=launcher,
        action=action,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_capped=stdout_capped,
        stderr_capped=stderr_capped,
    )


def _timeout_seconds(timeout: object) -> int:
    if type(timeout) is int:
        value = timeout
    elif type(timeout) is float and math.isfinite(timeout) and timeout.is_integer():
        value = int(timeout)
    else:
        return 0
    if value < 0 or value > _MAX_DIAGNOSTIC_INT:
        return 0
    return value


def _valid_returncode(value: object) -> bool:
    return type(value) is int and _MIN_RETURN_CODE <= value <= _MAX_DIAGNOSTIC_INT


def _run(
    command: list[str],
    environment: dict[str, str],
    *,
    phase: str,
    input_text: str = "",
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    launcher, action = _classify_command(command)
    timeout_value = _timeout_seconds(timeout)
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout_bytes, stdout_capped = _stream_count(getattr(error, "output", None))
        stderr_bytes, stderr_capped = _stream_count(getattr(error, "stderr", None))
        observation = ProcessObservation(
            launcher=launcher,
            action=action,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout_capped=stdout_capped,
            stderr_capped=stderr_capped,
        )
        raise _failure(
            phase,
            "timeout",
            observation=observation,
            exception="timeout",
            timeout_seconds=timeout_value,
        ) from error
    except OSError as error:
        raise _failure(
            phase,
            "spawn_failed",
            launcher=launcher,
            action=action,
            exception=_exception_kind(error),
        ) from error
    except UnicodeError as error:
        raise _failure(
            phase,
            "output_decode_failed",
            launcher=launcher,
            action=action,
            exception="unicode",
        ) from error
    observation = _process_observation(command, result)
    returncode = getattr(result, "returncode", None)
    if not _valid_returncode(returncode):
        raise _failure(phase, "invalid_returncode", observation=observation)
    if returncode != 0:
        raise _failure(phase, "process_exit", observation=observation, returncode=returncode)
    return result


def _decode(
    frames_text: str,
    *,
    request_id: str,
    phase: str,
    require_input_gate: bool = False,
    observation: ProcessObservation | None = None,
) -> dict[str, Any]:
    def fail(reason: str) -> None:
        raise _failure(phase, reason, observation=observation)

    if type(frames_text) is not str:
        fail("malformed_ndjson")
    try:
        frames = [json.loads(line) for line in frames_text.splitlines() if line.strip()]
    except (json.JSONDecodeError, UnicodeError):
        fail("malformed_ndjson")
    if not frames or any(type(item) is not dict for item in frames):
        fail("malformed_ndjson")
    if frames[0].get("type") != "ready" or frames[0].get("protocol") != 1:
        fail("ready_missing")
    if require_input_gate and (frames[0].get("input_echo") is not False or frames[0].get("input_mode") != "pipe"):
        fail("input_gate_mismatch")
    if len(frames) < 3:
        fail("response_frames_missing")
    response = frames[1]
    if response.get("type") != "response" or response.get("id") != request_id:
        fail("response_metadata_mismatch")
    chunks = frames[2:]
    if response.get("chunks") != len(chunks) or [item.get("seq") for item in chunks] != list(range(len(chunks))):
        fail("chunk_sequence_mismatch")
    if not chunks or chunks[-1].get("done") is not True or any(item.get("done") is True for item in chunks[:-1]):
        fail("chunk_completion_mismatch")
    if any(item.get("id") != request_id or item.get("sha256") != response.get("sha256") for item in chunks):
        fail("chunk_identity_mismatch")
    serialized_parts = [item.get("data", "") for item in chunks]
    if any(type(item) is not str for item in serialized_parts):
        fail("result_json_malformed")
    serialized = "".join(serialized_parts)
    try:
        raw = serialized.encode("utf-8")
    except UnicodeError:
        fail("result_json_malformed")
    if len(raw) != response.get("bytes") or hashlib.sha256(raw).hexdigest() != response.get("sha256"):
        fail("result_digest_mismatch")
    for item in chunks:
        try:
            chunk_raw = item["data"].encode("utf-8")
        except (KeyError, UnicodeError, AttributeError):
            fail("chunk_digest_mismatch")
        if hashlib.sha256(chunk_raw).hexdigest() != item.get("chunk_sha256"):
            fail("chunk_digest_mismatch")
    try:
        value = json.loads(serialized)
    except (json.JSONDecodeError, UnicodeError):
        fail("result_json_malformed")
    if response.get("ok") is not True:
        fail("bridge_error")
    if not isinstance(value, dict):
        fail("result_not_object")
    return value


def _config(path: Path) -> None:
    path.write_text(
        "[shell_environment_policy.set]\n"
        'CREATIVE_MODEL_PROVIDER = "smoke"\n'
        'CREATIVE_MODEL_DEFAULT = "smoke/model"\n\n'
        "[model_providers.smoke]\n"
        'base_url = "http://offline.invalid/v1"\n'
        'wire_api = "responses"\n',
        encoding="utf-8",
    )


def _toml_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _migration_fixture(home: Path) -> tuple[str, str, Path]:
    fixture_root = Path(__file__).resolve().parents[1] / "plugins" / "creative-model-bridge" / "tests" / "fixtures" / "history" / "v0.1.18"
    state_root = home / "creative-model-bridge"
    state_root.mkdir(parents=True)
    materialized_home = home.resolve()
    command_path = materialized_home / "legacy-command"
    command_value = str(command_path)
    ssl_cert_file = str(materialized_home / "legacy-cert.pem") if os.name == "nt" else "/etc/ssl/cert.pem"
    config_text = (fixture_root / "config.toml").read_text(encoding="utf-8")
    config_text = config_text.replace(
        'command = "/private/tmp/cmb-history-materializer/v0.1.18/legacy-command"',
        f"command = {_toml_literal(command_value)}",
    ).replace(
        'CODEX_HOME = "/private/tmp/cmb-history-materializer/v0.1.18"',
        f"CODEX_HOME = {_toml_literal(str(materialized_home))}",
    ).replace(
        'SSL_CERT_FILE = "/etc/ssl/cert.pem"',
        f"SSL_CERT_FILE = {_toml_literal(ssl_cert_file)}",
    )
    config = (config_text + '\n[mcp_servers.other]\ncommand = "other"\n').encode("utf-8")
    config_path = home / "config.toml"
    config_path.write_bytes(config)
    shutil.copyfile(fixture_root / "legacy-command", command_path)
    command_path.chmod(0o700)
    state = json.loads((fixture_root / "provision-state.json").read_text(encoding="utf-8"))
    state["config_path"] = str(config_path.resolve())
    state["command"] = command_value
    state["ssl_cert_file"] = ssl_cert_file
    begin = config.decode("utf-8").index("# creative-model-bridge:begin")
    end = config.decode("utf-8").index("# creative-model-bridge:end", begin)
    end = config.decode("utf-8").index("\n", end) + 1
    state["managed_digest"] = hashlib.sha256(config.decode("utf-8")[begin:end].encode("utf-8")).hexdigest()
    (state_root / "provision-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    runtime = state_root / "runtime" / "v4" / "objects" / "active-object"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"cmb-active-v4\n")
    pointer = state_root / "runtime" / "v4" / "pointer"
    pointer.write_bytes(b"active-object\n")
    # Keep the explicit helper contract used by repository tests: the
    # install_id and command are returned as provenance strings, while the
    # command in the materialized state remains a real regular file.
    install_id = str(state["install_id"])
    return install_id, "/tmp/creative-model-bridge-legacy", pointer


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: smoke_creative_model_bridge.py BINARY", file=sys.stderr)
        return 2
    current_phase = "startup"
    try:
        binary = Path(sys.argv[1]).resolve()
        if not binary.is_file():
            raise _failure("startup", "binary_missing")
        launcher = Path(__file__).resolve().parents[1] / "plugins" / "creative-model-bridge" / "scripts" / "bootstrap.sh"
        entrypoint = launcher.with_name("provision.ps1") if os.name == "nt" else launcher
        if not entrypoint.is_file():
            raise _failure("startup", "launcher_missing")
        with tempfile.TemporaryDirectory(prefix="creative-smoke-") as temporary:
            root = Path(temporary)
            home = root / "offline-home"
            home.mkdir()
            _config(home / "config.toml")
            environment = os.environ.copy()
            environment.update(
                {
                    "CODEX_HOME": str(home),
                    "CREATIVE_MODEL_BRIDGE_BIN": str(binary),
                    "CREATIVE_MODEL_BRIDGE_OFFLINE": "1",
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            current_phase = "cache"
            _phase(current_phase)
            cache_command = _launcher_command(launcher, "cache")
            cached = _run(cache_command, environment, phase=current_phase)
            cache_observation = _process_observation(cache_command, cached)
            if cached.stdout:
                raise _failure(current_phase, "unexpected_stdout", observation=cache_observation)
            active_pointers = list((home / "creative-model-bridge" / "runtime" / "v0.2.0" / "objects").glob("*/active"))
            if len(active_pointers) != 1:
                raise _failure(current_phase, "cache_pointer_missing", observation=cache_observation)
            active_lines = active_pointers[0].read_text(encoding="utf-8").splitlines()
            if len(active_lines) != 3 or active_lines[0] != "cmb-active-v4":
                raise _failure(current_phase, "cache_pointer_marker_mismatch", observation=cache_observation)
            digest, generation = active_lines[1:]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest) or not generation:
                raise _failure(current_phase, "cache_pointer_identity_mismatch", observation=cache_observation)
            object_dir = active_pointers[0].parent / digest / generation
            payloads = [path for path in object_dir.iterdir() if path.is_file() and path.name != "complete"] if object_dir.is_dir() else []
            if len(payloads) != 1 or hashlib.sha256(payloads[0].read_bytes()).hexdigest() != digest:
                raise _failure(current_phase, "cache_object_digest_mismatch", observation=cache_observation)
            current_phase = "preview"
            _phase(current_phase)
            request = {"protocol": 1, "type": "request", "id": "preview-1", "operation": "creative_preview", "arguments": {"task": "offline smoke", "model": "smoke-model"}}
            preview_command = _launcher_command(launcher, "run")
            run_environment = environment.copy()
            run_environment.pop("CREATIVE_MODEL_BRIDGE_BIN", None)
            result = _run(preview_command, run_environment, phase=current_phase, input_text=json.dumps(request, ensure_ascii=False) + "\n")
            preview_observation = _process_observation(preview_command, result)
            preview = _decode(
                result.stdout,
                request_id="preview-1",
                phase=current_phase,
                require_input_gate=True,
                observation=preview_observation,
            )
            if preview.get("network") is not False or preview.get("model") != "smoke-model":
                raise _failure(current_phase, "preview_parity_mismatch", observation=preview_observation)
            current_phase = "migration"
            _phase(current_phase)
            migrate_home = root / "migration-home"
            migrate_home.mkdir()
            _install_id, _command, pointer = _migration_fixture(migrate_home)
            runtime = migrate_home / "creative-model-bridge" / "runtime" / "v4" / "objects" / "active-object"
            migration_env = os.environ.copy()
            migration_env.update(
                {
                    "CODEX_HOME": str(migrate_home),
                    "CREATIVE_MODEL_BRIDGE_BIN": str(binary),
                    "CREATIVE_MODEL_BRIDGE_OFFLINE": "1",
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            migration_command = _launcher_command(launcher, "install")
            migrated = _run(migration_command, migration_env, phase=current_phase)
            migration_observation = _process_observation(migration_command, migrated)
            try:
                report = json.loads(migrated.stdout)
            except (json.JSONDecodeError, TypeError, UnicodeError):
                raise _failure(current_phase, "migration_report_malformed", observation=migration_observation)
            if not isinstance(report, dict):
                raise _failure(current_phase, "migration_report_malformed", observation=migration_observation)
            if report.get("status") != "migrated" or not pointer.exists() or not runtime.exists():
                raise _failure(current_phase, "migration_assertion_failed", observation=migration_observation)
            config_after = (migrate_home / "config.toml").read_text(encoding="utf-8")
            if "creative-model-bridge:begin" in config_after or "[mcp_servers.other]" not in config_after:
                raise _failure(current_phase, "migration_assertion_failed", observation=migration_observation)
    except SmokeFailure as failure:
        print(f"creative-model-bridge smoke failure: {failure.render()}", file=sys.stderr)
        return 1
    except Exception as error:
        failure = _failure(current_phase, "unexpected_exception", exception=_exception_kind(error))
        print(f"creative-model-bridge smoke failure: {failure.render()}", file=sys.stderr)
        return 1
    return 0


def _launcher_command(launcher: Path, action: str) -> list[str]:
    """Select the platform-native provision entrypoint for one approved action."""

    _enum(action, _ACTIONS)
    if os.name == "nt":
        provision = launcher.with_name("provision.ps1")
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(provision),
            action,
        ]
    return [str(launcher), action]


if __name__ == "__main__":
    raise SystemExit(main())
