from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugins/gortex"
VERSION = "1.2.3"


class GortexLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "plugin"
        shutil.copytree(PLUGIN_SOURCE, self.plugin)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.curl_log = self.root / "curl.log"
        self.home = self.root / "home"
        self.windows_cache_root = self.root / "windows-local-app-data"
        self.runtime = self.root / "gortex"
        self.runtime.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = version ] && [ \"${2:-}\" = --short ]; then\n"
            f"  printf '%s\\n' 'v{VERSION}+fixture'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = mcp ]; then\n"
            "  case \"${2:-}\" in\n"
            "    probe) printf 'probe:%s\\n' \"${3:-}\" ;;\n"
            "    probe-cwd) printf 'cwd:%s\\n' \"$PWD\" ;;\n"
            "    *) printf 'mcp:%s\\n' \"${2:-}\" ;;\n"
            "  esac\n"
            "  exit 0\n"
            "fi\n"
            "printf 'unexpected:%s\\n' \"${1:-}\"\n",
            encoding="utf-8",
        )
        self.runtime.chmod(self.runtime.stat().st_mode | stat.S_IXUSR)
        self.valid_tar = self.root / "runtime.tar.gz"
        self.valid_zip = self.root / "runtime.zip"
        self._make_tar(self.valid_tar)
        self._make_zip(self.valid_zip)
        self._write_fake_tools()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_executable(self, name: str, contents: str) -> None:
        path = self.fake_bin / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_fake_tools(self) -> None:
        self._write_executable(
            "uname",
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  -s) printf '%s\\n' \"$TEST_UNAME_S\" ;;\n"
            "  -m) printf '%s\\n' \"$TEST_UNAME_M\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
        )
        self._write_executable(
            "cygpath",
            "#!/bin/sh\n"
            "[ \"${1:-}\" = -u ] || exit 2\n"
            "case \"${2:-}\" in\n"
            "  \"$LOCALAPPDATA\") printf '%s\\n' \"$TEST_CYGPATH_LOCALAPPDATA\" ;;\n"
            "  \"$USERPROFILE/.codex\") printf '%s\\n' \"$TEST_CYGPATH_CODEX_HOME\" ;;\n"
            "  *) exit 3 ;;\n"
            "esac\n",
        )
        self._write_executable(
            "curl",
            "#!/bin/sh\n"
            "set -eu\n"
            "output=\n"
            "url=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --output) output=$2; shift 2 ;;\n"
            "    *) url=$1; shift ;;\n"
            "  esac\n"
            "done\n"
            "printf '%s\\n' \"$url\" >> \"$TEST_CURL_LOG\"\n"
            "if [ -n \"${TEST_CURL_GATE:-}\" ]; then\n"
            "  while [ ! -f \"$TEST_CURL_GATE\" ]; do sleep 0.01; done\n"
            "fi\n"
            "/bin/cp \"$TEST_ARCHIVE\" \"$output\"\n",
        )

    def _make_tar(self, path: Path, unsafe: bool = False) -> None:
        with tarfile.open(path, "w:gz") as archive:
            archive.add(self.runtime, arcname="package/gortex")
            if unsafe:
                member = tarfile.TarInfo("../outside")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))

    def _make_zip(self, path: Path, unsafe: bool = False) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(self.runtime, arcname="package/gortex.exe")
            if unsafe:
                archive.writestr("../outside", b"x")

    @staticmethod
    def _asset(name: str, archive: Path, expected_size: int | None = None, expected_sha: str | None = None) -> dict[str, object]:
        payload = archive.read_bytes()
        return {
            "name": name,
            "url": f"https://fixtures.invalid/{name}",
            "size": len(payload) if expected_size is None else expected_size,
            "sha256": hashlib.sha256(payload).hexdigest() if expected_sha is None else expected_sha,
        }

    def _write_metadata(
        self,
        darwin_archive: Path | None = None,
        windows_archive: Path | None = None,
        darwin_size: int | None = None,
        darwin_sha: str | None = None,
    ) -> None:
        metadata = {
            "version": VERSION,
            "assets": {
                "gortex_darwin_arm64": self._asset(
                    "gortex_darwin_arm64.tar.gz",
                    darwin_archive or self.valid_tar,
                    darwin_size,
                    darwin_sha,
                ),
                "gortex_windows_amd64": self._asset(
                    "gortex_windows_amd64.zip", windows_archive or self.valid_zip
                ),
            },
        }
        (self.plugin / "runtime-release.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def _environment(self, system: str, machine: str, archive: Path, gate: Path | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{environment.get('PATH', '')}",
                "HOME": str(self.home),
                "CODEX_HOME": str(self.root / ".codex"),
                "LOCALAPPDATA": r"C:\\Users\\test\\AppData\\Local",
                "USERPROFILE": r"C:\\Users\\test",
                "TEST_ARCHIVE": str(archive),
                "TEST_CURL_LOG": str(self.curl_log),
                "TEST_CYGPATH_LOCALAPPDATA": str(self.windows_cache_root),
                "TEST_CYGPATH_CODEX_HOME": str(self.root / "windows-profile/.codex"),
                "TEST_UNAME_S": system,
                "TEST_UNAME_M": machine,
            }
        )
        if gate is not None:
            environment["TEST_CURL_GATE"] = str(gate)
        return environment

    def _run(self, system: str, machine: str, archive: Path, *arguments: str, gate: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(self.plugin / "scripts/launch.sh"), *arguments],
            cwd=self.root,
            env=self._environment(system, machine, archive, gate),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def _version_dir(self, windows: bool = False) -> Path:
        cache_root = (
            self.windows_cache_root / "gortex"
            if windows
            else self.home / ".local/share/gortex"
        )
        return cache_root / "versions" / VERSION

    def _curl_calls(self) -> list[str]:
        return self.curl_log.read_text(encoding="utf-8").splitlines() if self.curl_log.exists() else []

    def test_darwin_arm64_installs_once_then_reuses_the_verified_cache(self) -> None:
        self._write_metadata()
        first = self._run("Darwin", "arm64", self.valid_tar, "probe", "first")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "probe:first\n")
        binary = self._version_dir() / "gortex"
        self.assertTrue(binary.is_file())
        self.assertTrue(os.access(binary, os.X_OK))

        second = self._run("Darwin", "arm64", self.root / "does-not-exist", "probe", "cached")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, "probe:cached\n")
        self.assertEqual(len(self._curl_calls()), 1)

    def test_mingw_x86_64_uses_windows_zip_and_localappdata_cache(self) -> None:
        self._write_metadata()
        result = self._run("MINGW64_NT-10.0", "x86_64", self.valid_zip, "probe", "windows")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "probe:windows\n")
        self.assertTrue((self._version_dir(windows=True) / "gortex.exe").is_file())
        self.assertEqual(self._curl_calls(), ["https://fixtures.invalid/gortex_windows_amd64.zip"])

    def test_concurrent_first_launches_publish_only_a_complete_version(self) -> None:
        self._write_metadata()
        gate = self.root / "allow-download"
        command = ["/bin/sh", str(self.plugin / "scripts/launch.sh"), "probe", "concurrent"]
        first = subprocess.Popen(command, cwd=self.root, env=self._environment("Darwin", "arm64", self.valid_tar, gate), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while not self._curl_calls() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(self._curl_calls()), 1, "the first launcher did not reach the isolated downloader")
        self.assertFalse(self._version_dir().exists(), "a version must not be visible before staging completes")
        second = subprocess.Popen(command, cwd=self.root, env=self._environment("Darwin", "arm64", self.valid_tar, gate), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.05)
        gate.touch()
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertEqual(second.returncode, 0, second_stderr)
        self.assertEqual(first_stdout, "probe:concurrent\n")
        self.assertEqual(second_stdout, "probe:concurrent\n")
        self.assertEqual(len(self._curl_calls()), 1)
        self.assertTrue((self._version_dir() / "gortex").is_file())

    def test_size_and_sha256_failures_do_not_publish_a_version(self) -> None:
        self._write_metadata(darwin_size=self.valid_tar.stat().st_size + 1)
        size_failure = self._run("Darwin", "arm64", self.valid_tar, "probe", "size")
        self.assertNotEqual(size_failure.returncode, 0)
        self.assertIn("Archive size verification failed", size_failure.stderr)
        self.assertFalse(self._version_dir().exists())

        self._write_metadata(darwin_sha="0" * 64)
        digest_failure = self._run("Darwin", "arm64", self.valid_tar, "probe", "digest")
        self.assertNotEqual(digest_failure.returncode, 0)
        self.assertIn("Archive SHA-256 verification failed", digest_failure.stderr)
        self.assertFalse(self._version_dir().exists())

    def test_unsafe_and_corrupt_archives_do_not_publish_a_version(self) -> None:
        unsafe = self.root / "unsafe.tar.gz"
        self._make_tar(unsafe, unsafe=True)
        self._write_metadata(darwin_archive=unsafe)
        unsafe_result = self._run("Darwin", "arm64", unsafe, "probe", "unsafe")
        self.assertNotEqual(unsafe_result.returncode, 0)
        self.assertIn("Archive contains an unsafe extraction path", unsafe_result.stderr)
        self.assertFalse(self._version_dir().exists())

        corrupt = self.root / "corrupt.tar.gz"
        corrupt.write_bytes(b"not a gzip archive")
        self._write_metadata(darwin_archive=corrupt)
        corrupt_result = self._run("Darwin", "arm64", corrupt, "probe", "corrupt")
        self.assertNotEqual(corrupt_result.returncode, 0)
        self.assertFalse(self._version_dir().exists())

    def test_linux_and_other_platforms_fail_before_downloading(self) -> None:
        self._write_metadata()
        for system, machine in (("Linux", "x86_64"), ("FreeBSD", "amd64")):
            with self.subTest(system=system, machine=machine):
                result = self._run(system, machine, self.valid_tar, "probe", "unsupported")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Unsupported gortex platform: {system}-{machine}", result.stderr)
        self.assertEqual(self._curl_calls(), [])

    def test_mcp_git_alias_preserves_the_calling_repository_cwd(self) -> None:
        self._write_metadata()
        companion = json.loads((self.plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = companion["gortex"]
        installed_plugin = (
            self.home
            / ".codex/plugins/cache/dale0525-codex-plugins/gortex/0.1.1"
        )
        shutil.copytree(self.plugin, installed_plugin)
        project = self.root / "different-project"
        nested_workspace = project / "packages/example"
        nested_workspace.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=project,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [server["command"], *server["args"], "probe-cwd"],
            cwd=nested_workspace,
            env={
                key: value
                for key, value in self._environment("Darwin", "arm64", self.valid_tar).items()
                if key != "CODEX_HOME"
            },
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"cwd:{nested_workspace.resolve()}\n")

    def test_mcp_manifest_forwards_windows_cache_location(self) -> None:
        companion = json.loads((self.plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = companion["gortex"]
        self.assertEqual(
            server["env_vars"],
            ["CODEX_HOME", "HOME", "USERPROFILE", "LOCALAPPDATA"],
        )

    def test_mcp_git_alias_falls_back_to_windows_userprofile(self) -> None:
        self._write_metadata()
        companion = json.loads((self.plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = companion["gortex"]
        installed_plugin = (
            self.root
            / "windows-profile/.codex/plugins/cache/dale0525-codex-plugins/gortex/0.1.1"
        )
        shutil.copytree(self.plugin, installed_plugin)
        project = self.root / "windows-project"
        project.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"], cwd=project, check=True, capture_output=True, text=True
        )
        environment = self._environment("MINGW64_NT-10.0", "x86_64", self.valid_zip)
        environment.pop("CODEX_HOME", None)
        environment.pop("HOME", None)
        result = subprocess.run(
            [server["command"], *server["args"], "probe-cwd"],
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"cwd:{project.resolve()}\n")

    def test_mcp_windows_launch_succeeds_without_cygpath_in_path(self) -> None:
        self._write_metadata()
        (self.fake_bin / "cygpath").unlink()
        installed_plugin = (
            self.root
            / "C:/Users/test/.codex/plugins/cache/dale0525-codex-plugins/gortex/fixture"
        )
        shutil.copytree(self.plugin, installed_plugin)
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        companion = json.loads((self.plugin / ".mcp.json").read_text(encoding="utf-8"))
        server = companion["gortex"]
        environment = self._environment("MINGW64_NT-10.0", "x86_64", self.valid_zip)
        environment["CODEX_HOME"] = r"C:\Users\test\.codex"
        environment["LOCALAPPDATA"] = r"C:\Users\test\AppData\Local"

        result = subprocess.run(
            [server["command"], *server["args"], "probe-cwd"],
            cwd=self.root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"cwd:{self.root.resolve()}\n")
        self.assertTrue(
            (
                self.root
                / f"C:/Users/test/AppData/Local/gortex/versions/{VERSION}/gortex.exe"
            ).is_file()
        )

    def test_launcher_always_executes_the_mcp_subcommand(self) -> None:
        self._write_metadata()
        result = self._run("Darwin", "arm64", self.valid_tar, "probe", "mcp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "probe:mcp\n")


if __name__ == "__main__":
    unittest.main()
