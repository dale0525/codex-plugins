#!/usr/bin/env python3
"""Validate the frozen story stage and its independent semantic review."""

from __future__ import annotations

import argparse
from pathlib import Path

from stage_validation_common import print_result, validate_story


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    return print_result("story", validate_story(args.package_dir.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
