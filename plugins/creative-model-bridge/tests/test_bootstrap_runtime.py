from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1]
BOOTSTRAP = PLUGIN / "scripts/bootstrap.sh"
PROVISION_PS1 = PLUGIN / "scripts/provision.ps1"
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
        powershell = self.fakebin / "powershell.exe"
        powershell.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$FAKE_POWERSHELL_LOG\"\n",
            encoding="utf-8",
        )
        powershell.chmod(0o755)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_bootstrap(self, *, home: Path | None = None, offline: str = "0", mode: str = "", argument: str = "hello", extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
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
        return subprocess.run([str(BOOTSTRAP), argument], capture_output=True, text=True, env=env, timeout=30)

    def test_override_does_not_download(self) -> None:
        override = self.root / "override"
        override.write_text("#!/bin/sh\nprintf 'override\\n'\n", encoding="utf-8")
        override.chmod(0o755)
        result = self.run_bootstrap(extra={"CREATIVE_MODEL_BRIDGE_BIN": str(override)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "override\n")
        self.assertFalse(self.calls.exists())

    def test_serve_override_execs_runtime_in_serve_mode(self) -> None:
        override = self.root / "override-serve"
        override.write_text("#!/bin/sh\nprintf 'override %s\\n' \"$1\"\n", encoding="utf-8")
        override.chmod(0o755)
        result = self.run_bootstrap(argument="serve", extra={"CREATIVE_MODEL_BRIDGE_BIN": str(override)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "override serve\n")
        self.assertFalse(self.calls.exists())

    def test_msys_serve_handoff_uses_powershell_launcher_argv(self) -> None:
        powershell_log = self.root / "powershell.calls"
        result = self.run_bootstrap(
            argument="serve",
            extra={
                "FAKE_UNAME_S": "MSYS_NT-10.0-22631",
                "FAKE_UNAME_M": "x86_64",
                "FAKE_POWERSHELL_LOG": str(powershell_log),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = powershell_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv[:5], ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        self.assertEqual(Path(argv[5]).name, "provision.ps1")
        self.assertEqual(argv[6:], ["serve"])
        self.assertFalse(self.calls.exists())

    def test_download_checksum_cache_and_offline_hot_start(self) -> None:
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "bridge-ok provision\n")
        cache = self.home / "creative-model-bridge/runtime/v0.1.18/objects/aarch64-apple-darwin"
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
        cache = self.home / "creative-model-bridge/runtime/objects/aarch64-apple-darwin"
        self.assertFalse((cache / "active").exists())
        self.assertEqual(list(cache.glob("staging.*")), [])

    def test_platform_selection_and_immutable_old_object(self) -> None:
        for index, (system, machine, target) in enumerate((
            ("Darwin", "arm64", "aarch64-apple-darwin"),
            ("Darwin", "x86_64", "x86_64-apple-darwin"),
            ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
            ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        )):
            home = self.root / ("platform-" + str(index))
            result = self.run_bootstrap(home=home, extra={"FAKE_UNAME_S": system, "FAKE_UNAME_M": machine})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / "creative-model-bridge/runtime/v0.1.18/objects" / target / "active").is_file())

    def test_version_bound_cache_does_not_cross_start_offline(self) -> None:
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        wrong_version = self.run_bootstrap(offline="1", extra={"CREATIVE_MODEL_BRIDGE_VERSION": "0.1.8"})
        self.assertNotEqual(wrong_version.returncode, 0)
        self.assertIn("cached runtime", wrong_version.stderr)

    def test_active_pointer_digest_and_generation_traversal_are_rejected(self) -> None:
        cache = self.home / "creative-model-bridge/runtime/v0.1.18/objects/aarch64-apple-darwin"
        cache.mkdir(parents=True)
        (cache / "active").write_text("cmb-active-v4\n../escape\n../../outside\n", encoding="utf-8")
        result = self.run_bootstrap(offline="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_windows_launcher_static_contract_is_strict(self) -> None:
        text = PROVISION_PS1.read_text(encoding="utf-8")
        self.assertIn("Tls12", text)
        self.assertIn("Get-FileHash", text)
        self.assertIn("[Security.Cryptography.SHA256]::Create()", text)
        self.assertIn("Get-Command -Name Get-FileHash", text)
        self.assertIn("ValidateSet('setup','status','repair','uninstall','serve')", text)
        self.assertIn("if ($Action -eq 'serve')", text)
        self.assertIn("-in @('.', '..')", text)
        self.assertIn("$marker[1] -ne $current[1]", text)
        self.assertIn("$marker[2] -ne $current[2]", text)
        self.assertNotRegex(text, r"(?im)^\s*\$home\s*=")
        self.assertIn("$codexHome =", text)


if __name__ == "__main__":
    unittest.main()
