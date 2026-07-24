#!/usr/bin/env python3
"""Validate all six AI-video capability-test production packages."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent / "capabilities"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_ai_video_package.py"
ADAPTERS = SKILL_ROOT / "references" / "model-adapters.json"
TEST_IDS = (
    "dialogue_2p",
    "mystery_reveal",
    "visual_comedy",
    "action_multi",
    "montage",
    "novel_adaptation",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-name", default="raw-output")
    parser.add_argument("--log-name", default="validator.log")
    args = parser.parse_args()
    failed: list[str] = []
    for test_id in TEST_IDS:
        test_dir = TEST_ROOT / test_id
        package = test_dir / args.output_name
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                str(package),
                "--adapters",
                str(ADAPTERS),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        log = (result.stdout + result.stderr).replace(str(SKILL_ROOT), "<SKILL_ROOT>")
        test_dir.mkdir(parents=True, exist_ok=True)
        (test_dir / args.log_name).write_text(log, encoding="utf-8")
        if result.returncode:
            failed.append(test_id)
            print(f"FAIL {test_id}")
        else:
            print(f"PASS {test_id}")
    if failed:
        print(f"ERROR: capability packages failed: {', '.join(failed)}")
        return 1
    print(f"OK: {len(TEST_IDS)} capability packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
