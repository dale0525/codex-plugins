from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CLI = PLUGIN_ROOT / "mcp" / "cli.py"
FIXTURE_ROOT = PLUGIN_ROOT / "tests" / "fixtures" / "history"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _config(path: Path) -> None:
    path.write_text(
        "[shell_environment_policy.set]\n"
        'CREATIVE_MODEL_PROVIDER = "provider-a"\n'
        'CREATIVE_MODEL_DEFAULT = "opaque-model"\n\n'
        "[model_providers.provider-a]\n"
        'base_url = "https://provider.test/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "BRIDGE_SCHEMA_KEY"\n',
        encoding="utf-8",
    )


def _owned_block(text: str) -> str:
    begin = text.index("# creative-model-bridge:begin")
    end = text.index("# creative-model-bridge:end", begin)
    end = text.index("\n", end) + 1
    return text[begin:end]


def _materialize_history(home: Path, family: str) -> tuple[Path, Path, Path, bytes, bytes]:
    """Copy a committed historical fixture and rebase only path-bound values."""

    source = FIXTURE_ROOT / family
    state_root = home / "creative-model-bridge"
    state_root.mkdir(parents=True)
    old_root = f"/private/tmp/cmb-history-materializer/{family}"
    materialized_home = home.resolve()
    config_raw = (source / "config.toml").read_bytes().replace(old_root.encode(), str(materialized_home).encode())
    command_path = materialized_home / "legacy-command"
    shutil.copyfile(source / "legacy-command", command_path)
    command_path.chmod(0o700)
    config_path = materialized_home / "config.toml"
    config_path.write_bytes(config_raw)
    state = json.loads((source / "provision-state.json").read_text(encoding="utf-8"))
    state["config_path"] = str(config_path)
    state["command"] = str(command_path)
    # The historical managed renderer digest is path-bound.  Recompute only
    # that field; config_digest intentionally remains a historical image digest
    # and is allowed to be stale after unrelated edits.
    state["managed_digest"] = _sha256(_owned_block(config_raw.decode("utf-8")).encode("utf-8"))
    state_path = state_root / "provision-state.json"
    state_bytes = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")
    state_path.write_bytes(state_bytes)
    # Current v4 runtime data is outside the legacy marker and must survive.
    runtime = state_root / "runtime" / "v4" / "objects" / "active-object"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"cmb-active-v4\n")
    pointer = state_root / "runtime" / "v4" / "pointer"
    pointer.write_bytes(b"active-object\n")
    return config_path, state_path, runtime, config_raw, state_bytes


def _run_migrate(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(CLI), "migrate", "--codex-home", str(home)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


class LifecycleAndSchemaTests(unittest.TestCase):
    def test_manifest_and_tasks_have_no_mcp_surface(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("mcp_servers", manifest)
        self.assertFalse((PLUGIN_ROOT / "mcp" / "server.py").exists())
        self.assertFalse((PLUGIN_ROOT / "mcp" / "provision.py").exists())
        pixi = (PLUGIN_ROOT / "pixi.toml").read_text(encoding="utf-8")
        self.assertIn("run =", pixi)
        self.assertNotIn("server.py", pixi)
        self.assertNotIn("provision setup", pixi)

    def test_history_fixture_manifest_hashes_are_stable(self) -> None:
        manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        for family, details in manifest["fixtures"].items():
            for name, expected in details["files"].items():
                self.assertEqual(_sha256((FIXTURE_ROOT / family / name).read_bytes()), expected, (family, name))

    def test_cli_ready_response_and_result_reassemble(self) -> None:
        with tempfile.TemporaryDirectory(prefix="creative-cli-lifecycle-") as temporary:
            root = Path(temporary)
            home = root / "codex-home"
            home.mkdir()
            _config(home / "config.toml")
            env = os.environ.copy()
            env.update({"CODEX_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"})
            request = {"protocol": 1, "type": "request", "id": "schema-1", "operation": "creative_preview", "arguments": {"task": "写作"}}
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "run"],
                input=json.dumps(request, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(frames[0]["type"], "ready")
            self.assertFalse(frames[0]["input_echo"])
            self.assertEqual(frames[0]["input_mode"], "pipe")
            response = frames[1]
            self.assertEqual(response["type"], "response")
            data = [frame for frame in frames[2:] if frame["type"] == "data"]
            self.assertEqual([frame["seq"] for frame in data], list(range(len(data))))
            serialized = "".join(frame["data"] for frame in data)
            raw = serialized.encode("utf-8")
            self.assertEqual(hashlib.sha256(raw).hexdigest(), response["sha256"])
            self.assertEqual(len(raw), response["bytes"])
            self.assertTrue(data[-1]["done"])
            value = json.loads(serialized)
            self.assertEqual(value["text"], "")

    def test_migration_rebases_committed_v015_and_v018_and_preserves_v4(self) -> None:
        for family in ("v0.1.5", "v0.1.18"):
            with self.subTest(family=family), tempfile.TemporaryDirectory(prefix="creative-cli-migrate-history-") as temporary:
                home = Path(temporary)
                config_path, state_path, runtime, config_before, state_before = _materialize_history(home, family)
                # Unrelated edits are outside the managed spans and make the
                # historical config_digest stale by design.
                with config_path.open("ab") as handle:
                    handle.write(b"[mcp_servers.other]\ncommand = \"other\"\n")
                config_before_migration = config_path.read_bytes()
                result = _run_migrate(home)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "migrated")
                self.assertEqual(report["family"], "0.1.5" if family == "v0.1.5" else "0.1.18")
                updated = config_path.read_text(encoding="utf-8")
                self.assertNotIn("creative-model-bridge:begin", updated)
                self.assertIn('[mcp_servers.other]\ncommand = "other"', updated)
                self.assertFalse(state_path.exists())
                self.assertEqual(runtime.read_bytes(), b"cmb-active-v4\n")
                backup = Path(report["backup"])
                self.assertEqual((backup / "config.toml").read_bytes(), config_before_migration)
                self.assertEqual((backup / "provision-state.json").read_bytes(), state_before)
                manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["config_sha256"], _sha256(config_before_migration))
                self.assertEqual(manifest["state_sha256"], _sha256(state_before))

    def test_migration_rejects_duplicate_retyped_missing_and_unknown_state(self) -> None:
        cases = ("duplicate", "retyped", "missing", "unknown")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix="creative-cli-migrate-state-") as temporary:
                home = Path(temporary)
                config_path, state_path, runtime, _, _ = _materialize_history(home, "v0.1.5")
                raw = state_path.read_text(encoding="utf-8")
                state = json.loads(raw)
                if case == "duplicate":
                    raw = raw.rstrip()[:-1] + ',"status":"installed"}\n'
                elif case == "retyped":
                    state["schema_version"] = True
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                elif case == "missing":
                    del state["managed_digest"]
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                else:
                    state["unexpected"] = "nope"
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                if case == "duplicate":
                    state_path.write_text(raw, encoding="utf-8")
                before = (config_path.read_bytes(), state_path.read_bytes(), runtime.read_bytes())
                result = _run_migrate(home)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((config_path.read_bytes(), state_path.read_bytes(), runtime.read_bytes()), before)

    def test_migration_rejects_command_digest_env_ssl_and_marker_drift(self) -> None:
        mutations = ("command", "command_digest", "env", "ssl", "marker")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="creative-cli-migrate-drift-") as temporary:
                home = Path(temporary)
                config_path, state_path, _runtime, _, _ = _materialize_history(home, "v0.1.18")
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if mutation == "command":
                    state["command"] = str(home / "other-command")
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                elif mutation == "command_digest":
                    (home / "legacy-command").write_bytes(b"changed")
                elif mutation == "env":
                    config_path.write_text(config_path.read_text(encoding="utf-8").replace('env_key = "HISTORY_KEY"', 'env_key = "OTHER_KEY"'), encoding="utf-8")
                elif mutation == "ssl":
                    config_path.write_text(config_path.read_text(encoding="utf-8").replace('/etc/ssl/cert.pem', '/etc/ssl/other.pem'), encoding="utf-8")
                else:
                    config_path.write_text(config_path.read_text(encoding="utf-8").replace("schema=1", "schema=2"), encoding="utf-8")
                before = (config_path.read_bytes(), state_path.read_bytes())
                result = _run_migrate(home)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((config_path.read_bytes(), state_path.read_bytes()), before)

    def test_migration_rejects_unknown_table_and_owned_comment(self) -> None:
        for addition in (
            "[mcp_servers.creative-model-bridge.extra]\nvalue = true\n",
            "# unowned comment\n",
            "# creative-model-bridge:gap\n",
        ):
            with self.subTest(addition=addition), tempfile.TemporaryDirectory(prefix="creative-cli-migrate-owned-") as temporary:
                home = Path(temporary)
                config_path, state_path, _runtime, _, _ = _materialize_history(home, "v0.1.5")
                text = config_path.read_text(encoding="utf-8")
                if addition.startswith("["):
                    text = text.replace("[mcp_servers.creative-model-bridge.env]\n", addition + "[mcp_servers.creative-model-bridge.env]\n")
                elif addition.startswith("# creative-model-bridge:gap"):
                    text = text.replace("[mcp_servers.creative-model-bridge]\n", addition + "[mcp_servers.creative-model-bridge]\n")
                else:
                    text = text.replace("args = []\n", "args = []\n" + addition)
                config_path.write_text(text, encoding="utf-8")
                before = (config_path.read_bytes(), state_path.read_bytes())
                result = _run_migrate(home)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((config_path.read_bytes(), state_path.read_bytes()), before)

    def test_migration_failure_injection_rolls_back_and_retains_wal(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("cmb_migrate_under_test", PLUGIN_ROOT / "mcp" / "migrate.py")
        assert spec and spec.loader
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        with tempfile.TemporaryDirectory(prefix="creative-cli-migrate-failure-") as temporary:
            home = Path(temporary)
            config_path, state_path, _runtime, config_before, state_before = _materialize_history(home, "v0.1.5")
            original = migrate._atomic_write

            def fail_config(path: Path, raw: bytes) -> None:
                if path == config_path and raw != config_before:
                    raise OSError("injected config write failure")
                original(path, raw)

            with patch.object(migrate, "_atomic_write", side_effect=fail_config):
                with self.assertRaises(migrate.MigrationError):
                    migrate.migrate_legacy(home=home)
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual(state_path.read_bytes(), state_before)
            self.assertTrue(list((home / "creative-model-bridge" / "migration-backups").glob("*/manifest.json")))
            self.assertTrue((home / "creative-model-bridge" / "migration.wal.json").exists())

    def test_migration_concurrent_cas_detects_edit_and_rolls_back(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("cmb_migrate_cas", PLUGIN_ROOT / "mcp" / "migrate.py")
        assert spec and spec.loader
        migrate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrate)
        with tempfile.TemporaryDirectory(prefix="creative-cli-migrate-cas-") as temporary:
            home = Path(temporary)
            config_path, state_path, _runtime, config_before, state_before = _materialize_history(home, "v0.1.5")
            original = migrate._atomic_write
            mutated = False

            def mutate_after_config_write(path: Path, raw: bytes) -> None:
                nonlocal mutated
                original(path, raw)
                if path == config_path and raw != config_before and not mutated:
                    mutated = True
                    path.write_bytes(raw + b"# concurrent edit\n")

            with patch.object(migrate, "_atomic_write", side_effect=mutate_after_config_write):
                with self.assertRaises(migrate.MigrationError):
                    migrate.migrate_legacy(home=home)
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual(state_path.read_bytes(), state_before)


if __name__ == "__main__":
    unittest.main()
