#!/usr/bin/env python3
"""Build the self-contained bridge executable with the locked Pixi toolchain."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--asset-name", default="creative-model-bridge")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / ".pyinstaller-work"
    spec_dir = output_dir / ".pyinstaller-spec"
    for path in (work_dir, spec_dir):
        if path.exists():
            shutil.rmtree(path)
    command = [
        "python",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        args.asset_name,
        "--distpath",
        str(output_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(ROOT / "mcp"),
        "--hidden-import",
        "bridge",
        "--hidden-import",
        "provision",
        str(ROOT / "mcp/server.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    executable = output_dir / args.asset_name
    if not executable.is_file() and not executable.with_name(executable.name + ".exe").is_file():
        raise SystemExit(f"PyInstaller did not produce {executable} or its Windows .exe form")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
