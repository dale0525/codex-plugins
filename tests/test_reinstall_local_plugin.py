from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import reinstall_local_plugin


class ReinstallLocalPluginTests(unittest.TestCase):
    def test_pixi_environment_is_hidden_during_install_and_restored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="plugin-reinstall-test-") as temporary:
            plugin = Path(temporary) / "plugin"
            environment = plugin / ".pixi"
            environment.mkdir(parents=True)
            marker = environment / "marker"
            marker.write_text("original", encoding="utf-8")

            def runner(command: tuple[str, ...]) -> None:
                self.assertEqual(
                    command,
                    ("codex", "plugin", "add", "plugin@marketplace"),
                )
                self.assertFalse(environment.exists())

            reinstall_local_plugin.reinstall(
                plugin,
                "plugin@marketplace",
                runner,
            )

            self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_pixi_environment_is_restored_when_install_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="plugin-reinstall-test-") as temporary:
            plugin = Path(temporary) / "plugin"
            environment = plugin / ".pixi"
            environment.mkdir(parents=True)

            def runner(_command: tuple[str, ...]) -> None:
                raise RuntimeError("install failed")

            with self.assertRaisesRegex(RuntimeError, "install failed"):
                reinstall_local_plugin.reinstall(
                    plugin,
                    "plugin@marketplace",
                    runner,
                )

            self.assertTrue(environment.is_dir())

    def test_original_environment_is_preserved_when_install_creates_one(self) -> None:
        with tempfile.TemporaryDirectory(prefix="plugin-reinstall-test-") as temporary:
            plugin = Path(temporary) / "plugin"
            environment = plugin / ".pixi"
            environment.mkdir(parents=True)
            (environment / "marker").write_text("original", encoding="utf-8")

            def runner(_command: tuple[str, ...]) -> None:
                environment.mkdir()
                (environment / "marker").write_text("new", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "retained the new one at") as error:
                reinstall_local_plugin.reinstall(
                    plugin,
                    "plugin@marketplace",
                    runner,
                )

            self.assertEqual(
                (environment / "marker").read_text(encoding="utf-8"),
                "original",
            )
            retained = Path(str(error.exception).rsplit(" at ", 1)[1])
            self.assertEqual(
                (retained / "marker").read_text(encoding="utf-8"),
                "new",
            )


if __name__ == "__main__":
    unittest.main()
