from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_creative_model_bridge_release as release_validator  # noqa: E402


class CreativeReleaseValidatorTests(unittest.TestCase):
    def test_checked_in_contract_matches_v014(self) -> None:
        self.assertEqual(release_validator.validate(ROOT, "creative-model-bridge-v0.1.4"), [])

    def test_wrong_tag_version_is_rejected(self) -> None:
        errors = release_validator.validate(ROOT, "creative-model-bridge-v0.1.5")
        self.assertTrue(any("tag version" in error for error in errors))

    def test_publish_step_final_verification_precedes_edit(self) -> None:
        workflow = (ROOT / ".github/workflows/release-creative-model-bridge.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(release_validator.validate_publish_step_structure(workflow), [])

    def test_publish_step_rejects_remote_mutation_after_final_verification(self) -> None:
        workflow = (ROOT / ".github/workflows/release-creative-model-bridge.yml").read_text(
            encoding="utf-8"
        )
        workflow = workflow.replace(
            "            verify_remote\n            gh release edit",
            "            verify_remote\n            gh release upload foo\n            gh release edit",
            1,
        )
        errors = release_validator.validate_publish_step_structure(workflow)
        self.assertTrue(any("next command" in error for error in errors))

    def test_build_setup_pixi_requires_plugin_manifest_path(self) -> None:
        workflow = (ROOT / ".github/workflows/release-creative-model-bridge.yml").read_text(
            encoding="utf-8"
        )
        missing = workflow.replace(
            "          manifest-path: plugins/creative-model-bridge/pixi.toml\n", "", 1
        )
        errors = release_validator.validate_build_pixi_manifest_path(missing)
        self.assertTrue(any("must set manifest-path" in error for error in errors))

    def test_build_setup_pixi_rejects_wrong_manifest_path(self) -> None:
        workflow = (ROOT / ".github/workflows/release-creative-model-bridge.yml").read_text(
            encoding="utf-8"
        )
        wrong = workflow.replace(
            "manifest-path: plugins/creative-model-bridge/pixi.toml",
            "manifest-path: pixi.toml",
            1,
        )
        errors = release_validator.validate_build_pixi_manifest_path(wrong)
        self.assertTrue(any("got pixi.toml" in error for error in errors))

    def test_pixi_lock_requires_all_platform_environments(self) -> None:
        lock = (ROOT / "plugins/creative-model-bridge/pixi.lock").read_text(encoding="utf-8")
        missing = lock.replace("      win-64:\n", "      win-64-missing:\n", 1)
        errors = release_validator.validate_pixi_lock_platforms(missing)
        self.assertTrue(any("missing or empty: win-64" in error for error in errors))

    def test_pixi_lock_requires_platform_specific_build_packages(self) -> None:
        lock = (ROOT / "plugins/creative-model-bridge/pixi.lock").read_text(encoding="utf-8")
        wrong = lock.replace(
            "https://conda.anaconda.org/conda-forge/linux-aarch64/pyinstaller-",
            "https://conda.anaconda.org/conda-forge/noarch/pyinstaller-",
            1,
        )
        errors = release_validator.validate_pixi_lock_platforms(wrong)
        self.assertTrue(any("linux-aarch64" in error and "pyinstaller" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
