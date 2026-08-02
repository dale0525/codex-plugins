"""Transactional local MCP provisioning with crash recovery."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator


INSTALL_NAME = "creative-model-bridge"
SCHEMA_VERSION = 2
BEGIN_PREFIX = "creative-model-bridge:begin"
END_PREFIX = "creative-model-bridge:end"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GENERATION_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvisionError(RuntimeError):
    pass


class ManualRecovery(ProvisionError):
    pass


def codex_home() -> Path:
    value = os.environ.get("CODEX_HOME")
    return Path(value).expanduser() if value else Path.home() / ".codex"


def state_root(home: Path | None = None) -> Path:
    return (home or codex_home()) / INSTALL_NAME


def state_path(home: Path | None = None) -> Path:
    return state_root(home) / "provision-state.json"


def journal_path(home: Path | None = None) -> Path:
    return state_root(home) / "provision-journal.jsonl"


def wal_path(home: Path | None = None) -> Path:
    return state_root(home) / "provision-wal.json"


def lock_path(home: Path | None = None) -> Path:
    return state_root(home) / ".provision.lock"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_digest(text: str) -> str:
    return _digest(text.encode("utf-8"))


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _image(path: Path) -> tuple[bool, bytes, str]:
    if not path.exists():
        return False, b"", _digest(b"")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ProvisionError(f"cannot read {path.name}") from error
    return True, data, _digest(data)


def _same(path: Path, exists: bool, digest: str) -> bool:
    current_exists, _, current_digest = _image(path)
    return current_exists == exists and current_digest == digest


def _write_cas(path: Path, expected_exists: bool, expected_digest: str, data: bytes) -> None:
    if not _same(path, expected_exists, expected_digest):
        raise ManualRecovery(f"external edit detected while writing {path.name}")
    _atomic_write(path, data)


def _restore_image(path: Path, before_exists: bool, before_bytes: bytes) -> None:
    """Restore one WAL image without materializing an absent file.

    Existing images retain the normal atomic replacement semantics.  An image
    that did not exist before the transaction is restored by removing the path
    (if present), rather than writing an empty file for its empty payload.
    """
    if before_exists:
        _atomic_write(path, before_bytes)
    else:
        path.unlink(missing_ok=True)


def _journal(home: Path, event: str, **fields: Any) -> None:
    path = journal_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": int(time.time()), "event": event, **fields}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _state(home: Path) -> tuple[bool, bytes, dict[str, Any] | None]:
    exists, data, _ = _image(state_path(home))
    if not exists:
        return False, b"", None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvisionError("provision state is invalid") from error
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ProvisionError("provision state schema must be 2")
    install_id = value.get("install_id")
    if not isinstance(install_id, str) or not UUID_RE.fullmatch(install_id):
        raise ProvisionError("provision state install_id is invalid")
    if value.get("status") not in {"installed", "uninstalled"}:
        raise ProvisionError("provision state status is invalid")
    return True, data, value


def _parse_toml(text: str) -> dict[str, Any]:
    import tomllib

    try:
        value = tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise ProvisionError("Codex config.toml is not valid TOML") from error
    return value if isinstance(value, dict) else {}


def _marker(text: str) -> dict[str, Any] | None:
    lines = text.splitlines(keepends=True)
    begin: tuple[int, str] | None = None
    end: tuple[int, str] | None = None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        has_begin = BEGIN_PREFIX in line
        has_end = END_PREFIX in line
        if not has_begin and not has_end:
            continue
        begin_match = re.fullmatch(r'# creative-model-bridge:begin schema=1 install_id="([^"]+)"', line)
        end_match = re.fullmatch(r'# creative-model-bridge:end install_id="([^"]+)"', line)
        if has_begin and begin_match is None or has_end and end_match is None:
            raise ProvisionError("creative-model-bridge marker is malformed")
        if begin_match:
            if begin is not None or end is not None:
                raise ProvisionError("creative-model-bridge begin marker is repeated or nested")
            install_id = begin_match.group(1)
            if not UUID_RE.fullmatch(install_id):
                raise ProvisionError("creative-model-bridge begin install_id is invalid")
            begin = (index, install_id)
        if end_match:
            if begin is None or end is not None:
                raise ProvisionError("creative-model-bridge end marker is isolated or repeated")
            install_id = end_match.group(1)
            if not UUID_RE.fullmatch(install_id) or install_id != begin[1]:
                raise ProvisionError("creative-model-bridge marker install_id mismatch")
            end = (index, install_id)
    if begin is None and end is None:
        return None
    if begin is None or end is None or end[0] <= begin[0]:
        raise ProvisionError("creative-model-bridge marker pair is incomplete")
    return {"install_id": begin[1], "block": "".join(lines[begin[0] : end[0] + 1]), "start": begin[0], "end": end[0]}


def _provider_env_key(text: str) -> str | None:
    value = _parse_toml(text)
    shell = value.get("shell_environment_policy", {})
    selected = shell.get("set", {}).get("CREATIVE_MODEL_PROVIDER") if isinstance(shell, dict) else None
    providers = value.get("model_providers", {})
    provider = providers.get(selected, {}) if isinstance(providers, dict) and isinstance(selected, str) else {}
    key = provider.get("env_key") if isinstance(provider, dict) else None
    if key is None:
        return None
    if not isinstance(key, str) or not ENV_RE.fullmatch(key):
        raise ProvisionError("configured provider env_key is invalid")
    return key


def _foreign(text: str) -> bool:
    value = _parse_toml(text)
    servers = value.get("mcp_servers")
    return isinstance(servers, dict) and INSTALL_NAME in servers


def _executable() -> Path:
    override = os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE") or os.environ.get("CREATIVE_MODEL_BRIDGE_BIN")
    path = Path(override).expanduser().resolve() if override else (Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve().with_name("server.py"))
    if not path.is_file():
        raise ProvisionError(f"runtime executable does not exist: {path}")
    return path


def _install_id(state: dict[str, Any] | None) -> str:
    if state and state.get("install_id"):
        return str(state["install_id"])
    configured = os.environ.get("CREATIVE_MODEL_BRIDGE_INSTALL_ID")
    if configured and UUID_RE.fullmatch(configured):
        return configured
    if configured:
        raise ProvisionError("CREATIVE_MODEL_BRIDGE_INSTALL_ID must be a UUID")
    return str(uuid.uuid4())


def _render_block(install_id: str, command: Path, home: Path, env_key: str | None) -> str:
    envs = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"]
    if env_key and env_key not in envs:
        envs.append(env_key)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    return (
        f'# creative-model-bridge:begin schema=1 install_id="{install_id}"\n'
        "[mcp_servers.creative-model-bridge]\n"
        f"command = {quote(str(command))}\nargs = []\nenv_vars = {json.dumps(envs)}\n\n"
        "[mcp_servers.creative-model-bridge.env]\n"
        f"CODEX_HOME = {quote(str(home))}\n"
        f'# creative-model-bridge:end install_id="{install_id}"\n'
    )


def _validate_final(text: str, install_id: str, command: Path, home: Path, env_key: str | None) -> dict[str, Any]:
    marker = _marker(text)
    if marker is None or marker["install_id"] != install_id:
        raise ProvisionError("final config does not contain the owned marker pair")
    value = _parse_toml(text)
    servers = value.get("mcp_servers", {})
    entry = servers.get(INSTALL_NAME) if isinstance(servers, dict) else None
    envs = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"] + ([env_key] if env_key and env_key not in {"CODEX_HOME", "CREATIVE_MODEL_API_KEY"} else [])
    if not isinstance(entry, dict) or entry.get("command") != str(command) or entry.get("args") != [] or entry.get("env_vars") != envs:
        raise ProvisionError("final MCP config failed validation")
    env_table = entry.get("env")
    if not isinstance(env_table, dict) or env_table.get("CODEX_HOME") != str(home):
        raise ProvisionError("final MCP environment config failed validation")
    return {"managed_digest": _digest(marker["block"].encode("utf-8")), "env_key": env_key}


def _remove_owned(text: str, install_id: str) -> tuple[str, str]:
    marker = _marker(text)
    if marker is None:
        if _foreign(text):
            raise ProvisionError("foreign same-name MCP config")
        return text, ""
    if marker["install_id"] != install_id:
        raise ProvisionError("creative-model-bridge marker is owned by another installation")
    return text.replace(marker["block"], "", 1).lstrip("\n"), _digest(marker["block"].encode("utf-8"))


def _owner(path: Path) -> tuple[str, int] | None:
    markers = list(path.glob("owner.*")) if path.is_dir() else []
    if len(markers) != 1:
        return None
    token = markers[0].name[6:]
    try:
        line = next(item for item in markers[0].read_text(encoding="utf-8").splitlines() if item.startswith("pid="))
        return token, int(line[4:])
    except (OSError, StopIteration, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _validate_wal(raw: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate and decode a WAL without touching any managed files."""
    try:
        text = raw.decode("utf-8")

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate WAL field")
                result[key] = value
            return result

        wal = json.loads(text, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ManualRecovery("provision WAL is unreadable; manual recovery required") from error
    if not isinstance(wal, dict):
        raise ManualRecovery("provision WAL schema is invalid; manual recovery required")
    required = {
        "schema_version", "phase", "operation", "config_exists", "state_exists",
        "config_before", "config_after", "state_before", "state_after",
        "config_before_digest", "config_after_digest", "state_before_digest", "state_after_digest",
    }
    if set(wal) < required:
        raise ManualRecovery("provision WAL schema is incomplete; manual recovery required")
    if type(wal["schema_version"]) is not int or wal["schema_version"] != SCHEMA_VERSION:
        raise ManualRecovery("unsupported WAL schema; manual recovery required")
    phase = wal["phase"]
    if not isinstance(phase, str) or phase not in {"prepared", "config_written", "state_written", "committed", "rollback_requested", "manual_required"}:
        raise ManualRecovery("unknown WAL phase; manual recovery required")
    if not isinstance(wal["operation"], str) or wal["operation"] not in {"setup", "repair", "uninstall"}:
        raise ManualRecovery("provision WAL operation is invalid; manual recovery required")
    if type(wal["config_exists"]) is not bool or type(wal["state_exists"]) is not bool:
        raise ManualRecovery("provision WAL existence flags are invalid; manual recovery required")

    payloads: dict[str, bytes] = {}
    for key in ("config_before", "config_after", "state_before", "state_after"):
        encoded = wal[key]
        if not isinstance(encoded, str):
            raise ManualRecovery("provision WAL payload type is invalid; manual recovery required")
        try:
            payloads[key] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            raise ManualRecovery("provision WAL payload is invalid; manual recovery required") from error

    digest_fields = {
        "config_before": "config_before_digest", "config_after": "config_after_digest",
        "state_before": "state_before_digest", "state_after": "state_after_digest",
    }
    for payload_key, digest_key in digest_fields.items():
        digest = wal[digest_key]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None or digest != _digest(payloads[payload_key]):
            raise ManualRecovery("provision WAL payload digest mismatch; manual recovery required")
    for payload_key, exists_key in (("config_before", "config_exists"), ("state_before", "state_exists")):
        if not wal[exists_key] and payloads[payload_key] != b"":
            raise ManualRecovery("provision WAL absent image is non-empty; manual recovery required")
    return wal, payloads


def _recover(home: Path) -> None:
    path = wal_path(home)
    if not path.is_file():
        return
    try:
        wal, payloads = _validate_wal(path.read_bytes())
        phase = wal.get("phase")
        if phase == "manual_required":
            raise ManualRecovery("manual recovery is required; WAL retained")
        before = {key: payloads[key] for key in ("config_before", "state_before")}
        after = {key: payloads[key] for key in ("config_after", "state_after")}
        config = home / "config.toml"
        state = state_path(home)
        current = {"config_before": _same(config, wal["config_exists"], wal["config_before_digest"]), "state_before": _same(state, wal["state_exists"], wal["state_before_digest"]), "config_after": _same(config, True, wal["config_after_digest"]), "state_after": _same(state, True, wal["state_after_digest"])}
        if all(current[key] for key in ("config_before", "state_before", "config_after", "state_after")):
            raise ManualRecovery("WAL image ambiguity requires manual recovery")
        if current["config_before"] and current["state_before"] or current["config_after"] and current["state_after"]:
            path.unlink(missing_ok=True)
            return
        if phase in {"config_written", "state_written", "rollback_requested"}:
            if current["config_after"]:
                _restore_image(config, wal["config_exists"], before["config_before"])
            elif not current["config_before"]:
                raise ManualRecovery("config image diverged")
            if current["state_after"]:
                _restore_image(state, wal["state_exists"], before["state_before"])
            elif not current["state_before"]:
                raise ManualRecovery("state image diverged")
            _journal(home, "recovery", operation=wal.get("operation", "unknown"))
            path.unlink(missing_ok=True)
            return
        raise ManualRecovery("unknown WAL phase; WAL retained")
    except ManualRecovery:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ManualRecovery("provision WAL is unreadable; manual recovery required") from error


@contextmanager
def _lock(home: Path) -> Iterator[None]:
    root = state_root(home)
    root.mkdir(parents=True, exist_ok=True)
    path = lock_path(home)
    retired = root / "retired-locks"
    retired.mkdir(exist_ok=True)
    token = f"{os.getpid()}.{secrets.token_hex(8)}"
    attempts = 0
    max_attempts = int(os.environ.get("CREATIVE_MODEL_BRIDGE_LOCK_MAX_ATTEMPTS", "600"))
    stale_seconds = int(os.environ.get("CREATIVE_MODEL_BRIDGE_LOCK_STALE_SECONDS", "300"))
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            attempts += 1
            owner = _owner(path)
            stale = owner is not None and not _alive(owner[1]) and time.time() - path.stat().st_mtime >= stale_seconds
            stale = stale or owner is None and time.time() - path.stat().st_mtime >= stale_seconds
            if stale:
                try:
                    os.replace(path, retired / f"stale.{token}.{attempts}")
                    continue
                except OSError:
                    pass
            if attempts >= max_attempts:
                raise ProvisionError("another creative-model-bridge operation is active")
            time.sleep(0.05)
    marker = path / f"owner.{token}"
    marker.write_text(f"pid={os.getpid()}\ntoken={token}\nstarted={int(time.time())}\n", encoding="utf-8")
    try:
        _recover(home)
        yield
    finally:
        owner = _owner(path)
        if owner and owner[0] == token:
            released = retired / f"released.{token}"
            try:
                os.replace(path, released)
                shutil.rmtree(released, ignore_errors=True)
            except OSError:
                pass


def _transaction(home: Path, operation: str, before_config: tuple[bool, bytes, str], after_config: bytes, before_state: tuple[bool, bytes, str], after_state: bytes) -> None:
    config_path = home / "config.toml"
    state_file = state_path(home)
    record = {
        "schema_version": 2, "phase": "prepared", "operation": operation,
        "config_exists": before_config[0], "state_exists": before_state[0],
        "config_before": base64.b64encode(before_config[1]).decode(), "state_before": base64.b64encode(before_state[1]).decode(),
        "config_after": base64.b64encode(after_config).decode(), "state_after": base64.b64encode(after_state).decode(),
        "config_before_digest": before_config[2], "state_before_digest": before_state[2], "config_after_digest": _digest(after_config), "state_after_digest": _digest(after_state),
    }
    _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
    try:
        _write_cas(config_path, before_config[0], before_config[2], after_config)
        record["phase"] = "config_written"
        _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        if os.environ.get("CREATIVE_MODEL_BRIDGE_TEST_FAIL_AFTER_CONFIG") == "1":
            raise ProvisionError("injected provisioning failure after config write")
        if os.environ.get("CREATIVE_MODEL_BRIDGE_TEST_EXTERNAL_CONFIG_EDIT") == "1":
            _atomic_write(config_path, b"external edit\n")
        if not _same(config_path, True, record["config_after_digest"]):
            raise ManualRecovery("config changed before state CAS; WAL retained")
        _write_cas(state_file, before_state[0], before_state[2], after_state)
        record["phase"] = "state_written"
        _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        record["phase"] = "committed"
        _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        wal_path(home).unlink(missing_ok=True)
    except ManualRecovery:
        record["phase"] = "manual_required"
        _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        raise
    except Exception:
        record["phase"] = "rollback_requested"
        _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        try:
            if _same(config_path, True, record["config_after_digest"]):
                _restore_image(config_path, before_config[0], before_config[1])
            elif not _same(config_path, before_config[0], before_config[2]):
                raise ManualRecovery("config changed during rollback")
            if _same(state_file, True, record["state_after_digest"]):
                _restore_image(state_file, before_state[0], before_state[1])
            elif not _same(state_file, before_state[0], before_state[2]):
                raise ManualRecovery("state changed during rollback")
            wal_path(home).unlink(missing_ok=True)
        except ManualRecovery:
            record["phase"] = "manual_required"
            _atomic_write(wal_path(home), (json.dumps(record, sort_keys=True) + "\n").encode())
        raise


def _healthy(state: dict[str, Any], marker: dict[str, Any], text: str, home: Path) -> bool:
    command = Path(str(state.get("command", "")))
    try:
        env_key = _provider_env_key(text)
        details = _validate_final(text, str(state["install_id"]), command, home, env_key)
    except ProvisionError:
        return False
    return state.get("status") == "installed" and state.get("managed_digest") == details["managed_digest"] and state.get("env_key") == env_key and command.is_file() and state.get("command_sha256") == _file_digest(command) and state.get("config_path") == str(home / "config.toml")


def setup(*, home: Path | None = None, repair: bool = False) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    home.mkdir(parents=True, exist_ok=True)
    with _lock(home):
        config_path = home / "config.toml"
        before_config = _image(config_path)
        state_exists, state_bytes, state = _state(home)
        text = before_config[1].decode("utf-8")
        marker = _marker(text)
        if state and state.get("status") == "installed" and marker and _healthy(state, marker, text, home):
            return state
        if state and state.get("status") == "installed" and not repair:
            raise ProvisionError("owned configuration drift detected; run provision repair")
        if repair and (not state or state.get("status") != "installed" or marker is None or marker["install_id"] != state.get("install_id")):
            raise ProvisionError("repair requires an owned installed configuration")
        install_id = _install_id(state)
        if marker and marker["install_id"] != install_id:
            raise ProvisionError("foreign creative-model-bridge marker")
        base = text.replace(marker["block"], "", 1).lstrip("\n") if marker else text
        if not marker and _foreign(text):
            raise ProvisionError("foreign same-name MCP config")
        command = _executable()
        env_key = _provider_env_key(base)
        block = _render_block(install_id, command, home, env_key)
        updated = base + ("\n" if base and not base.endswith("\n") else "") + ("\n" if base else "") + block
        details = _validate_final(updated, install_id, command, home, env_key)
        new_state = {"schema_version": 2, "status": "installed", "install_id": install_id, "config_path": str(config_path), "config_digest": _digest(updated.encode("utf-8")), "managed_digest": details["managed_digest"], "command": str(command), "command_sha256": _file_digest(command), "env_key": env_key, "updated_at": int(time.time())}
        after_state = (json.dumps(new_state, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _transaction(home, "repair" if repair else "setup", before_config, updated.encode("utf-8"), (state_exists, state_bytes, _digest(state_bytes)), after_state)
        _journal(home, "repair" if repair else "setup", install_id=install_id)
        return new_state


def status(*, home: Path | None = None) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    wal = wal_path(home)
    if wal.is_file():
        try:
            phase = json.loads(wal.read_text(encoding="utf-8")).get("phase")
        except Exception:
            phase = "unknown"
        return {"schema_version": 2, "status": "pending_manual_recovery", "issues": [f"WAL phase {phase}; manual recovery required"], "config_path": str(home / "config.toml"), "managed": False}
    config_path = home / "config.toml"
    _, data, config_digest = _image(config_path)
    text = data.decode("utf-8", errors="replace")
    issues: list[str] = []
    try:
        marker = _marker(text)
        _parse_toml(text)
    except ProvisionError as error:
        marker, issues = None, [str(error)]
    try:
        state_exists, _, state = _state(home)
    except ProvisionError as error:
        state_exists, state, issues = False, None, [str(error)]
    foreign = False
    try:
        foreign = marker is None and _foreign(text)
    except ProvisionError as error:
        issues.append(str(error))
    if state is None:
        status_value = "foreign" if foreign else ("drift" if issues else "absent")
    elif state.get("status") == "uninstalled":
        status_value = "uninstalled" if marker is None and not foreign else "drift"
    elif marker is None:
        status_value = "drift"
    elif _healthy(state, marker, text, home):
        status_value = "installed"
    else:
        status_value = "drift"
        issues.append("owned configuration drift")
    return {"schema_version": 2, "status": status_value, "state": state, "config_path": str(config_path), "config_digest": config_digest, "managed": marker is not None, "command": state.get("command") if state else None, "command_exists": bool(state and Path(str(state.get("command", ""))).is_file()), "managed_digest": state.get("managed_digest") if state else None, "issues": issues}


def uninstall(*, home: Path | None = None) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    with _lock(home):
        config_path = home / "config.toml"
        before_config = _image(config_path)
        state_exists, state_bytes, state = _state(home)
        if state is None:
            return status(home=home)
        if state.get("status") == "uninstalled":
            return state
        text = before_config[1].decode("utf-8")
        marker = _marker(text)
        if marker is None or marker["install_id"] != state.get("install_id"):
            raise ProvisionError("owned marker is absent or foreign")
        if not _healthy(state, marker, text, home):
            raise ProvisionError("owned configuration drift detected; run provision repair")
        updated, managed_digest = _remove_owned(text, str(state["install_id"]))
        _parse_toml(updated)
        new_state = {**state, "status": "uninstalled", "config_digest": _digest(updated.encode("utf-8")), "managed_digest": managed_digest, "updated_at": int(time.time())}
        after_state = (json.dumps(new_state, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _transaction(home, "uninstall", before_config, updated.encode("utf-8"), (state_exists, state_bytes, _digest(state_bytes)), after_state)
        _journal(home, "uninstall", install_id=state["install_id"])
        return new_state


def run(action: str, *, home: Path | None = None, yes: bool = False) -> dict[str, Any]:
    if action == "setup":
        return setup(home=home)
    if action == "repair":
        return setup(home=home, repair=True)
    if action == "status":
        return status(home=home)
    if action == "uninstall":
        return uninstall(home=home)
    raise ProvisionError(f"unknown provision action: {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creative-model-bridge provision")
    parser.add_argument("action", choices=("setup", "status", "repair", "uninstall"))
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.action, home=args.codex_home, yes=args.yes)
    except ProvisionError as error:
        print(f"creative-model-bridge: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
