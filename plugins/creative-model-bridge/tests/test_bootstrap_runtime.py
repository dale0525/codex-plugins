from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1]
BOOTSTRAP = PLUGIN / "scripts/bootstrap.sh"
PROVISION_PS1 = PLUGIN / "scripts/provision.ps1"
CLI = PLUGIN / "mcp" / "cli.py"
FIXTURE_ROOT = PLUGIN / "tests" / "fixtures" / "history"
ASSET = "creative-model-bridge-aarch64-apple-darwin"


@unittest.skipUnless(os.name != "nt", "POSIX bootstrap.sh is not executed on native Windows")
class BootstrapRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="creative-bootstrap-")
        self.root = Path(self.temp.name)
        self.home = self.root / "codex-home"
        self.fakebin = self.root / "fakebin"
        self.fakebin.mkdir()
        self.payload = b"#!/bin/sh\nprintf 'bridge-ok %s\\n' \"$1\"\n"
        self.calls = self.root / "curl.calls"
        fake_curl = self.root / "fake-curl.py"
        fake_curl.write_text(
            "import hashlib, os, sys\n"
            "args=sys.argv[1:]\n"
            "url=next(x for x in args if x.startswith('https://'))\n"
            "out=args[args.index('--output')+1]\n"
            "payload=os.environ['FAKE_CURL_PAYLOAD'].encode()\n"
            "asset=os.environ.get('FAKE_CURL_ASSET','" + ASSET + "')\n"
            "if os.environ.get('FAKE_CURL_LOG'): open(os.environ['FAKE_CURL_LOG'],'a').write(url+'\\n')\n"
            "if os.environ.get('FAKE_CURL_MODE') == '404': raise SystemExit(22)\n"
            "if url.endswith('/checksums.txt'):\n"
            " data=(hashlib.sha256(payload).hexdigest()+'  '+asset+'\\n').encode()\n"
            "else: data=payload\n"
            "if os.environ.get('FAKE_CURL_MODE') == 'bad': data=(('0'*64)+'  '+asset+'\\n').encode() if url.endswith('/checksums.txt') else payload\n"
            "open(out,'wb').write(data)\n",
            encoding="utf-8",
        )
        curl = self.fakebin / "curl"
        curl.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(fake_curl)) + " \"$@\"\n", encoding="utf-8")
        curl.chmod(0o755)
        uname = self.fakebin / "uname"
        uname.write_text("#!/bin/sh\n[ \"$1\" = -s ] && printf '%s\\n' \"${FAKE_UNAME_S:-Darwin}\" || printf '%s\\n' \"${FAKE_UNAME_M:-arm64}\"\n", encoding="utf-8")
        uname.chmod(0o755)

    def make_cli_wrapper(self) -> Path:
        wrapper = self.root / "cmb-cli"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(CLI)) + " \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def materialize_history(self, home: Path) -> tuple[Path, Path, Path]:
        source = FIXTURE_ROOT / "v0.1.18"
        materialized_home = home.resolve()
        state_root = materialized_home / "creative-model-bridge"
        state_root.mkdir(parents=True)
        old_root = b"/private/tmp/cmb-history-materializer/v0.1.18"
        config_path = materialized_home / "config.toml"
        config_raw = (source / "config.toml").read_bytes().replace(old_root, str(materialized_home).encode())
        config_path.write_bytes(config_raw)
        command_path = materialized_home / "legacy-command"
        shutil.copyfile(source / "legacy-command", command_path)
        command_path.chmod(0o700)
        state = json.loads((source / "provision-state.json").read_text(encoding="utf-8"))
        state["config_path"] = str(config_path)
        state["command"] = str(command_path)
        begin = config_raw.decode("utf-8").index("# creative-model-bridge:begin")
        end = config_raw.decode("utf-8").index("# creative-model-bridge:end", begin)
        end = config_raw.decode("utf-8").index("\n", end) + 1
        state["managed_digest"] = hashlib.sha256(config_raw.decode("utf-8")[begin:end].encode("utf-8")).hexdigest()
        state_path = state_root / "provision-state.json"
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        runtime = state_root / "runtime" / "v4" / "objects" / "active-object"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"cmb-active-v4\n")
        return config_path, state_path, runtime

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_bootstrap(self, *, home: Path | None = None, offline: str = "0", mode: str = "", argument: str = "run", extra: dict[str, str] | None = None, stdin_data: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        requested = extra or {}
        system = requested.get("FAKE_UNAME_S", "Darwin")
        machine = requested.get("FAKE_UNAME_M", "arm64")
        asset = ASSET if system == "Darwin" and machine == "arm64" else (
            "creative-model-bridge-x86_64-apple-darwin" if system == "Darwin" else (
                "creative-model-bridge-aarch64-unknown-linux-gnu" if machine in {"aarch64", "arm64"} else "creative-model-bridge-x86_64-unknown-linux-gnu"
            )
        )
        env.update({
            "CODEX_HOME": str(home or self.home),
            "PATH": str(self.fakebin) + os.pathsep + env["PATH"],
            "FAKE_CURL_PAYLOAD": self.payload.decode(),
            "FAKE_CURL_LOG": str(self.calls),
            "FAKE_CURL_MODE": mode,
            "CREATIVE_MODEL_BRIDGE_OFFLINE": offline,
            "FAKE_UNAME_S": "Darwin",
            "FAKE_UNAME_M": "arm64",
            "FAKE_CURL_ASSET": asset,
        })
        if extra:
            env.update(extra)
        return subprocess.run([str(BOOTSTRAP), argument], input=stdin_data, capture_output=True, text=True, env=env, timeout=30)

    def test_override_runs_cli_without_download(self) -> None:
        override = self.root / "override"
        override.write_text("#!/bin/sh\nprintf 'override %s\\n' \"$1\"\n", encoding="utf-8")
        override.chmod(0o755)
        result = self.run_bootstrap(extra={"CREATIVE_MODEL_BRIDGE_BIN": str(override)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "override run\n")
        self.assertFalse(self.calls.exists())

    def test_download_checksum_cache_and_offline_hot_start(self) -> None:
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "bridge-ok run\n")
        cache = self.home / "creative-model-bridge/runtime/v0.2.0/objects/aarch64-apple-darwin"
        active = (cache / "active").read_text(encoding="utf-8").splitlines()
        self.assertEqual(active[0], "cmb-active-v4")
        digest, generation = active[1:]
        binary = cache / digest / generation / ASSET
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), digest)
        calls = self.calls.read_text(encoding="utf-8").count("\n")
        second = self.run_bootstrap(offline="1", mode="404")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.calls.read_text(encoding="utf-8").count("\n"), calls)

    def test_bad_checksum_never_publishes(self) -> None:
        result = self.run_bootstrap(mode="bad")
        self.assertNotEqual(result.returncode, 0)
        cache = self.home / "creative-model-bridge/runtime/v0.2.0/objects/aarch64-apple-darwin"
        self.assertFalse((cache / "active").exists())
        self.assertEqual(list(cache.glob("staging.*")), [])

    def test_platform_selection_and_version_bound_offline_cache(self) -> None:
        for index, (system, machine, target) in enumerate((
            ("Darwin", "arm64", "aarch64-apple-darwin"),
            ("Darwin", "x86_64", "x86_64-apple-darwin"),
            ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
            ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        )):
            home = self.root / ("platform-" + str(index))
            result = self.run_bootstrap(home=home, extra={"FAKE_UNAME_S": system, "FAKE_UNAME_M": machine})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / "creative-model-bridge/runtime/v0.2.0/objects" / target / "active").is_file())
        wrong_version = self.run_bootstrap(offline="1", extra={"CREATIVE_MODEL_BRIDGE_VERSION": "0.1.8"})
        self.assertNotEqual(wrong_version.returncode, 0)
        self.assertIn("cached runtime", wrong_version.stderr)

    def test_active_pointer_traversal_is_rejected(self) -> None:
        cache = self.home / "creative-model-bridge/runtime/v0.2.0/objects/aarch64-apple-darwin"
        cache.mkdir(parents=True)
        (cache / "active").write_text("cmb-active-v4\n../escape\n../../outside\n", encoding="utf-8")
        result = self.run_bootstrap(offline="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_windows_launcher_static_contract_has_only_new_actions(self) -> None:
        text = PROVISION_PS1.read_text(encoding="utf-8")
        self.assertIn("Tls12", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("[Security.Cryptography.SHA256]::Create()", text)
        self.assertIn("ValidateSet('run','cli','exec','cache','install','migrate')", text)
        self.assertNotIn("ValidateSet('setup'", text)
        self.assertIn("if ($Action -eq 'cache')", text)
        self.assertIn("'--codex-home'", text)
        self.assertIn("$migrateArgs = @('migrate', '--codex-home', $codexHome) + @($RemainingArgs)", text)
        self.assertIn("& ([string]$binary) @migrateArgs", text)
        self.assertIn("function Publish-LocalOverride", text)
        self.assertIn("cmb-object-v4", text)
        self.assertIn("if ($Action -eq 'migrate')", text)

    def test_cache_prewarms_local_override_and_install_runs_migrate(self) -> None:
        override = self.root / "override"
        override.write_text("#!/bin/sh\nprintf 'override %s\\n' \"$1\"\n", encoding="utf-8")
        override.chmod(0o755)
        cached = self.run_bootstrap(argument="cache", extra={"CREATIVE_MODEL_BRIDGE_BIN": str(override)})
        self.assertEqual(cached.returncode, 0, cached.stderr)
        self.assertEqual(cached.stdout, "")
        cache = self.home / "creative-model-bridge/runtime/v0.2.0/objects/aarch64-apple-darwin"
        active = (cache / "active").read_text(encoding="utf-8").splitlines()
        self.assertEqual(active[0], "cmb-active-v4")
        digest, generation = active[1:]
        object_root = cache / digest / generation
        self.assertEqual((object_root / "complete").read_text(encoding="utf-8").splitlines(), ["cmb-object-v4", digest, generation])
        self.assertEqual(hashlib.sha256((object_root / ASSET).read_bytes()).hexdigest(), digest)
        installed = self.run_bootstrap(argument="install", extra={"CREATIVE_MODEL_BRIDGE_BIN": str(override)})
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(installed.stdout, "override migrate\n")
        self.assertFalse(self.calls.exists())

    def test_run_override_eof_fails_before_ready(self) -> None:
        result = self.run_bootstrap(extra={"CREATIVE_MODEL_BRIDGE_BIN": str(self.make_cli_wrapper())})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn('"type":"ready"', result.stdout)

    def test_install_migrates_historical_fixture_and_preserves_v4_runtime(self) -> None:
        home = self.root / "historical-home"
        config_path, state_path, runtime = self.materialize_history(home)
        wrapper = self.make_cli_wrapper()
        result = self.run_bootstrap(home=home, argument="install", extra={"CREATIVE_MODEL_BRIDGE_BIN": str(wrapper)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "migrated")
        self.assertFalse(state_path.exists())
        self.assertNotIn("creative-model-bridge:begin", config_path.read_text(encoding="utf-8"))
        self.assertEqual(runtime.read_bytes(), b"cmb-active-v4\n")


if __name__ == "__main__":
    unittest.main()
