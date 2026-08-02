from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_creative_model_bridge_release as release_validator  # noqa: E402


class CreativeReleaseValidatorTests(unittest.TestCase):
    def test_checked_in_contract_matches_v015(self) -> None:
        self.assertEqual(release_validator.validate(ROOT, "creative-model-bridge-v0.1.6"), [])

    def test_wrong_tag_version_is_rejected(self) -> None:
        errors = release_validator.validate(ROOT, "creative-model-bridge-v0.1.7")
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

    def test_windows_accepts_tracked_100755_with_zero_worktree_mode(self) -> None:
        bootstrap = ROOT / "plugins/creative-model-bridge/scripts/bootstrap.sh"
        calls: list[tuple[list[str], dict[str, object]]] = []

        def git_runner(command: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append((command, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout="100755 deadbeef 0\tplugins/creative-model-bridge/scripts/bootstrap.sh\n",
            )

        errors = release_validator.validate_bootstrap_tracking(
            ROOT,
            bootstrap,
            platform_name="nt",
            git_runner=git_runner,
            stat_fn=lambda _: SimpleNamespace(st_mode=0),
        )
        self.assertEqual(errors, [])
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(calls[0][1]["cwd"], ROOT)

    def test_windows_rejects_tracked_100644(self) -> None:
        bootstrap = ROOT / "plugins/creative-model-bridge/scripts/bootstrap.sh"
        runner = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="100644 deadbeef 0\tplugins/creative-model-bridge/scripts/bootstrap.sh\n",
        )
        errors = release_validator.validate_bootstrap_tracking(
            ROOT, bootstrap, platform_name="nt", git_runner=runner, stat_fn=lambda _: SimpleNamespace(st_mode=0)
        )
        self.assertTrue(any("mode must be 100755" in error for error in errors))

    def test_posix_requires_tracked_and_worktree_execute_bits(self) -> None:
        bootstrap = ROOT / "plugins/creative-model-bridge/scripts/bootstrap.sh"
        runner = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="100755 deadbeef 0\tplugins/creative-model-bridge/scripts/bootstrap.sh\n",
        )
        passing = release_validator.validate_bootstrap_tracking(
            ROOT,
            bootstrap,
            platform_name="posix",
            git_runner=runner,
            stat_fn=lambda _: SimpleNamespace(st_mode=0o755),
        )
        failing = release_validator.validate_bootstrap_tracking(
            ROOT,
            bootstrap,
            platform_name="posix",
            git_runner=runner,
            stat_fn=lambda _: SimpleNamespace(st_mode=0o644),
        )
        self.assertEqual(passing, [])
        self.assertTrue(any("working-tree mode must be executable" in error for error in failing))

    def test_git_missing_duplicate_and_failure_fail_closed(self) -> None:
        bootstrap = ROOT / "plugins/creative-model-bridge/scripts/bootstrap.sh"
        cases = (
            (0, "", "tracked exactly once"),
            (0, "100755 a 0\tplugins/creative-model-bridge/scripts/bootstrap.sh\n100755 b 0\tplugins/creative-model-bridge/scripts/bootstrap.sh\n", "duplicate"),
            (1, "", "git ls-files bootstrap check failed"),
        )
        for returncode, stdout, expected in cases:
            runner = lambda *_args, _returncode=returncode, _stdout=stdout, **_kwargs: SimpleNamespace(
                returncode=_returncode, stdout=_stdout
            )
            errors = release_validator.validate_bootstrap_tracking(
                ROOT,
                bootstrap,
                platform_name="nt",
                git_runner=runner,
                stat_fn=lambda _: SimpleNamespace(st_mode=0),
            )
            self.assertTrue(any(expected in error for error in errors), (returncode, stdout, errors))

    def test_provisioner_default_version_mismatch_fails(self) -> None:
        text = (ROOT / "plugins/creative-model-bridge/scripts/provision.ps1").read_text(encoding="utf-8")
        mismatched = text.replace("else { '0.1.6' }", "else { '0.1.7' }")
        errors = release_validator.validate_provisioner_contract(mismatched, "0.1.6")
        self.assertTrue(any("default version" in error for error in errors))

    def test_provision_version_mismatch_fails(self) -> None:
        text = (ROOT / "plugins/creative-model-bridge/mcp/provision.py").read_text(encoding="utf-8")
        mismatched = text.replace('PROVISION_VERSION = "0.1.6"', 'PROVISION_VERSION = "0.1.7"', 1)
        errors = release_validator.validate_provision_version(mismatched, "0.1.6")
        self.assertTrue(any("PROVISION_VERSION" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
