#!/usr/bin/env python3
"""Run the checked-in MCP command against a local release binary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if binary is None:
        raise SystemExit("usage: smoke_creative_model_bridge.py BINARY")
    command = [str(binary)]
    payload = "\n".join(
        [
            '{"jsonrpc":"2.0","id":1,"method":"initialize"}',
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
            '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"creative_preview","arguments":{"task":"offline smoke","model":"smoke-model"}}}',
            "",
        ]
    )
    environment = os.environ.copy()
    environment["CREATIVE_MODEL_BRIDGE_BIN"] = str(binary)
    environment["CREATIVE_MODEL_BRIDGE_OFFLINE"] = "1"
    with tempfile.TemporaryDirectory(prefix="creative-smoke-") as home:
        environment["CODEX_HOME"] = home
        result = subprocess.run(
            command,
            cwd=binary.parent,
            input=payload,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
            timeout=90,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return result.returncode or 1
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if len(responses) != 3 or [item.get("id") for item in responses] != [1, 2, 3]:
            print(f"unexpected MCP smoke output: {result.stdout}", file=sys.stderr)
            return 1
        if "creative_preview" not in result.stdout:
            print("tools/list did not expose creative_preview", file=sys.stderr)
            return 1
        setup = subprocess.run([str(binary), "provision", "setup", "--yes"], capture_output=True, text=True, env=environment, timeout=90)
        if setup.returncode != 0:
            print(setup.stderr, file=sys.stderr)
            return setup.returncode or 1
        state_path = Path(home) / "creative-model-bridge" / "provision-state.json"
        config_path = Path(home) / "config.toml"
        if not state_path.is_file() or not config_path.is_file():
            print("provision setup did not create config/state", file=sys.stderr)
            return 1
        status = subprocess.run([str(binary), "provision", "status"], capture_output=True, text=True, env=environment, timeout=90)
        if status.returncode != 0 or '"status": "installed"' not in status.stdout:
            print(status.stderr or status.stdout, file=sys.stderr)
            return status.returncode or 1
        uninstall = subprocess.run([str(binary), "provision", "uninstall"], capture_output=True, text=True, env=environment, timeout=90)
        if uninstall.returncode != 0 or "mcp_servers.creative-model-bridge" in config_path.read_text(encoding="utf-8"):
            print(uninstall.stderr or "uninstall did not remove config", file=sys.stderr)
            return uninstall.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
