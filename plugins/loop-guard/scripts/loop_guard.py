#!/usr/bin/env python3
"""Privacy-preserving Codex hook for exact repeated tool failures."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "mode": "observe",
    "identical_failure_limit": 3,
    "window_seconds": 120,
    "state_ttl_seconds": 86400,
    "max_log_bytes": 5 * 1024 * 1024,
}
EXPLICIT_FAILURE_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_config(plugin_root: Path, data_dir: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    for path in (plugin_root / "config" / "defaults.json", data_dir / "config.json"):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if isinstance(loaded, dict):
            config.update(loaded)
    if config["mode"] not in {"observe", "enforce"}:
        config["mode"] = "observe"
    for key in (
        "identical_failure_limit",
        "window_seconds",
        "state_ttl_seconds",
        "max_log_bytes",
    ):
        try:
            config[key] = max(1, int(config[key]))
        except (TypeError, ValueError):
            config[key] = DEFAULT_CONFIG[key]
    return config


def load_or_create_secret(data_dir: Path) -> bytes:
    path = data_dir / "secret.key"
    try:
        value = path.read_bytes()
        if len(value) >= 32:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_bytes(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_bytes()
        return existing if len(existing) >= 32 else value
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
    return value


def digest(secret: bytes, label: str, value: Any) -> str:
    payload = label.encode("utf-8") + b"\x00" + canonical_json(value)
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:24]


def connect_database(data_dir: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(data_dir / "state.sqlite3", timeout=1.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=1000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scope_state (
            scope_key TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            tool_signature TEXT NOT NULL,
            failure_signature TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            last_seen REAL NOT NULL,
            blocked_attempts INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    return connection


def explicit_failure(value: Any) -> tuple[bool, Any]:
    if not isinstance(value, dict):
        return False, None
    for key in ("isError", "is_error", "failed"):
        if value.get(key) is True:
            return True, {key: True, "error": value.get("error")}
    for key in ("exit_code", "exitCode", "returncode", "status_code", "statusCode"):
        if key in value:
            try:
                code = int(value[key])
            except (TypeError, ValueError):
                continue
            if code != 0:
                return True, {key: code, "error": value.get("error")}
    status = value.get("status")
    if isinstance(status, str) and status.lower() in EXPLICIT_FAILURE_STATUSES:
        return True, {"status": status.lower(), "error": value.get("error")}
    if value.get("error") not in (None, "", False, {}):
        return True, {"error": value.get("error")}
    for key in ("result", "response", "content"):
        child = value.get(key)
        if isinstance(child, dict):
            failed, evidence = explicit_failure(child)
            if failed:
                return True, {key: evidence}
    return False, None


def scope_parts(event: dict[str, Any], secret: bytes) -> tuple[str, str, bool]:
    session_id = str(event.get("session_id") or "")
    turn_id = str(event.get("turn_id") or "")
    transcript_path = str(event.get("transcript_path") or "")
    session_key = digest(secret, "session", session_id)
    scope_key = digest(secret, "scope", [session_id, turn_id, transcript_path])
    enforceable = bool(session_id and turn_id and transcript_path)
    return session_key, scope_key, enforceable


def tool_signature(event: dict[str, Any], secret: bytes) -> str:
    return digest(
        secret,
        "tool",
        [str(event.get("tool_name") or ""), event.get("tool_input")],
    )


def append_event(data_dir: Path, config: dict[str, Any], payload: dict[str, Any]) -> None:
    path = data_dir / "events.jsonl"
    try:
        if path.stat().st_size >= config["max_log_bytes"]:
            rotated = data_dir / "events.previous.jsonl"
            os.replace(path, rotated)
    except FileNotFoundError:
        pass
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")


def cleanup(connection: sqlite3.Connection, cutoff: float) -> None:
    connection.execute("DELETE FROM scope_state WHERE last_seen < ?", (cutoff,))


def user_prompt_submit(
    connection: sqlite3.Connection,
    session_key: str,
) -> None:
    connection.execute("DELETE FROM scope_state WHERE session_key = ?", (session_key,))


def pre_tool_use(
    connection: sqlite3.Connection,
    event: dict[str, Any],
    secret: bytes,
    config: dict[str, Any],
    now: float,
    data_dir: Path,
) -> dict[str, Any] | None:
    session_key, scope_key, enforceable = scope_parts(event, secret)
    signature = tool_signature(event, secret)
    state = connection.execute(
        """
        SELECT tool_signature, consecutive_failures, last_seen, blocked_attempts
        FROM scope_state WHERE scope_key = ?
        """,
        (scope_key,),
    ).fetchone()
    if state is None:
        return None
    same_signature = state[0] == signature
    recent = now - float(state[2]) <= config["window_seconds"]
    threshold_met = int(state[1]) >= config["identical_failure_limit"]
    if not (same_signature and recent and threshold_met):
        return None
    blocked_attempts = int(state[3]) + 1
    connection.execute(
        "UPDATE scope_state SET blocked_attempts = ?, last_seen = ? WHERE scope_key = ?",
        (blocked_attempts, now, scope_key),
    )
    append_event(
        data_dir,
        config,
        {
            "at": int(now),
            "event": "repeat_block_candidate",
            "mode": config["mode"],
            "session": session_key,
            "tool": str(event.get("tool_name") or ""),
            "consecutive_failures": int(state[1]),
            "blocked_attempts": blocked_attempts,
            "enforceable": enforceable,
        },
    )
    if config["mode"] != "enforce" or not enforceable:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Loop Guard: this exact tool call already failed at least "
                f"{int(state[1])} consecutive times. Diagnose the recorded error and use a "
                "different strategy instead of repeating identical arguments."
            ),
        }
    }


def post_tool_use(
    connection: sqlite3.Connection,
    event: dict[str, Any],
    secret: bytes,
    config: dict[str, Any],
    now: float,
    data_dir: Path,
) -> dict[str, Any] | None:
    session_key, scope_key, enforceable = scope_parts(event, secret)
    signature = tool_signature(event, secret)
    failed, _ = explicit_failure(event.get("tool_response"))
    if not failed:
        connection.execute("DELETE FROM scope_state WHERE scope_key = ?", (scope_key,))
        return None
    failure_signature = digest(secret, "failure", event.get("tool_response"))
    state = connection.execute(
        """
        SELECT tool_signature, failure_signature, consecutive_failures, last_seen
        FROM scope_state WHERE scope_key = ?
        """,
        (scope_key,),
    ).fetchone()
    repeated = (
        state is not None
        and state[0] == signature
        and state[1] == failure_signature
        and now - float(state[3]) <= config["window_seconds"]
    )
    count = int(state[2]) + 1 if repeated else 1
    connection.execute(
        """
        INSERT INTO scope_state (
            scope_key, session_key, tool_signature, failure_signature,
            consecutive_failures, last_seen, blocked_attempts
        ) VALUES (?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(scope_key) DO UPDATE SET
            session_key = excluded.session_key,
            tool_signature = excluded.tool_signature,
            failure_signature = excluded.failure_signature,
            consecutive_failures = excluded.consecutive_failures,
            last_seen = excluded.last_seen,
            blocked_attempts = 0
        """,
        (scope_key, session_key, signature, failure_signature, count, now),
    )
    if count != config["identical_failure_limit"]:
        return None
    append_event(
        data_dir,
        config,
        {
            "at": int(now),
            "event": "repeat_failure_candidate",
            "mode": config["mode"],
            "session": session_key,
            "tool": str(event.get("tool_name") or ""),
            "consecutive_failures": count,
            "enforceable": enforceable,
        },
    )
    if config["mode"] != "enforce" or not enforceable:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "Loop Guard detected three consecutive failures from the exact same tool and "
                "arguments. Analyze the existing error, change strategy, and do not submit the "
                "identical call again."
            ),
        }
    }


def process_event(
    event: dict[str, Any],
    plugin_root: Path,
    data_dir: Path,
    now: float | None = None,
) -> dict[str, Any] | None:
    data_dir.mkdir(parents=True, exist_ok=True)
    config = load_config(plugin_root, data_dir)
    secret = load_or_create_secret(data_dir)
    current_time = time.time() if now is None else now
    connection = connect_database(data_dir)
    try:
        cleanup(connection, current_time - config["state_ttl_seconds"])
        session_key, _, _ = scope_parts(event, secret)
        event_name = event.get("hook_event_name")
        if event_name == "UserPromptSubmit":
            user_prompt_submit(connection, session_key)
            result = None
        elif event_name == "PreToolUse":
            result = pre_tool_use(
                connection, event, secret, config, current_time, data_dir
            )
        elif event_name == "PostToolUse":
            result = post_tool_use(
                connection, event, secret, config, current_time, data_dir
            )
        else:
            result = None
        connection.commit()
        return result
    finally:
        connection.close()


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        plugin_root_value = os.environ.get("PLUGIN_ROOT")
        data_dir_value = os.environ.get("PLUGIN_DATA")
        if not plugin_root_value or not data_dir_value:
            return 0
        result = process_event(
            event,
            Path(plugin_root_value),
            Path(data_dir_value),
        )
        if result is not None:
            json.dump(result, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
    except Exception:
        # Hooks must fail open. A diagnostic helper must never break a Codex task.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
