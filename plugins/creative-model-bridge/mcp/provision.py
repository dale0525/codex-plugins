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
from typing import Any, Callable, Iterator

try:  # pragma: no cover - direct script execution fallback
    from provision_ownership import OwnershipError, marker as _ownership_marker, owned_config as _parse_owned_config, remove_spans as _remove_owned_spans, scan_markers as _scan_owned_markers
except ImportError:  # pragma: no cover
    from .provision_ownership import OwnershipError, marker as _ownership_marker, owned_config as _parse_owned_config, remove_spans as _remove_owned_spans, scan_markers as _scan_owned_markers


INSTALL_NAME = "creative-model-bridge"
SCHEMA_VERSION = 2
PROVISION_VERSION = "0.1.13"
SSL_CERT_ENV = "SSL_CERT_FILE"
# Ordered, deterministic Linux candidates.  The first readable, non-empty
# regular file wins; callers/tests may provide an explicit candidate sequence.
LINUX_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/pki/tls/cacert.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
)
MACOS_CA_CANDIDATES = ("/etc/ssl/cert.pem",)
BEGIN_PREFIX = "creative-model-bridge:begin"
END_PREFIX = "creative-model-bridge:end"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GENERATION_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
RESERVED_ENV_KEYS = frozenset({"CODEX_HOME", "CREATIVE_MODEL_API_KEY", SSL_CERT_ENV})
LEGACY_STATE_KEYS = frozenset({
    "schema_version", "status", "install_id", "config_path", "config_digest",
    "managed_digest", "command", "command_sha256", "env_key", "updated_at",
})


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


def _valid_ca_file(path: Path) -> bool:
    """Return true only for an absolute readable non-empty regular file."""

    try:
        stat_result = path.stat()
        return bool(path.is_absolute() and stat_result.st_size > 0 and stat_result.st_mode & 0o170000 == 0o100000 and stat_result.st_mode & 0o444 and os.access(path, os.R_OK))
    except OSError:
        return False


def resolve_ssl_cert_file(
    *,
    platform_name: str | None = None,
    platform: str | None = None,
    explicit: str | Path | None = None,
    candidates: tuple[str | Path, ...] | list[str | Path] | None = None,
    file_checker: Callable[[Path], bool] | None = None,
) -> str | None:
    """Resolve the deterministic CA bundle used by the provisioned server.

    ``SSL_CERT_FILE`` (or the plugin-specific alias) is an explicit override;
    when present it is validated before any provisioning writes.  Windows keeps
    the platform trust store by default and therefore returns ``None``.
    ``platform_name``, ``candidates`` and ``file_checker`` are injectable for
    deterministic unit tests.
    """

    if explicit is None:
        if "CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE" in os.environ:
            explicit = os.environ["CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE"]
        elif SSL_CERT_ENV in os.environ:
            explicit = os.environ[SSL_CERT_ENV]
    checker = file_checker or _valid_ca_file
    if explicit is not None:
        value = Path(str(explicit))
        if not value.is_absolute() or not checker(value):
            raise ProvisionError("SSL_CERT_FILE must be an absolute readable non-empty regular file")
        return str(value)

    normalized = (platform_name or platform or sys.platform).lower()
    if normalized.startswith("win") or normalized in {"windows", "nt"}:
        return None
    if normalized.startswith("darwin") or normalized in {"macos", "mac"}:
        ordered = candidates if candidates is not None else MACOS_CA_CANDIDATES
    elif normalized.startswith("linux"):
        ordered = candidates if candidates is not None else LINUX_CA_CANDIDATES
    else:
        ordered = candidates if candidates is not None else LINUX_CA_CANDIDATES
    for candidate in ordered:
        path = Path(candidate).expanduser()
        if path.is_absolute() and checker(path):
            return str(path)
    raise ProvisionError("no usable system CA bundle was found; set SSL_CERT_FILE to an absolute readable file")


# Kept as a private alias for embedders that used the provision module's
# internal helper naming during the 0.1.5 preview.
_resolve_ssl_cert_file = resolve_ssl_cert_file


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


def _scan_markers(text: str, *, allow_incomplete: bool = False) -> dict[str, Any] | None:
    try:
        return _scan_owned_markers(text, allow_incomplete=allow_incomplete)
    except OwnershipError as error:
        raise ProvisionError(str(error)) from error


def _marker(text: str) -> dict[str, Any] | None:
    """Return one complete canonical marker pair, preserving legacy semantics."""
    try:
        return _ownership_marker(text)
    except OwnershipError as error:
        raise ProvisionError(str(error)) from error


def _owned_config(text: str) -> dict[str, Any] | None:
    try:
        return _parse_owned_config(text, _parse_toml)
    except OwnershipError as error:
        raise ProvisionError(str(error)) from error


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    return _remove_owned_spans(text, spans)


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
    if key in RESERVED_ENV_KEYS:
        raise ProvisionError(f"configured provider env_key conflicts with reserved environment key: {key}")
    return key


def _state_values_valid(state: dict[str, Any]) -> bool:
    if not isinstance(state.get("config_path"), str) or not isinstance(state.get("command"), str):
        return False
    if any(
        not isinstance(state.get(key), str) or DIGEST_RE.fullmatch(state[key]) is None
        for key in ("config_digest", "managed_digest", "command_sha256")
    ):
        return False
    env_key = state.get("env_key")
    if env_key is not None and (not isinstance(env_key, str) or not ENV_RE.fullmatch(env_key) or env_key in RESERVED_ENV_KEYS):
        return False
    return type(state.get("updated_at")) is int and state["updated_at"] >= 0


def _legacy_state_shape(state: dict[str, Any]) -> bool:
    """Accept only reviewed pre-0.1.13 state contracts for migration/removal."""

    has_version = "bridge_version" in state
    version = state.get("bridge_version")
    if has_version and version not in {"0.1.5", "0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10", "0.1.11", "0.1.12"}:
        return False
    base = LEGACY_STATE_KEYS | ({"bridge_version"} if has_version else set())
    if version in {"0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10", "0.1.11", "0.1.12"}:
        # CA016/CA017 emitted ssl_cert_file on POSIX and omitted it when
        # Windows kept the native trust store. Both exact shapes are
        # migratable; arbitrary extra fields remain fail-closed.
        if set(state) == base:
            pass
        elif set(state) != base | {"ssl_cert_file"}:
            return False
        else:
            value = state.get("ssl_cert_file")
            if not isinstance(value, str) or not Path(value).is_absolute():
                return False
    elif set(state) != base or "ssl_cert_file" in state:
        return False
    return _state_values_valid(state)


def _current_state_shape(state: dict[str, Any]) -> bool:
    if state.get("bridge_version") != PROVISION_VERSION:
        return False
    base = LEGACY_STATE_KEYS | {"bridge_version"}
    if set(state) == base:
        return _state_values_valid(state)
    if set(state) != base | {"ssl_cert_file"}:
        return False
    value = state.get("ssl_cert_file")
    return isinstance(value, str) and Path(value).is_absolute() and _state_values_valid(state)


def _supported_state_version(state: dict[str, Any]) -> bool:
    return _current_state_shape(state) or _legacy_state_shape(state)


def _preflight_provider_env(home: Path) -> None:
    """Reject reserved provider channels before lock/state/WAL creation."""

    config_path = home / "config.toml"
    if not config_path.exists():
        return
    try:
        _provider_env_key(config_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise ProvisionError("Codex config.toml is not valid UTF-8") from error


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


def _render_block(install_id: str, command: Path, home: Path, env_key: str | None, ssl_cert_file: str | None) -> str:
    if env_key in RESERVED_ENV_KEYS:
        raise ProvisionError(f"configured provider env_key conflicts with reserved environment key: {env_key}")
    envs = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"]
    if env_key and env_key not in envs:
        envs.append(env_key)
    if ssl_cert_file:
        envs.append(SSL_CERT_ENV)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    block = (
        f'# creative-model-bridge:begin schema=1 install_id="{install_id}"\n'
        "[mcp_servers.creative-model-bridge]\n"
        f"command = {quote(str(command))}\nargs = []\nenv_vars = {json.dumps(envs)}\n\n"
        "[mcp_servers.creative-model-bridge.env]\n"
        f"CODEX_HOME = {quote(str(home))}\n"
    )
    if ssl_cert_file:
        block += f"{SSL_CERT_ENV} = {quote(ssl_cert_file)}\n"
    return block + f'# creative-model-bridge:end install_id="{install_id}"\n'


def _validate_final(text: str, install_id: str, command: Path, home: Path, env_key: str | None, ssl_cert_file: str | None) -> dict[str, Any]:
    marker = _marker(text)
    if marker is None or marker["install_id"] != install_id:
        raise ProvisionError("final config does not contain the owned marker pair")
    value = _parse_toml(text)
    servers = value.get("mcp_servers", {})
    entry = servers.get(INSTALL_NAME) if isinstance(servers, dict) else None
    envs = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"] + ([env_key] if env_key and env_key not in {"CODEX_HOME", "CREATIVE_MODEL_API_KEY"} else [])
    if ssl_cert_file:
        envs.append(SSL_CERT_ENV)
    if not isinstance(entry, dict) or entry.get("command") != str(command) or entry.get("args") != [] or entry.get("env_vars") != envs:
        raise ProvisionError("final MCP config failed validation")
    env_table = entry.get("env")
    if not isinstance(env_table, dict) or env_table.get("CODEX_HOME") != str(home):
        raise ProvisionError("final MCP environment config failed validation")
    if set(entry) != {"command", "args", "env_vars", "env"}:
        raise ProvisionError("final MCP config contains unexpected owned keys")
    expected_env_keys = {"CODEX_HOME"} | ({SSL_CERT_ENV} if ssl_cert_file else set())
    if set(env_table) != expected_env_keys:
        raise ProvisionError("final MCP environment config contains unexpected owned keys")
    if ssl_cert_file:
        if env_table.get(SSL_CERT_ENV) != ssl_cert_file:
            raise ProvisionError("final MCP CA environment config failed validation")
    elif SSL_CERT_ENV in env_table:
        raise ProvisionError("final MCP CA environment config is unexpected")
    return {"managed_digest": _digest(marker["block"].encode("utf-8")), "env_key": env_key}


def _legacy_ssl_cert(state: dict[str, Any]) -> str | None:
    version = state.get("bridge_version")
    return state.get("ssl_cert_file") if version in {"0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10", PROVISION_VERSION} else None


def _validate_owned_semantics(
    value: dict[str, Any],
    state: dict[str, Any],
    command: Path,
    home: Path,
    env_key: str | None,
    ssl_cert_file: str | None,
) -> None:
    servers = value.get("mcp_servers")
    entry = servers.get(INSTALL_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict) or entry.get("command") != str(command) or entry.get("args") != []:
        raise ProvisionError("owned MCP config command semantics do not match provision state")
    envs = ["CODEX_HOME", "CREATIVE_MODEL_API_KEY"] + ([env_key] if env_key and env_key not in {"CODEX_HOME", "CREATIVE_MODEL_API_KEY"} else [])
    if ssl_cert_file:
        envs.append(SSL_CERT_ENV)
    if entry.get("env_vars") != envs or set(entry) != {"command", "args", "env_vars", "env"}:
        raise ProvisionError("owned MCP config environment semantics do not match provision state")
    env_table = entry.get("env")
    if not isinstance(env_table, dict) or env_table.get("CODEX_HOME") != str(home):
        raise ProvisionError("owned MCP environment home does not match provision state")
    expected_env_keys = {"CODEX_HOME"} | ({SSL_CERT_ENV} if ssl_cert_file else set())
    if set(env_table) != expected_env_keys:
        raise ProvisionError("owned MCP environment keys do not match provision state")
    if ssl_cert_file:
        if env_table.get(SSL_CERT_ENV) != ssl_cert_file:
            raise ProvisionError("owned MCP CA environment does not match provision state")
    elif SSL_CERT_ENV in env_table:
        raise ProvisionError("owned MCP CA environment is unexpected")
    if state.get("env_key") != env_key:
        raise ProvisionError("owned MCP provider environment does not match provision state")


def _structural_owned_healthy(
    state: dict[str, Any],
    owned: dict[str, Any],
    text: str,
    home: Path,
    *,
    allow_missing_ssl: bool = False,
) -> bool:
    """Validate the exact legacy identity required for begin-only recovery."""

    if state.get("status") != "installed" or not (_legacy_state_shape(state) or _current_state_shape(state)):
        return False
    if owned.get("install_id") != state.get("install_id"):
        return False
    command = Path(str(state.get("command", "")))
    if state.get("config_path") != str(home / "config.toml") or not command.is_file():
        return False
    try:
        if state.get("command_sha256") != _file_digest(command):
            return False
        env_key = _provider_env_key(text)
        ssl_cert_file = _legacy_ssl_cert(state)
        if ssl_cert_file is not None and (not isinstance(ssl_cert_file, str) or not Path(ssl_cert_file).is_absolute()):
            return False
        _validate_owned_semantics(owned["value"], state, command, home, env_key, ssl_cert_file)
        canonical = _render_block(str(state["install_id"]), command, home, env_key, ssl_cert_file)
        if state.get("managed_digest") != _digest(canonical.encode("utf-8")):
            return False
    except (OSError, ProvisionError):
        return False
    return not ssl_cert_file or allow_missing_ssl or _valid_ca_file(Path(ssl_cert_file))


_legacy_owned_healthy = _structural_owned_healthy


def _remove_owned(text: str, install_id: str) -> tuple[str, str]:
    owned = _owned_config(text)
    if owned is None:
        if _foreign(text):
            raise ProvisionError("foreign same-name MCP config")
        return text, ""
    if owned["install_id"] != install_id:
        raise ProvisionError("creative-model-bridge marker is owned by another installation")
    spans = owned["spans"] + owned["marker_spans"]
    updated = _remove_spans(text, spans)
    owned_bytes = "".join(text[start:end] for start, end in sorted(spans))
    return updated, _digest(owned_bytes.encode("utf-8"))


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


def _healthy(
    state: dict[str, Any],
    marker: dict[str, Any],
    text: str,
    home: Path,
    *,
    allow_missing_ssl: bool = False,
    legacy: bool = False,
) -> bool:
    command = Path(str(state.get("command", "")))
    if legacy and not _legacy_state_shape(state):
        return False
    try:
        env_key = _provider_env_key(text)
        # The 0.1.6 state contract already recorded CA ownership when one was
        # configured; only the older pre-CA shape omits it.
        ssl_cert_file = state.get("ssl_cert_file") if (not legacy or state.get("bridge_version") in {"0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10"}) else None
        details = _validate_final(text, str(state["install_id"]), command, home, env_key, ssl_cert_file)
    except ProvisionError:
        return False
    if state.get("status") != "installed" or state.get("managed_digest") != details["managed_digest"] or state.get("env_key") != env_key or not command.is_file() or state.get("command_sha256") != _file_digest(command) or state.get("config_path") != str(home / "config.toml"):
        return False
    if not legacy and state.get("bridge_version") != PROVISION_VERSION:
        return False
    ssl_cert_file = state.get("ssl_cert_file") if (not legacy or state.get("bridge_version") in {"0.1.6", "0.1.7", "0.1.8", "0.1.9", "0.1.10"}) else None
    if ssl_cert_file is not None and (not isinstance(ssl_cert_file, str) or not Path(ssl_cert_file).is_absolute()):
        return False
    if ssl_cert_file and not allow_missing_ssl and not _valid_ca_file(Path(ssl_cert_file)):
        return False
    return True


def setup(
    *,
    home: Path | None = None,
    repair: bool = False,
    platform_name: str | None = None,
    candidates: tuple[str | Path, ...] | list[str | Path] | None = None,
    ssl_cert_file: str | Path | None = None,
) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    _preflight_provider_env(home)
    # Resolve and validate explicit/default CA values before creating the
    # lock/state directory.  A previously installed legacy/current state owns
    # its CA path; preserve that exact value during migration/repair instead of
    # silently replacing it with the host default.
    pre_state: dict[str, Any] | None = None
    if state_path(home).is_file() and not wal_path(home).is_file():
        _, _, pre_state = _state(home)
    explicit_ca = ssl_cert_file is not None or "CREATIVE_MODEL_BRIDGE_SSL_CERT_FILE" in os.environ or SSL_CERT_ENV in os.environ
    owned_ca = None
    if pre_state and pre_state.get("status") == "installed":
        if _current_state_shape(pre_state) or _legacy_state_shape(pre_state):
            owned_ca = pre_state.get("ssl_cert_file")
    if owned_ca is not None and not _valid_ca_file(Path(str(owned_ca))):
        raise ProvisionError("configured CA bundle is missing or unreadable")
    ssl_cert_file_value = (
        resolve_ssl_cert_file(platform_name=platform_name, candidates=candidates, explicit=ssl_cert_file)
        if explicit_ca or owned_ca is None
        else str(owned_ca)
    )
    home.mkdir(parents=True, exist_ok=True)
    with _lock(home):
        config_path = home / "config.toml"
        before_config = _image(config_path)
        state_exists, state_bytes, state = _state(home)
        if state is not None and not _supported_state_version(state):
            raise ProvisionError("provision state bridge_version is unsupported")
        text = before_config[1].decode("utf-8")
        owned = _owned_config(text)
        marker = _marker(text) if owned and owned["complete"] else None
        current_healthy = bool(state and state.get("status") == "installed" and marker and _healthy(state, marker, text, home))
        legacy_healthy = bool(state and state.get("status") == "installed" and marker and _healthy(state, marker, text, home, legacy=True))
        structural_healthy = bool(state and owned and _structural_owned_healthy(state, owned, text, home))
        legacy_structural_healthy = bool(state and owned and _legacy_state_shape(state) and _structural_owned_healthy(state, owned, text, home))
        if current_healthy and not owned["expanded"]:
            return state  # type: ignore[return-value]
        if owned and not owned["complete"] and not legacy_structural_healthy:
            raise ProvisionError("begin-only ownership requires an exact installed legacy configuration")
        if state and state.get("status") == "installed" and not repair and not legacy_healthy and not structural_healthy:
            raise ProvisionError("owned configuration drift detected; run provision repair")
        if repair and (not state or state.get("status") != "installed" or owned is None or owned["install_id"] != state.get("install_id")):
            raise ProvisionError("repair requires an owned installed configuration")
        if repair and owned and not owned["complete"] and not legacy_structural_healthy:
            raise ProvisionError("repair requires an exact installed legacy begin-only configuration")
        install_id = _install_id(state)
        if owned and owned["install_id"] != install_id:
            raise ProvisionError("foreign creative-model-bridge marker")
        if owned:
            base, _ = _remove_owned(text, install_id)
        else:
            base = text
        if not owned and _foreign(text):
            raise ProvisionError("foreign same-name MCP config")
        command = _executable()
        env_key = _provider_env_key(base)
        block = _render_block(install_id, command, home, env_key, ssl_cert_file_value)
        updated = base + ("\n" if base and not base.endswith("\n") else "") + ("\n" if base else "") + block
        details = _validate_final(updated, install_id, command, home, env_key, ssl_cert_file_value)
        new_state = {"schema_version": 2, "status": "installed", "install_id": install_id, "config_path": str(config_path), "config_digest": _digest(updated.encode("utf-8")), "managed_digest": details["managed_digest"], "command": str(command), "command_sha256": _file_digest(command), "env_key": env_key, "bridge_version": PROVISION_VERSION, "updated_at": int(time.time())}
        if ssl_cert_file_value:
            new_state["ssl_cert_file"] = ssl_cert_file_value
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
    if state and state.get("ssl_cert_file"):
        ca_path = Path(str(state["ssl_cert_file"]))
        if not _valid_ca_file(ca_path):
            issues.append(f"configured CA bundle is missing or unreadable: {ca_path}")
            if status_value == "installed":
                status_value = "drift"
    return {"schema_version": 2, "status": status_value, "state": state, "config_path": str(config_path), "config_digest": config_digest, "managed": marker is not None, "command": state.get("command") if state else None, "command_exists": bool(state and Path(str(state.get("command", ""))).is_file()), "managed_digest": state.get("managed_digest") if state else None, "issues": issues}


def uninstall(*, home: Path | None = None) -> dict[str, Any]:
    home = (home or codex_home()).resolve()
    with _lock(home):
        config_path = home / "config.toml"
        before_config = _image(config_path)
        state_exists, state_bytes, state = _state(home)
        if state is None:
            return status(home=home)
        if not _supported_state_version(state):
            raise ProvisionError("provision state bridge_version is unsupported")
        if state.get("status") == "uninstalled":
            return state
        text = before_config[1].decode("utf-8")
        owned = _owned_config(text)
        marker = _marker(text) if owned and owned["complete"] else None
        if owned is None or owned["install_id"] != state.get("install_id"):
            raise ProvisionError("owned marker is absent or foreign")
        legacy = state.get("bridge_version") != PROVISION_VERSION
        if owned["complete"]:
            healthy = bool(marker and _healthy(state, marker, text, home, allow_missing_ssl=True, legacy=legacy))
            if not healthy and owned["expanded"]:
                healthy = _structural_owned_healthy(state, owned, text, home, allow_missing_ssl=True)
        else:
            healthy = _legacy_state_shape(state) and _structural_owned_healthy(state, owned, text, home, allow_missing_ssl=True)
        if not healthy:
            raise ProvisionError("owned configuration drift detected; run provision repair")
        updated, managed_digest = _remove_owned(text, str(state["install_id"]))
        _parse_toml(updated)
        new_state = {**state, "status": "uninstalled", "config_digest": _digest(updated.encode("utf-8")), "managed_digest": managed_digest, "updated_at": int(time.time())}
        after_state = (json.dumps(new_state, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _transaction(home, "uninstall", before_config, updated.encode("utf-8"), (state_exists, state_bytes, _digest(state_bytes)), after_state)
        _journal(home, "uninstall", install_id=state["install_id"])
        return new_state


def run(
    action: str,
    *,
    home: Path | None = None,
    yes: bool = False,
    platform_name: str | None = None,
    candidates: tuple[str | Path, ...] | list[str | Path] | None = None,
    ssl_cert_file: str | Path | None = None,
) -> dict[str, Any]:
    if action == "setup":
        return setup(home=home, platform_name=platform_name, candidates=candidates, ssl_cert_file=ssl_cert_file)
    if action == "repair":
        return setup(home=home, repair=True, platform_name=platform_name, candidates=candidates, ssl_cert_file=ssl_cert_file)
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
    parser.add_argument("--ssl-cert-file", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.action, home=args.codex_home, yes=args.yes, ssl_cert_file=args.ssl_cert_file)
    except ProvisionError as error:
        print(f"creative-model-bridge: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
