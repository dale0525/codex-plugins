from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_creative_model_bridge_release as release_validator  # noqa: E402


class CreativeReleaseValidatorTests(unittest.TestCase):
    def test_checked_in_contract_matches_v013(self) -> None:
        self.assertEqual(release_validator.validate(ROOT, "creative-model-bridge-v0.1.3"), [])

    def test_wrong_tag_version_is_rejected(self) -> None:
        errors = release_validator.validate(ROOT, "creative-model-bridge-v9.9.9")
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


if __name__ == "__main__":
    unittest.main()
