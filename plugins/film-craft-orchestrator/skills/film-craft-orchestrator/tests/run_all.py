#!/usr/bin/env python3
"""Run the complete deterministic validation suite for this skill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (
    [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "validate_corpus.py"),
        str(SKILL_ROOT),
    ],
    [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "validate_model_adapters.py"),
        str(SKILL_ROOT / "references" / "model-adapters.json"),
    ],
    [sys.executable, str(SKILL_ROOT / "tests" / "run_ai_video_fixtures.py")],
    [sys.executable, str(SKILL_ROOT / "tests" / "run_staged_compiler_fixtures.py")],
    [
        sys.executable,
        str(SKILL_ROOT / "tests" / "run_capability_packages.py"),
        "--output-name",
        "retest-output",
        "--log-name",
        "retest-validator.log",
    ],
)


def main() -> int:
    for command in COMMANDS:
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    print("OK: complete film-craft-orchestrator validation suite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
