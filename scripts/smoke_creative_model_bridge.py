#!/usr/bin/env python3
"""Smoke-test the CMB-EXEC-2 launcher cache/run/install path.

The smoke test intentionally uses a local override and never sends a provider
request. Provider SSE/verbatim behavior is covered by the plugin's mock HTTP
tests; this script proves release-binary framing and legacy cleanup wiring.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


MAX_CHUNK_BYTES = 4096


class SmokeFailure(RuntimeError):
    def __init__(self, phase: str, detail: str, returncode: int | None = None) -> None:
        self.phase = phase
        self.detail = detail
        self.returncode = returncode
        suffix = f" (returncode={returncode})" if returncode is not None else ""
        super().__init__(f"{phase}: {detail}{suffix}")


def _phase(name: str) -> None:
    print(f"creative-model-bridge smoke phase: {name}", file=sys.stderr, flush=True)


def _run(command: list[str], environment: dict[str, str], *, phase: str, input_text: str = "", timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, input=input_text, capture_output=True, text=True, env=environment, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise SmokeFailure(phase, "process") from error
    if type(result.returncode) is not int:
        raise SmokeFailure(phase, "invalid return code")
    return result


def _decode(frames_text: str, *, request_id: str, phase: str, require_input_gate: bool = False) -> dict[str, Any]:
    try:
        frames = [json.loads(line) for line in frames_text.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise SmokeFailure(phase, "malformed NDJSON") from error
    if not frames or frames[0].get("type") != "ready" or frames[0].get("protocol") != 1:
        raise SmokeFailure(phase, "ready frame missing")
    if require_input_gate and (frames[0].get("input_echo") is not False or frames[0].get("input_mode") != "pipe"):
        raise SmokeFailure(phase, "input gate metadata mismatch")
    if len(frames) < 3:
        raise SmokeFailure(phase, "response frames missing")
    response = frames[1]
    if response.get("type") != "response" or response.get("id") != request_id:
        raise SmokeFailure(phase, "response metadata mismatch")
    chunks = frames[2:]
    if response.get("chunks") != len(chunks) or [item.get("seq") for item in chunks] != list(range(len(chunks))):
        raise SmokeFailure(phase, "chunk sequence mismatch")
    if not chunks or chunks[-1].get("done") is not True or any(item.get("done") is True for item in chunks[:-1]):
        raise SmokeFailure(phase, "chunk completion marker mismatch")
    if any(item.get("id") != request_id or item.get("sha256") != response.get("sha256") for item in chunks):
        raise SmokeFailure(phase, "chunk identity mismatch")
    serialized = "".join(item.get("data", "") for item in chunks)
    raw = serialized.encode("utf-8")
    if len(raw) != response.get("bytes") or hashlib.sha256(raw).hexdigest() != response.get("sha256"):
        raise SmokeFailure(phase, "result digest mismatch")
    for item in chunks:
        if hashlib.sha256(str(item.get("data", "")).encode("utf-8")).hexdigest() != item.get("chunk_sha256"):
            raise SmokeFailure(phase, "chunk digest mismatch")
    try:
        value = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise SmokeFailure(phase, "result JSON is malformed") from error
    if response.get("ok") is not True:
        raise SmokeFailure(phase, f"bridge returned an error: {value.get('error') if isinstance(value, dict) else 'unknown'}")
    if not isinstance(value, dict):
        raise SmokeFailure(phase, "result is not an object")
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


def _migration_fixture(home: Path) -> tuple[str, str, Path]:
    fixture_root = Path(__file__).resolve().parents[1] / "plugins" / "creative-model-bridge" / "tests" / "fixtures" / "history" / "v0.1.18"
    state_root = home / "creative-model-bridge"
    state_root.mkdir(parents=True)
    materialized_home = home.resolve()
    old_root = b"/private/tmp/cmb-history-materializer/v0.1.18"
    config = (fixture_root / "config.toml").read_bytes().replace(old_root, str(materialized_home).encode())
    config += b"\n[mcp_servers.other]\ncommand = \"other\"\n"
    config_path = home / "config.toml"
    config_path.write_bytes(config)
    command_path = materialized_home / "legacy-command"
    shutil.copyfile(fixture_root / "legacy-command", command_path)
    command_path.chmod(0o700)
    state = json.loads((fixture_root / "provision-state.json").read_text(encoding="utf-8"))
    state["config_path"] = str(config_path.resolve())
    state["command"] = str(command_path)
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
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        print("creative-model-bridge smoke failure: binary missing", file=sys.stderr)
        return 1
    launcher = Path(__file__).resolve().parents[1] / "plugins" / "creative-model-bridge" / "scripts" / "bootstrap.sh"
    if not launcher.is_file():
        print("creative-model-bridge smoke failure: launcher missing", file=sys.stderr)
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="creative-smoke-") as temporary:
            root = Path(temporary)
            home = root / "offline-home"
            home.mkdir()
            _config(home / "config.toml")
            environment = os.environ.copy()
            environment.update({
                "CODEX_HOME": str(home),
                "CREATIVE_MODEL_BRIDGE_BIN": str(binary),
                "CREATIVE_MODEL_BRIDGE_OFFLINE": "1",
                "PYTHONIOENCODING": "utf-8",
            })
            _phase("cache-prewarm")
            cached = _run([str(launcher), "cache"], environment, phase="cache")
            if cached.returncode != 0 or cached.stdout:
                raise SmokeFailure("cache", "non-zero exit or unexpected output", cached.returncode)
            active_pointers = list((home / "creative-model-bridge" / "runtime" / "v0.2.0" / "objects").glob("*/active"))
            if len(active_pointers) != 1:
                raise SmokeFailure("cache", "current-version active pointer missing")
            _phase("run-preview")
            request = {"protocol": 1, "type": "request", "id": "preview-1", "operation": "creative_preview", "arguments": {"task": "offline smoke", "model": "smoke-model"}}
            result = _run([str(launcher), "run"], environment, phase="preview", input_text=json.dumps(request, ensure_ascii=False) + "\n")
            if result.returncode != 0:
                raise SmokeFailure("preview", "non-zero exit", result.returncode)
            preview = _decode(result.stdout, request_id="preview-1", phase="preview", require_input_gate=True)
            if preview.get("network") is not False or preview.get("model") != "smoke-model":
                raise SmokeFailure("preview", "network/model parity")
            _phase("legacy-migration")
            migrate_home = root / "migration-home"
            migrate_home.mkdir()
            _install_id, _command, pointer = _migration_fixture(migrate_home)
            runtime = migrate_home / "creative-model-bridge" / "runtime" / "v4" / "objects" / "active-object"
            migration_env = os.environ.copy()
            migration_env.update({
                "CODEX_HOME": str(migrate_home),
                "CREATIVE_MODEL_BRIDGE_BIN": str(binary),
                "CREATIVE_MODEL_BRIDGE_OFFLINE": "1",
                "PYTHONIOENCODING": "utf-8",
            })
            migrated = _run([str(launcher), "install"], migration_env, phase="migration")
            if migrated.returncode != 0:
                raise SmokeFailure("migration", "non-zero exit", migrated.returncode)
            report = json.loads(migrated.stdout)
            if report.get("status") != "migrated" or not pointer.exists() or not runtime.exists():
                raise SmokeFailure("migration", "owned cleanup assertion")
            config_after = (migrate_home / "config.toml").read_text(encoding="utf-8")
            if "creative-model-bridge:begin" in config_after or "[mcp_servers.other]" not in config_after:
                raise SmokeFailure("migration", "unrelated config preservation")
    except SmokeFailure as failure:
        print(f"creative-model-bridge smoke failure: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
