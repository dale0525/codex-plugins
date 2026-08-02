from __future__ import annotations

import json
import base64
import os
from pathlib import Path
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
import sys

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "mcp"))
import provision  # noqa: E402


class ProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="creative-provision-")
        self.home = Path(self.temp.name) / "codex-home"
        self.home.mkdir()
        self.binary = self.home / "bridge.exe"
        self.binary.write_bytes(b"bridge")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _wal_fixture(self) -> dict[str, object]:
        before_config = b"title = 'before'\n"
        after_config = b"title = 'partial'\n"
        return {
            "schema_version": 2,
            "phase": "config_written",
            "operation": "setup",
            "config_exists": True,
            "state_exists": False,
            "config_before": base64.b64encode(before_config).decode(),
            "config_after": base64.b64encode(after_config).decode(),
            "state_before": "",
            "state_after": "",
            "config_before_digest": provision._digest(before_config),
            "config_after_digest": provision._digest(after_config),
            "state_before_digest": provision._digest(b""),
            "state_after_digest": provision._digest(b""),
        }

    def _write_wal(self, wal: dict[str, object], home: Path | None = None) -> bytes:
        home = home or self.home
        provision.wal_path(home).parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(wal, sort_keys=True) + "\n").encode("utf-8")
        provision.wal_path(home).write_bytes(raw)
        return raw

    def _recovery_wal(
        self,
        home: Path,
        *,
        config_exists: bool,
        state_exists: bool,
        shape: str,
    ) -> tuple[bytes, bytes, bytes, bytes]:
        config_before = b"config-before\n" if config_exists else b""
        state_before = b"state-before\n" if state_exists else b""
        config_after = b"config-after\n"
        state_after = b"state-after\n"
        if shape == "config-after/state-before":
            config = config_after
            state = state_before if state_exists else None
            phase = "config_written"
        elif shape == "config-before/state-after":
            config = config_before if config_exists else None
            state = state_after
            phase = "state_written"
        else:
            raise AssertionError(f"unknown crash shape: {shape}")
        config_path = home / "config.toml"
        if config is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(config)
        state_path = provision.state_path(home)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if state is None:
            state_path.unlink(missing_ok=True)
        else:
            state_path.write_bytes(state)
        wal = {
            "schema_version": 2,
            "phase": phase,
            "operation": "setup",
            "config_exists": config_exists,
            "state_exists": state_exists,
            "config_before": base64.b64encode(config_before).decode(),
            "config_after": base64.b64encode(config_after).decode(),
            "state_before": base64.b64encode(state_before).decode(),
            "state_after": base64.b64encode(state_after).decode(),
            "config_before_digest": provision._digest(config_before),
            "config_after_digest": provision._digest(config_after),
            "state_before_digest": provision._digest(state_before),
            "state_after_digest": provision._digest(state_after),
        }
        self._write_wal(wal, home)
        return config_before, config_after, state_before, state_after

    def test_wal_recovery_restores_bytes_or_absence_for_all_before_images_and_crash_shapes(self) -> None:
        for config_exists, state_exists in ((True, True), (True, False), (False, True), (False, False)):
            flags = ("T" if config_exists else "F") + ("T" if state_exists else "F")
            for shape in ("config-after/state-before", "config-before/state-after"):
                with self.subTest(before=flags, shape=shape):
                    home = Path(self.temp.name) / f"recovery-{flags}-{shape.split('/')[0]}"
                    home.mkdir()
                    config_before, _, state_before, _ = self._recovery_wal(
                        home,
                        config_exists=config_exists,
                        state_exists=state_exists,
                        shape=shape,
                    )
                    provision._recover(home)

                    config_path = home / "config.toml"
                    state_path = provision.state_path(home)
                    if config_exists:
                        self.assertEqual(config_path.read_bytes(), config_before)
                    else:
                        self.assertFalse(config_path.exists())
                    if state_exists:
                        self.assertEqual(state_path.read_bytes(), state_before)
                    else:
                        self.assertFalse(state_path.exists())
                    self.assertFalse(provision.wal_path(home).exists())
                    journal_lines = provision.journal_path(home).read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(journal_lines), 1)
                    self.assertEqual(json.loads(journal_lines[0])["event"], "recovery")

    def _assert_damaged_wal_is_fail_closed(self, field: str) -> None:
        (self.home / "config.toml").write_bytes(b"title = 'partial'\n")
        wal = self._wal_fixture()
        wal[field] = base64.b64encode(b"tampered\n").decode()
        wal_raw = self._write_wal(wal)
        config_before = (self.home / "config.toml").read_bytes()
        state_before = provision.state_path(self.home).read_bytes() if provision.state_path(self.home).exists() else None
        journal_before = provision.journal_path(self.home).read_bytes() if provision.journal_path(self.home).exists() else None
        with self.assertRaises(provision.ManualRecovery):
            provision.setup(home=self.home)
        self.assertEqual(provision.wal_path(self.home).read_bytes(), wal_raw)
        self.assertEqual((self.home / "config.toml").read_bytes(), config_before)
        self.assertEqual(provision.state_path(self.home).read_bytes() if provision.state_path(self.home).exists() else None, state_before)
        self.assertEqual(provision.journal_path(self.home).read_bytes() if provision.journal_path(self.home).exists() else None, journal_before)

    def test_wal_payload_digest_tampering_fails_closed_without_writes(self) -> None:
        for field in ("config_before", "config_after", "state_before", "state_after"):
            with self.subTest(field=field):
                self._assert_damaged_wal_is_fail_closed(field)

    def test_wal_malformed_base64_invalid_digest_and_absent_nonempty_fail_closed(self) -> None:
        cases = ("malformed_base64", "invalid_digest", "absent_nonempty")
        for case in cases:
            with self.subTest(case=case):
                (self.home / "config.toml").write_bytes(b"title = 'partial'\n")
                wal = self._wal_fixture()
                if case == "malformed_base64":
                    wal["config_after"] = "%%%"
                elif case == "invalid_digest":
                    wal["config_after_digest"] = "not-a-digest"
                else:
                    wal["state_before"] = base64.b64encode(b"unexpected").decode()
                wal_raw = self._write_wal(wal)
                config_before = (self.home / "config.toml").read_bytes()
                with self.assertRaises(provision.ManualRecovery):
                    provision.setup(home=self.home)
                self.assertEqual(provision.wal_path(self.home).read_bytes(), wal_raw)
                self.assertEqual((self.home / "config.toml").read_bytes(), config_before)
                self.assertFalse(provision.state_path(self.home).exists())
                self.assertFalse(provision.journal_path(self.home).exists())

    def test_healthy_wal_recovery_still_restores_before_image(self) -> None:
        (self.home / "config.toml").write_bytes(b"title = 'partial'\n")
        self._write_wal(self._wal_fixture())
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            provision.setup(home=self.home)
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old
        self.assertIn(b"title = 'before'", (self.home / "config.toml").read_bytes())
        self.assertFalse(provision.wal_path(self.home).exists())
        self.assertIn('"event": "recovery"', provision.journal_path(self.home).read_text(encoding="utf-8"))

    def test_setup_status_repair_and_uninstall(self) -> None:
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            installed = provision.setup(home=self.home)
            self.assertEqual(installed["status"], "installed")
            config = (self.home / "config.toml").read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.creative-model-bridge]", config)
            self.assertEqual(provision.status(home=self.home)["status"], "installed")
            repaired = provision.run("repair", home=self.home)
            self.assertEqual(repaired["install_id"], installed["install_id"])
            removed = provision.uninstall(home=self.home)
            self.assertEqual(removed["status"], "uninstalled")
            self.assertNotIn("mcp_servers.creative-model-bridge", (self.home / "config.toml").read_text(encoding="utf-8"))
            (self.home / "config.toml").write_text("outside = true\n", encoding="utf-8")
            self.assertEqual(provision.status(home=self.home)["status"], "uninstalled")
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_foreign_same_name_is_hard_failure(self) -> None:
        (self.home / "config.toml").write_text(
            "[mcp_servers.creative-model-bridge]\ncommand = 'someone-else'\n", encoding="utf-8"
        )
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            with self.assertRaises(provision.ProvisionError):
                provision.setup(home=self.home)
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_state_and_journal_are_machine_readable(self) -> None:
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            provision.setup(home=self.home)
            state = json.loads(provision.state_path(self.home).read_text(encoding="utf-8"))
            self.assertEqual(state["schema_version"], 2)
            self.assertIn('"event": "setup"', provision.journal_path(self.home).read_text(encoding="utf-8"))
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_dynamic_provider_env_status_and_drift(self) -> None:
        (self.home / "config.toml").write_text(
            '[shell_environment_policy.set]\nCREATIVE_MODEL_PROVIDER = "p"\n'
            '[model_providers.p]\nenv_key = "MY_PROVIDER_KEY"\n', encoding="utf-8"
        )
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            provision.setup(home=self.home)
            config = (self.home / "config.toml").read_text(encoding="utf-8")
            self.assertIn('"MY_PROVIDER_KEY"', config)
            self.assertEqual(provision.status(home=self.home)["status"], "installed")
            (self.home / "config.toml").write_text(config + "# outside edit\n", encoding="utf-8")
            self.assertEqual(provision.status(home=self.home)["status"], "installed")
            provision.uninstall(home=self.home)
            self.assertIn("# outside edit", (self.home / "config.toml").read_text(encoding="utf-8"))
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_setup_and_repair_healthy_are_exact_noops_and_repair_preserves_outside_edits(self) -> None:
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            provision.setup(home=self.home)
            config = self.home / "config.toml"
            state = provision.state_path(self.home)
            journal = provision.journal_path(self.home)
            snapshot = (config.read_bytes(), state.read_bytes(), journal.read_bytes(), config.stat().st_mtime_ns, state.stat().st_mtime_ns, journal.stat().st_mtime_ns)
            time.sleep(0.01)
            provision.setup(home=self.home)
            self.assertEqual(snapshot, (config.read_bytes(), state.read_bytes(), journal.read_bytes(), config.stat().st_mtime_ns, state.stat().st_mtime_ns, journal.stat().st_mtime_ns))
            text = config.read_text(encoding="utf-8")
            config.write_text(text.replace('args = []', 'args = ["drift"]') + "outside = true\n", encoding="utf-8")
            self.assertEqual(provision.status(home=self.home)["status"], "drift")
            with self.assertRaises(provision.ProvisionError):
                provision.setup(home=self.home)
            repaired = provision.setup(home=self.home, repair=True)
            self.assertEqual(repaired["status"], "installed")
            repaired_text = config.read_text(encoding="utf-8")
            self.assertIn("outside = true", repaired_text)
            self.assertIn("args = []", repaired_text)
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_injected_failure_rolls_back_config_and_state(self) -> None:
        original = "title = 'keep'\n"
        (self.home / "config.toml").write_text(original, encoding="utf-8")
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        provision.os.environ["CREATIVE_MODEL_BRIDGE_TEST_FAIL_AFTER_CONFIG"] = "1"
        try:
            with self.assertRaises(provision.ProvisionError):
                provision.setup(home=self.home)
            self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original)
            self.assertFalse(provision.state_path(self.home).exists())
            self.assertFalse(provision.wal_path(self.home).exists())
        finally:
            provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_TEST_FAIL_AFTER_CONFIG", None)
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_malformed_duplicate_block_is_rejected(self) -> None:
        block = '# creative-model-bridge:begin schema=1 install_id="bad"\n'
        (self.home / "config.toml").write_text(block + block, encoding="utf-8")
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            with self.assertRaises(provision.ProvisionError):
                provision.setup(home=self.home)
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_marker_pair_requires_one_equal_uuid_pair(self) -> None:
        valid = "123e4567-e89b-12d3-a456-426614174000"
        cases = (
            f'# creative-model-bridge:begin schema=1 install_id="{valid}"\n',
            f'# creative-model-bridge:end install_id="{valid}"\n',
            f'# creative-model-bridge:begin schema=1 install_id="{valid}"\n# creative-model-bridge:end install_id="123e4567-e89b-12d3-a456-426614174001"\n',
            f'# creative-model-bridge:begin schema=1 install_id="{valid}"\n# creative-model-bridge:begin schema=1 install_id="{valid}"\n',
            f'# creative-model-bridge:begin schema=1 install_id="{valid}"\n# creative-model-bridge:end install_id="{valid}"\n# creative-model-bridge:end install_id="{valid}"\n',
            f'# creative-model-bridge:begin schema=1 install_id="{valid}"\n# nested # creative-model-bridge:end install_id="{valid}"\n',
        )
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(provision.ProvisionError):
                    provision._marker(text)

    def test_startup_recovers_prepared_wal_before_new_operation(self) -> None:
        original = "title = 'before'\n"
        (self.home / "config.toml").write_text("title = 'partial'\n", encoding="utf-8")
        wal = {
            "schema_version": 2, "phase": "config_written", "operation": "setup",
            "config_exists": True,
            "config_before": base64.b64encode(original.encode()).decode(),
            "config_after": base64.b64encode(b"title = 'partial'\n").decode(),
            "state_exists": False, "state_before": "", "state_after": base64.b64encode(b"").decode(),
            "config_before_digest": provision._digest(original.encode()), "state_before_digest": provision._digest(b""),
            "config_after_digest": provision._digest(b"title = 'partial'\n"), "state_after_digest": provision._digest(b""),
        }
        provision.wal_path(self.home).parent.mkdir(parents=True, exist_ok=True)
        provision.wal_path(self.home).write_text(json.dumps(wal), encoding="utf-8")
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            provision.setup(home=self.home)
            self.assertIn("title = 'before'", (self.home / "config.toml").read_text(encoding="utf-8"))
            self.assertFalse(provision.wal_path(self.home).exists())
            self.assertIn('"event": "recovery"', provision.journal_path(self.home).read_text(encoding="utf-8"))
        finally:
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_unknown_wal_is_retained_and_status_requires_manual_recovery(self) -> None:
        provision.wal_path(self.home).parent.mkdir(parents=True, exist_ok=True)
        provision.wal_path(self.home).write_text(json.dumps({"schema_version": 2, "phase": "manual_required"}), encoding="utf-8")
        result = provision.status(home=self.home)
        self.assertEqual(result["status"], "pending_manual_recovery")
        self.assertTrue(provision.wal_path(self.home).is_file())
        with self.assertRaises(provision.ManualRecovery):
            provision.setup(home=self.home)

    def test_unknown_external_edit_leaves_manual_wal_without_state_write(self) -> None:
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        provision.os.environ["CREATIVE_MODEL_BRIDGE_TEST_EXTERNAL_CONFIG_EDIT"] = "1"
        try:
            with self.assertRaises(provision.ManualRecovery):
                provision.setup(home=self.home)
            self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), "external edit\n")
            self.assertFalse(provision.state_path(self.home).exists())
            self.assertEqual(provision.status(home=self.home)["status"], "pending_manual_recovery")
        finally:
            provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_TEST_EXTERNAL_CONFIG_EDIT", None)
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old

    def test_stale_lock_recovery_live_owner_protection_and_concurrent_writers(self) -> None:
        old = provision.os.environ.get("CREATIVE_MODEL_BRIDGE_EXECUTABLE")
        provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = str(self.binary)
        try:
            lock = provision.lock_path(self.home)
            lock.mkdir(parents=True)
            (lock / "owner.999999.dead").write_text("pid=999999\n", encoding="utf-8")
            stale = time.time() - 600
            os.utime(lock, (stale, stale))
            provision.setup(home=self.home)
            self.assertTrue(list(provision.state_root(self.home).glob("retired-locks/stale.*")))
            live = provision.lock_path(self.home)
            live.mkdir(parents=True)
            (live / ("owner." + str(os.getpid()) + ".live")).write_text(f"pid={os.getpid()}\n", encoding="utf-8")
            provision.os.environ["CREATIVE_MODEL_BRIDGE_LOCK_MAX_ATTEMPTS"] = "2"
            with self.assertRaises(provision.ProvisionError):
                provision.setup(home=self.home)
            self.assertTrue(live.exists())
            provision.shutil.rmtree(live)
            provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_LOCK_MAX_ATTEMPTS", None)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: provision.setup(home=self.home)["status"], range(2)))
            self.assertEqual(results, ["installed", "installed"])
        finally:
            provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_LOCK_MAX_ATTEMPTS", None)
            if old is None:
                provision.os.environ.pop("CREATIVE_MODEL_BRIDGE_EXECUTABLE", None)
            else:
                provision.os.environ["CREATIVE_MODEL_BRIDGE_EXECUTABLE"] = old


if __name__ == "__main__":
    unittest.main()
