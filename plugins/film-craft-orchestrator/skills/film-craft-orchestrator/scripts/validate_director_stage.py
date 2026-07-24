#!/usr/bin/env python3
"""Validate frozen director/visual inputs before deterministic compilation."""

from __future__ import annotations

import argparse
from pathlib import Path

from stage_validation_common import print_result, validate_director


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    return print_result("director", validate_director(args.package_dir.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
