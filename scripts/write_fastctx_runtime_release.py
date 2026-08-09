#!/usr/bin/env python3
"""Generate or validate checked runtime metadata for the owned FastCtx release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any


TARGETS = {
    "aarch64-apple-darwin": "fastctx-aarch64-apple-darwin.tar.gz",
    "x86_64-apple-darwin": "fastctx-x86_64-apple-darwin.tar.gz",
    "x86_64-pc-windows-msvc": "fastctx-x86_64-pc-windows-msvc.zip",
    "x86_64-unknown-linux-gnu": "fastctx-x86_64-unknown-linux-gnu.tar.gz",
}
REPOSITORY = "dale0525/codex-plugins"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LICENSE_PAYLOAD = ("LICENSE-APACHE", "NOTICE", "THIRD_PARTY_LICENSES.md")
GLIBC_SYMBOL = re.compile(r"\bGLIBC_(\d+(?:\.\d+)+)\b")


class MetadataError(ValueError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cargo_version(manifest: Path) -> str:
    with manifest.open("rb") as handle:
        package = tomllib.load(handle).get("package")
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise MetadataError(f"Cargo package version is invalid: {manifest}")
    return version


def generate(version: str, tag: str, assets_dir: Path, cargo_manifest: Path | None = None) -> dict[str, Any]:
    if not SEMVER.fullmatch(version):
        raise MetadataError(f"version must be numeric semver: {version!r}")
    if tag != f"fastctx-v{version}":
        raise MetadataError(f"tag must be fastctx-v{version}: {tag!r}")
    if cargo_manifest is not None and cargo_version(cargo_manifest) != version:
        raise MetadataError("release version does not match Cargo.toml")
    assets: dict[str, dict[str, Any]] = {}
    for target, name in TARGETS.items():
        path = assets_dir / name
        if not path.is_file() or path.is_symlink():
            raise MetadataError(f"missing regular release asset: {path}")
        assets[target] = {
            "name": name,
            "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}",
            "size": path.stat().st_size,
            "sha256": _digest(path),
        }
    return {
        "schema_version": 2,
        "distribution": "codex-plugin",
        "transitional": False,
        "repository": REPOSITORY,
        "version": version,
        "tag": tag,
        "assets": assets,
    }


def validate_final(metadata: dict[str, Any], cargo_manifest: Path | None = None) -> None:
    if metadata.get("schema_version") != 2:
        raise MetadataError("schema_version must be 2")
    if metadata.get("distribution") != "codex-plugin" or metadata.get("transitional") is not False:
        raise MetadataError("metadata must be final codex-plugin metadata")
    if metadata.get("repository") != REPOSITORY:
        raise MetadataError("repository must be dale0525/codex-plugins")
    version = metadata.get("version")
    tag = metadata.get("tag")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise MetadataError("version must be numeric semver")
    if tag != f"fastctx-v{version}":
        raise MetadataError("tag must match version")
    if cargo_manifest is not None and cargo_version(cargo_manifest) != version:
        raise MetadataError("metadata version does not match Cargo.toml")
    assets = metadata.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(TARGETS):
        raise MetadataError("assets must contain exactly the four release targets")
    for target, name in TARGETS.items():
        asset = assets[target]
        if not isinstance(asset, dict):
            raise MetadataError(f"asset {target} must be an object")
        if asset.get("name") != name:
            raise MetadataError(f"asset {target} has the wrong name")
        expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        if asset.get("url") != expected_url:
            raise MetadataError(f"asset {target} has the wrong URL")
        if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
            raise MetadataError(f"asset {target} has an invalid size")
        if not isinstance(asset.get("sha256"), str) or not SHA256.fullmatch(asset["sha256"]):
            raise MetadataError(f"asset {target} has an invalid SHA-256")


def validate_release_archive(archive: Path, binary: str, payload_dir: Path) -> None:
    """Verify the distributable contains its executable and exact license payload."""
    expected = (binary, *LICENSE_PAYLOAD)
    expected_payload = {
        name: (payload_dir / name).read_bytes()
        for name in LICENSE_PAYLOAD
    }
    if archive.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(archive, "r:gz") as packaged:
            members = {member.name: member for member in packaged.getmembers()}
            for name in expected:
                member = members.get(name)
                if member is None or not member.isfile():
                    raise MetadataError(f"archive missing regular file: {name}")
            for name, content in expected_payload.items():
                handle = packaged.extractfile(members[name])
                if handle is None or handle.read() != content:
                    raise MetadataError(f"archive license payload differs: {name}")
        return
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as packaged:
            members = {member.filename: member for member in packaged.infolist()}
            for name in expected:
                member = members.get(name)
                if member is None or member.is_dir():
                    raise MetadataError(f"archive missing regular file: {name}")
            for name, content in expected_payload.items():
                if packaged.read(name) != content:
                    raise MetadataError(f"archive license payload differs: {name}")
        return
    raise MetadataError(f"unsupported release archive: {archive}")


def validate_glibc_version_info(version_info: str) -> None:
    """Reject Linux symbol versions newer than the GLIBC_2.31 baseline."""
    versions = []
    for match in GLIBC_SYMBOL.finditer(version_info):
        version = tuple(int(part) for part in match.group(1).split("."))
        while len(version) > 2 and version[-1] == 0:
            version = version[:-1]
        versions.append(version)
    if not versions:
        raise MetadataError("readelf output contains no GLIBC symbol versions")
    too_new = sorted({version for version in versions if version > (2, 31)})
    if too_new:
        rendered = ", ".join(
            "GLIBC_" + ".".join(str(part) for part in version)
            for version in too_new
        )
        raise MetadataError(
            "FastCtx Linux executable exceeds the GLIBC_2.31 compatibility floor: "
            + rendered
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--tag")
    parser.add_argument("--assets-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--cargo-manifest", type=Path)
    parser.add_argument("--check-archive", type=Path)
    parser.add_argument("--check-glibc-version-info", action="store_true")
    parser.add_argument("--binary")
    parser.add_argument("--payload-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.check_glibc_version_info:
            validate_glibc_version_info(sys.stdin.read())
        elif args.check_archive is not None:
            if args.binary is None or args.payload_dir is None:
                parser.error("archive validation requires --binary and --payload-dir")
            validate_release_archive(args.check_archive, args.binary, args.payload_dir)
        elif args.check is not None:
            validate_final(json.loads(args.check.read_text(encoding="utf-8")), args.cargo_manifest)
        else:
            if not all((args.version, args.tag, args.assets_dir, args.output)):
                parser.error("generation requires --version, --tag, --assets-dir, and --output")
            metadata = generate(args.version, args.tag, args.assets_dir, args.cargo_manifest)
            args.output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, MetadataError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
