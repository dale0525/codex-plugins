#!/usr/bin/env python3
"""Strictly validate portable UTF-8 CSV structure and selected columns."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--required-column", action="append", default=[])
    parser.add_argument("--unique-column")
    args = parser.parse_args()
    errors: list[str] = []

    try:
        raw = args.path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"ERROR: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    for match in re.finditer(r",[ \t]+\"", raw):
        line = raw.count("\n", 0, match.start()) + 1
        errors.append(f"line {line}: whitespace before quoted field is not portable CSV")

    try:
        rows = list(csv.reader(raw.splitlines(), strict=True))
    except csv.Error as exc:
        errors.append(f"CSV parse error: {exc}")
        rows = []

    if not rows:
        errors.append("CSV has no rows")
    else:
        header = rows[0]
        if not header or any(not value for value in header):
            errors.append("header contains an empty column")
        if len(header) != len(set(header)):
            errors.append("header contains duplicate columns")
        for required in args.required_column:
            if required not in header:
                errors.append(f"missing required column: {required}")
        for line, row in enumerate(rows[1:], start=2):
            if len(row) != len(header):
                errors.append(f"line {line}: expected {len(header)} columns, got {len(row)}")

        if args.unique_column and args.unique_column in header:
            column = header.index(args.unique_column)
            seen: set[str] = set()
            for line, row in enumerate(rows[1:], start=2):
                if len(row) <= column:
                    continue
                value = row[column]
                if not value:
                    errors.append(f"line {line}: {args.unique_column} is empty")
                elif value in seen:
                    errors.append(f"line {line}: duplicate {args.unique_column} {value}")
                seen.add(value)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: {args.path} ({len(rows) - 1} data rows, {len(rows[0])} columns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
