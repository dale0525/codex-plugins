#!/usr/bin/env python3
"""Reinstall a local Codex plugin without copying its Pixi environment."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path


Runner = Callable[[Sequence[str]], object]


def reinstall(plugin_path: Path, plugin_reference: str, runner: Runner) -> None:
    """Hide the local Pixi environment while Codex snapshots the plugin."""
    plugin_path = plugin_path.resolve(strict=True)
    pixi_environment = plugin_path / ".pixi"

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{plugin_path.name}-reinstall-",
            dir=plugin_path.parent,
        )
    )
    held_environment = temporary / ".pixi"
    environment_was_present = pixi_environment.exists()
    if environment_was_present:
        os.replace(pixi_environment, held_environment)

    try:
        runner(("codex", "plugin", "add", plugin_reference))
    finally:
        if environment_was_present:
            if pixi_environment.exists():
                conflicting_environment = temporary / ".pixi-created-during-install"
                os.replace(pixi_environment, conflicting_environment)
                os.replace(held_environment, pixi_environment)
                raise RuntimeError(
                    "restored the original Pixi environment and retained the "
                    f"new one at {conflicting_environment}"
                )
            os.replace(held_environment, pixi_environment)
        temporary.rmdir()


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_path", type=Path)
    parser.add_argument("plugin_reference", help="PLUGIN@MARKETPLACE")
    args = parser.parse_args()
    reinstall(args.plugin_path, args.plugin_reference, _run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
