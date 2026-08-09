from __future__ import annotations

import json
import re
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.write_fastctx_runtime_release import (
    LICENSE_PAYLOAD,
    MetadataError,
    TARGETS,
    generate,
    validate_final,
    validate_glibc_version_info,
    validate_release_archive,
)


class FastCtxRuntimeReleaseTests(unittest.TestCase):
    def test_generate_is_four_platform_digest_checked_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            for index, name in enumerate(TARGETS.values()):
                (assets / name).write_bytes(f"asset-{index}".encode())
            metadata = generate("0.2.5", "fastctx-v0.2.5", assets)
            validate_final(json.loads(json.dumps(metadata)))
            self.assertEqual(metadata["distribution"], "codex-plugin")
            self.assertFalse(metadata["transitional"])

    def test_rejects_cargo_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            for name in TARGETS.values():
                (assets / name).write_bytes(b"asset")
            manifest = root / "Cargo.toml"
            manifest.write_text("[package]\nname = 'fastctx'\nversion = '0.2.4'\n", encoding="utf-8")
            with self.assertRaisesRegex(MetadataError, "does not match Cargo"):
                generate("0.2.5", "fastctx-v0.2.5", assets, manifest)

    def test_generation_rejects_missing_asset_and_invalid_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            for name in list(TARGETS.values())[1:]:
                (assets / name).write_bytes(b"asset")
            with self.assertRaisesRegex(MetadataError, "missing regular"):
                generate("0.2.5", "fastctx-v0.2.5", assets)
            with self.assertRaisesRegex(MetadataError, "tag must"):
                generate("0.2.5", "v0.2.5", assets)

    def test_archive_validation_proves_tar_and_zip_license_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            for name in LICENSE_PAYLOAD:
                (payload / name).write_text(f"{name} contents\n", encoding="utf-8")
            (payload / "fastctx").write_bytes(b"unix binary")
            (payload / "fastctx.exe").write_bytes(b"windows binary")
            tar_path = root / "fastctx.tar.gz"
            with tarfile.open(tar_path, "w:gz") as archive:
                for name in ("fastctx", *LICENSE_PAYLOAD):
                    archive.add(payload / name, arcname=name)
            zip_path = root / "fastctx.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                for name in ("fastctx.exe", *LICENSE_PAYLOAD):
                    archive.write(payload / name, arcname=name)
            validate_release_archive(tar_path, "fastctx", payload)
            validate_release_archive(zip_path, "fastctx.exe", payload)
            nested_zip_path = root / "fastctx-nested.zip"
            with zipfile.ZipFile(nested_zip_path, "w") as archive:
                for name in ("fastctx.exe", *LICENSE_PAYLOAD):
                    archive.write(payload / name, arcname=f"package/{name}")
            with self.assertRaisesRegex(MetadataError, "missing regular file: fastctx.exe"):
                validate_release_archive(nested_zip_path, "fastctx.exe", payload)
            invalid_zip_path = root / "fastctx-invalid.zip"
            with zipfile.ZipFile(invalid_zip_path, "w") as archive:
                for name in ("fastctx.exe", *LICENSE_PAYLOAD):
                    content = "altered\n" if name == "NOTICE" else (payload / name).read_bytes()
                    archive.writestr(name, content)
            with self.assertRaisesRegex(MetadataError, "license payload differs: NOTICE"):
                validate_release_archive(invalid_zip_path, "fastctx.exe", payload)

    def test_glibc_version_validation_accepts_2_31_and_rejects_newer_versions(self) -> None:
        validate_glibc_version_info("Name: GLIBC_2.2.5\nName: GLIBC_2.31\nName: GLIBC_2.31.0\n")
        for version in ("2.32", "2.40", "2.100"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(MetadataError, rf"GLIBC_{re.escape(version)}"):
                    validate_glibc_version_info(f"Name: GLIBC_{version}\n")


if __name__ == "__main__":
    unittest.main()
