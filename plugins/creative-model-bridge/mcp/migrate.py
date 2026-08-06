"""Strict, byte-preserving migration of historical CMB MCP ownership.

Migration is intentionally read-only until every historical state, renderer,
marker, table span, and credential channel has been verified.  The transaction
then writes a backup/WAL, performs a config/state CAS, and uses atomic writes
with rollback.  It never touches current v4 runtime objects or provider data.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from typing import Any, Iterator


INSTALL_NAME = "creative-model-bridge"
SCHEMA_VERSION = 2
SSL_CERT_ENV = "SSL_CERT_FILE"
RESERVED_ENV_KEYS = frozenset({"CODEX_HOME", "CREATIVE_MODEL_API_KEY", SSL_CERT_ENV})
VERSIONED_FAMILIES = tuple(f"0.1.{minor}" for minor in range(6, 19))
VERSIONLESS_FAMILIES = frozenset({"0.1.3", "0.1.4", "0.1.5"})
LEGACY_STATE_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "install_id",
        "config_path",
        "config_digest",
        "managed_digest",
        "command",
        "command_sha256",
        "env_key",
        "updated_at",
    }
)
VERSIONED_STATE_KEYS = LEGACY_STATE_KEYS | {"bridge_version"}
ENTRY_KEYS = frozenset({"command", "args", "env_vars", "env"})
BASE_ENV_KEYS = frozenset({"CODEX_HOME"})
BASE_ENV_VARS = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"]
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BEGIN_RE = re.compile(r'^# creative-model-bridge:begin schema=1 install_id="(?P<id>[0-9a-f-]{36})"$')
END_RE = re.compile(r'^# creative-model-bridge:end install_id="(?P<id>[0-9a-f-]{36})"$')


class MigrationError(RuntimeError):
    """Safe migration error; provider values are never included."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        result: dict[str, Any] = {}
        for key, value in items:
            if key in seen:
                raise MigrationError("legacy provision state contains duplicate keys")
            seen.add(key)
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MigrationError(f"legacy provision state contains invalid number {value}")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
    except MigrationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError("legacy provision state is unreadable") from error
    if not isinstance(value, dict):
        raise MigrationError("legacy provision state must be an object")
    return value


def _codex_home(home: Path | None = None) -> Path:
    selected = home or (Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex")
    return selected.expanduser().resolve()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _remove_file(path: Path) -> None:
    path.unlink()


def _require_regular(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise MigrationError(f"legacy migration {label} is unreadable") from error
    if path.is_symlink() or mode & 0o170000 != 0o100000:
        raise MigrationError(f"legacy migration {label} must be a regular file")


@contextmanager
def _migration_lock(state_root: Path) -> Iterator[None]:
    lock = state_root / "migration.lock"
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    try:
        lock.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise MigrationError("another migration is active") from error
    try:
        _atomic_write(lock / "owner", f"pid={os.getpid()}\ntoken={token}\n".encode("ascii"))
        yield
    finally:
        try:
            _remove_file(lock / "owner")
        except FileNotFoundError:
            pass
        try:
            lock.rmdir()
        except OSError:
            pass


def _state_values(state: dict[str, Any], config_path: Path) -> tuple[str, str | None]:
    if type(state.get("schema_version")) is not int or state.get("schema_version") != SCHEMA_VERSION:
        raise MigrationError("legacy provision state schema_version must be integer 2")
    if state.get("status") != "installed":
        raise MigrationError("legacy provision state is not installed")
    install_id = state.get("install_id")
    if not isinstance(install_id, str) or UUID_RE.fullmatch(install_id) is None:
        raise MigrationError("legacy provision install_id is invalid")
    recorded_config = state.get("config_path")
    if not isinstance(recorded_config, str) or not Path(recorded_config).is_absolute() or Path(recorded_config).resolve() != config_path.resolve():
        raise MigrationError("legacy provision config path does not match CODEX_HOME")
    command_value = state.get("command")
    if not isinstance(command_value, str) or not Path(command_value).is_absolute():
        raise MigrationError("legacy provision command identity is invalid")
    command = Path(command_value)
    try:
        command_stat = command.lstat()
    except OSError as error:
        raise MigrationError("legacy provision command is missing") from error
    if not command_stat.st_mode & 0o170000 == 0o100000:
        raise MigrationError("legacy provision command is not a regular file")
    if command.is_symlink():
        raise MigrationError("legacy provision command must not be a symlink")
    command_digest = state.get("command_sha256")
    try:
        actual_command_digest = _digest(command.read_bytes())
    except OSError as error:
        raise MigrationError("legacy provision command is unreadable") from error
    if not isinstance(command_digest, str) or DIGEST_RE.fullmatch(command_digest) is None or actual_command_digest != command_digest:
        raise MigrationError("legacy provision command digest does not match")
    for key in ("config_digest", "managed_digest"):
        value = state.get(key)
        if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
            raise MigrationError(f"legacy provision {key} is invalid")
    updated_at = state.get("updated_at")
    if type(updated_at) is not int or updated_at < 0:
        raise MigrationError("legacy provision updated_at is invalid")

    bridge_version = state.get("bridge_version")
    if bridge_version is None:
        if set(state) != LEGACY_STATE_KEYS:
            raise MigrationError("versionless legacy state keys are not exact")
        family = "0.1.5"
    else:
        if type(bridge_version) is not str or bridge_version not in VERSIONED_FAMILIES:
            raise MigrationError("legacy provision bridge_version is unsupported")
        allowed = VERSIONED_STATE_KEYS
        if set(state) == allowed:
            ssl_cert = None
        elif set(state) == allowed | {"ssl_cert_file"}:
            ssl_cert = state.get("ssl_cert_file")
            if not isinstance(ssl_cert, str) or not Path(ssl_cert).is_absolute() or not ssl_cert:
                raise MigrationError("legacy provision ssl_cert_file is invalid")
        else:
            raise MigrationError("versioned legacy state keys are not exact")
        family = bridge_version
        if family == "0.1.18" and ssl_cert is not None and not isinstance(ssl_cert, str):
            raise MigrationError("legacy provision ssl_cert_file is invalid")
        if family in {"0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10", "0.1.11", "0.1.12", "0.1.13", "0.1.14", "0.1.15", "0.1.16", "0.1.17", "0.1.18"}:
            pass
    env_key = state.get("env_key")
    if env_key is not None and (not isinstance(env_key, str) or ENV_NAME_RE.fullmatch(env_key) is None or env_key in RESERVED_ENV_KEYS):
        raise MigrationError("legacy provision env_key is invalid")
    return family, env_key


def _provider_env_key(parsed: dict[str, Any]) -> str | None:
    shell = parsed.get("shell_environment_policy")
    selected = shell.get("set", {}).get("CREATIVE_MODEL_PROVIDER") if isinstance(shell, dict) and isinstance(shell.get("set"), dict) else None
    providers = parsed.get("model_providers")
    provider = providers.get(selected) if isinstance(providers, dict) and isinstance(selected, str) else None
    if not isinstance(provider, dict):
        return None
    value = provider.get("env_key")
    if value is None:
        return None
    if not isinstance(value, str) or ENV_NAME_RE.fullmatch(value) is None or value in RESERVED_ENV_KEYS:
        raise MigrationError("selected provider env_key is invalid")
    return value


def _render_block(family: str, install_id: str, command: str, home: Path, env_key: str | None, ssl_cert: str | None) -> str:
    env_vars = list(BASE_ENV_VARS)
    if env_key is not None:
        env_vars.append(env_key)
    if ssl_cert is not None:
        env_vars.append(SSL_CERT_ENV)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    block = (
        f'# creative-model-bridge:begin schema=1 install_id="{install_id}"\n'
        "[mcp_servers.creative-model-bridge]\n"
        f"command = {quote(command)}\nargs = []\nenv_vars = {json.dumps(env_vars)}\n\n"
        "[mcp_servers.creative-model-bridge.env]\n"
        f"CODEX_HOME = {quote(str(home))}\n"
    )
    if ssl_cert is not None:
        block += f"{SSL_CERT_ENV} = {quote(ssl_cert)}\n"
    return block + f'# creative-model-bridge:end install_id="{install_id}"\n'


def _heading(line: str) -> str | None:
    content = line.rstrip("\r\n")
    if content.startswith("[[") or not content.startswith("["):
        return None
    if not content.endswith("]"):
        raise MigrationError("legacy CMB table heading is malformed")
    return content[1:-1]


def _owned_structure(text: str, install_id: str) -> dict[str, Any]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    begin: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    headings: list[tuple[int, str]] = []
    for index, raw in enumerate(lines):
        content = raw.rstrip("\r\n")
        begin_match = BEGIN_RE.fullmatch(content)
        end_match = END_RE.fullmatch(content)
        if "creative-model-bridge:begin" in content and begin_match is None:
            raise MigrationError("legacy CMB begin marker is malformed")
        if "creative-model-bridge:end" in content and end_match is None:
            raise MigrationError("legacy CMB end marker is malformed")
        if begin_match:
            if begin is not None or end is not None or begin_match.group("id") != install_id:
                raise MigrationError("legacy CMB begin marker is repeated or mismatched")
            begin = (index, offsets[index])
        if end_match:
            if begin is None or end is not None or end_match.group("id") != install_id:
                raise MigrationError("legacy CMB end marker is repeated or mismatched")
            end = (index, offsets[index])
        table = _heading(content)
        if table is not None:
            headings.append((index, table))
    if begin is None or end is None or end[0] <= begin[0]:
        raise MigrationError("legacy CMB marker pair is incomplete")
    root_indices = [index for index, table in headings if table == f"mcp_servers.{INSTALL_NAME}"]
    env_indices = [index for index, table in headings if table == f"mcp_servers.{INSTALL_NAME}.env"]
    if len(root_indices) != 1 or len(env_indices) != 1:
        raise MigrationError("legacy CMB requires exactly one root and env table")
    root_index, env_index = root_indices[0], env_indices[0]
    if not begin[0] < root_index < env_index < end[0]:
        raise MigrationError("legacy CMB tables are outside marker span")
    for index in range(begin[0] + 1, root_index):
        if lines[index].strip():
            raise MigrationError("legacy CMB marker span contains unowned content")
    for index, table in headings:
        if begin[0] < index < end[0] and table not in {f"mcp_servers.{INSTALL_NAME}", f"mcp_servers.{INSTALL_NAME}.env"}:
            raise MigrationError("legacy CMB marker encloses an unrelated table")
        if INSTALL_NAME in table and table not in {f"mcp_servers.{INSTALL_NAME}", f"mcp_servers.{INSTALL_NAME}.env"}:
            raise MigrationError("legacy CMB table heading is not canonical")

    def span(index: int) -> tuple[int, int]:
        next_heading = next((candidate for candidate, _ in headings if candidate > index), len(lines))
        stop = offsets[next_heading] if next_heading < len(lines) else len(text)
        if stop > end[1]:
            stop = end[1]
        if stop <= offsets[index]:
            raise MigrationError("legacy CMB table span is empty")
        table_text = text[offsets[index] : stop]
        if "#" in table_text:
            raise MigrationError("legacy CMB table span contains an unowned comment")
        return offsets[index], stop

    root_span = span(root_index)
    env_span = span(env_index)
    return {
        "begin_span": (begin[1], begin[1] + len(lines[begin[0]])),
        "end_span": (end[1], end[1] + len(lines[end[0]])),
        "root_span": root_span,
        "env_span": env_span,
        "block": text[begin[1] : begin[1] + len(lines[begin[0]])] + text[root_span[0] : root_span[1]] + text[env_span[0] : env_span[1]] + text[end[1] : end[1] + len(lines[end[0]])],
    }


def _owned_span(text: str, install_id: str) -> dict[str, Any]:
    """Compatibility-named ownership span helper used by release checks."""

    return _owned_structure(text, install_id)


def _validate_owned_config(text: str, state: dict[str, Any], home: Path, family: str, env_key: str | None) -> dict[str, Any]:
    import tomllib

    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise MigrationError("Codex config.toml is not valid TOML") from error
    if _provider_env_key(parsed) != env_key:
        raise MigrationError("selected provider env_key does not match legacy state")
    structure = _owned_structure(text, str(state["install_id"]))
    servers = parsed.get("mcp_servers")
    entry = servers.get(INSTALL_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
        raise MigrationError("legacy CMB root table keys are not exact")
    if entry.get("command") != state["command"] or entry.get("args") != []:
        raise MigrationError("legacy CMB command or args do not match state")
    ssl_cert = state.get("ssl_cert_file")
    expected_env_vars = list(BASE_ENV_VARS)
    if env_key is not None:
        expected_env_vars.append(env_key)
    if ssl_cert is not None:
        expected_env_vars.append(SSL_CERT_ENV)
    if entry.get("env_vars") != expected_env_vars:
        raise MigrationError("legacy CMB env_vars order or channels do not match state")
    env_table = entry.get("env")
    expected_env_keys = set(BASE_ENV_KEYS) | ({SSL_CERT_ENV} if ssl_cert is not None else set())
    if not isinstance(env_table, dict) or set(env_table) != expected_env_keys:
        raise MigrationError("legacy CMB env table keys are not exact")
    if env_table.get("CODEX_HOME") != str(home):
        raise MigrationError("legacy CMB CODEX_HOME does not match state")
    if ssl_cert is not None and env_table.get(SSL_CERT_ENV) != ssl_cert:
        raise MigrationError("legacy CMB SSL channel does not match state")
    expected_block = _render_block(family, str(state["install_id"]), str(state["command"]), home, env_key, ssl_cert)
    if structure["block"] != expected_block:
        raise MigrationError("legacy CMB managed block is not the historical renderer output")
    if _digest(expected_block.encode("utf-8")) != state["managed_digest"]:
        raise MigrationError("legacy CMB managed_digest does not match historical renderer")
    return structure


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    result = text
    for start, end in sorted(spans, reverse=True):
        result = result[:start] + result[end:]
    return result


def _manifest(config_before: bytes, state_before: bytes, structure: dict[str, Any], family: str) -> bytes:
    value = {
        "schema_version": 1,
        "family": family,
        "config_sha256": _digest(config_before),
        "state_sha256": _digest(state_before),
        "spans": [structure[key] for key in ("begin_span", "root_span", "env_span", "end_span")],
    }
    return (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")


def migrate_legacy(*, home: Path | None = None) -> dict[str, Any]:
    """Remove one verified historical CMB marker and its two owned tables."""

    codex_home = _codex_home(home)
    config_path = codex_home / "config.toml"
    state_root = codex_home / INSTALL_NAME
    state_path = state_root / "provision-state.json"
    if not state_path.exists():
        if not config_path.exists():
            return {"status": "absent", "changed": False, "backup": None}
        try:
            config_probe = config_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise MigrationError("Codex config.toml is not valid UTF-8") from error
        if "creative-model-bridge:begin" not in config_probe and "creative-model-bridge:end" not in config_probe:
            return {"status": "absent", "changed": False, "backup": None}
        raise MigrationError("legacy migration requires both config.toml and state")
    if not config_path.exists():
        raise MigrationError("legacy migration requires both config.toml and state")
    if state_root.is_symlink():
        raise MigrationError("legacy migration state root must not be a symlink")
    _require_regular(config_path, "config.toml")
    _require_regular(state_path, "provision state")
    with _migration_lock(state_root):
        config_before = config_path.read_bytes()
        state_before = state_path.read_bytes()
        state = _strict_json(state_before)
        family, env_key = _state_values(state, config_path)
        try:
            text = config_before.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationError("Codex config.toml is not valid UTF-8") from error
        structure = _validate_owned_config(text, state, codex_home, family, env_key)
        config_after = _remove_spans(
            text,
            [structure["begin_span"], structure["root_span"], structure["env_span"], structure["end_span"]],
        ).encode("utf-8")
        wal_path = state_root / "migration.wal.json"
        if wal_path.exists():
            raise MigrationError("previous migration WAL requires recovery")
        token = f"{int(time.time())}-{uuid.uuid4().hex}"
        backup_root = state_root / "migration-backups" / token
        try:
            backup_root.mkdir(parents=True, exist_ok=False)
            try:
                os.chmod(backup_root, 0o700)
            except OSError:
                pass
            _atomic_write(backup_root / "config.toml", config_before)
            _atomic_write(backup_root / "provision-state.json", state_before)
            _atomic_write(backup_root / "manifest.json", _manifest(config_before, state_before, structure, family))
            wal = {
                "schema_version": 1,
                "backup": str(backup_root),
                "config_sha256": _digest(config_before),
                "state_sha256": _digest(state_before),
                "config_after_sha256": _digest(config_after),
            }
            _atomic_write(wal_path, (json.dumps(wal, sort_keys=True) + "\n").encode("utf-8"))
        except MigrationError:
            raise
        except Exception as error:
            raise MigrationError("legacy migration backup/WAL preparation failed") from error
        try:
            if config_path.read_bytes() != config_before or state_path.read_bytes() != state_before:
                raise MigrationError("legacy migration CAS changed during preflight")
            _atomic_write(config_path, config_after)
            if config_path.read_bytes() != config_after:
                raise MigrationError("legacy migration CAS changed during config write")
            if state_path.read_bytes() != state_before:
                raise MigrationError("legacy migration CAS changed during state removal")
            _remove_file(state_path)
            if state_path.exists():
                raise MigrationError("legacy migration CAS changed during state removal")
            _remove_file(wal_path)
        except Exception as error:
            try:
                _atomic_write(config_path, config_before)
                _atomic_write(state_path, state_before)
            except Exception as rollback_error:
                raise MigrationError("legacy migration rollback failed; backup and WAL retained") from rollback_error
            if isinstance(error, MigrationError):
                raise
            raise MigrationError("legacy migration failed; backup and WAL retained") from error
    return {
        "status": "migrated",
        "changed": True,
        "backup": str(backup_root),
        "removed_mcp": INSTALL_NAME,
        "removed_pointers": [],
        "family": family,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="creative-model-bridge migrate")
    parser.add_argument("--codex-home", type=Path)
    args = parser.parse_args(argv)
    try:
        result = migrate_legacy(home=args.codex_home)
    except MigrationError as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
