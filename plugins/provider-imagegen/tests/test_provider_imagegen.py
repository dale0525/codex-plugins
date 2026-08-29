from __future__ import annotations

import base64
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
import sys

sys.path.insert(0, str(SCRIPT_DIR))
import provider_imagegen as imagegen  # noqa: E402


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _rgba_png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixel) for pixel in pixels[row * width : (row + 1) * width])
        for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


class _ImageHandler(BaseHTTPRequestHandler):
    response_body = {}
    seen_body = None
    seen_headers = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        type(self).seen_body = self.rfile.read(length)
        type(self).seen_headers = {key.lower(): value for key, value in self.headers.items()}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(type(self).response_body).encode("utf-8"))

    def log_message(self, *_args):
        return


class ProviderImagegenTests(unittest.TestCase):
    def setUp(self):
        self.transparent_png = _rgba_png(2, 1, [(255, 128, 0, 0), (255, 128, 0, 255)])
        self.opaque_png = _rgba_png(1, 1, [(255, 128, 0, 255)])
        _ImageHandler.response_body = {
            "data": [{"b64_json": base64.b64encode(self.transparent_png).decode("ascii")}]
        }
        _ImageHandler.seen_body = None
        _ImageHandler.seen_headers = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ImageHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_unix_launcher_skips_a_broken_python3_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "broken"
            working = root / "working"
            broken.mkdir()
            working.mkdir()
            broken_python = broken / "python3"
            broken_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            broken_python.chmod(0o755)
            working_python = working / "python3"
            working_python.write_text(
                "#!/bin/sh\n"
                "if [ \"${1-}\" = \"-c\" ]; then exit 0; fi\n"
                "printf '%s\\n' '{\"shim\":\"working\"}'\n",
                encoding="utf-8",
            )
            working_python.chmod(0o755)
            environment = dict(os.environ)
            environment["PATH"] = os.pathsep.join(
                (str(broken), str(working), "/usr/bin", "/bin")
            )
            completed = subprocess.run(
                [str(SCRIPT_DIR / "run.sh"), "--dry-run"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(completed.stdout.decode("utf-8")), {"shim": "working"})

    def test_gpt_image_2_transparency_is_sent_without_local_rejection(self):
        payload = imagegen.build_parameters(
            {
                "model": "gpt-image-2",
                "size": "1024x1024",
                "quality": "low",
                "n": 1,
                "background": "transparent",
                "output_format": "png",
            }
        )
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["background"], "transparent")
        self.assertEqual(payload["output_format"], "png")

    def test_transparent_png_requires_a_nonopaque_pixel(self):
        self.assertEqual(imagegen.verify_transparent_png(self.transparent_png), (2, 1))
        with self.assertRaises(imagegen.ImagegenError) as error:
            imagegen.verify_transparent_png(self.opaque_png)
        self.assertEqual(error.exception.code, "transparent_alpha_missing")

    def test_build_endpoint_replaces_existing_api_suffix(self):
        endpoint = imagegen.build_endpoint(
            {"base_url": "https://provider.example/v1/chat/completions?tenant=one", "query_params": {"mode": "image"}},
            "generations",
        )
        self.assertEqual(endpoint, "https://provider.example/v1/images/generations?tenant=one&mode=image")

    def test_headers_use_provider_credential_without_echoing_it(self):
        headers = imagegen.resolve_headers({"http_headers": {"Authorization": "Bearer secret-token"}})
        self.assertEqual(headers["Authorization"], "Bearer secret-token")
        error = imagegen.failure_result(imagegen.ImagegenError("config", "bad", diagnostic={"stderr": {"bytes": 1}}))
        self.assertNotIn("secret-token", json.dumps(error))

    def test_cache_loader_reads_sync_file_without_permission_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "credential.json"
            cache.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider": "company",
                        "base_url": "https://provider.example/v1",
                        "headers": {"Authorization": "Bearer cached-secret"},
                        "env_http_headers": {},
                        "query_params": {},
                        "requires_openai_auth": True,
                        "fingerprint": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            import os

            os.chmod(root, 0o755)
            os.chmod(cache, 0o644)
            provider = imagegen.load_cached_provider({"PROVIDER_IMAGEGEN_CREDENTIAL_FILE": str(cache)})
        self.assertEqual(provider["http_headers"]["Authorization"], "Bearer cached-secret")

    def test_cache_loader_falls_back_to_stable_marketplace_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / ".codex"
            plugin_root = codex_home / "plugins/cache/market/provider-imagegen/0.1.4"
            plugin_root.mkdir(parents=True)
            stable = (
                codex_home
                / "plugins/cache/market/.codex-provider/provider-imagegen/credential.json"
            )
            stable.parent.mkdir(parents=True)
            stable.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provider": "company",
                        "base_url": "https://provider.example/v1",
                        "headers": {"Authorization": "Bearer stable-secret"},
                        "env_http_headers": {},
                        "query_params": {},
                        "requires_openai_auth": True,
                        "fingerprint": "1" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(
                imagegen,
                "__file__",
                str(plugin_root / "scripts/provider_imagegen.py"),
            ):
                provider = imagegen.load_cached_provider({"CODEX_HOME": str(codex_home)})
        self.assertEqual(provider["http_headers"]["Authorization"], "Bearer stable-secret")

    def test_cache_loader_rejects_stable_cache_ancestor_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / ".codex"
            plugin_root = codex_home / "plugins/cache/market/provider-imagegen/0.1.4"
            plugin_root.mkdir(parents=True)
            outside = root / "outside"
            outside_plugin = outside / "provider-imagegen"
            outside_plugin.mkdir(parents=True)
            (outside_plugin / "credential.json").write_text("{}", encoding="utf-8")
            os.symlink(outside, codex_home / "plugins/cache/market/.codex-provider")
            with patch.object(
                imagegen,
                "__file__",
                str(plugin_root / "scripts/provider_imagegen.py"),
            ):
                with self.assertRaises(imagegen.ImagegenError) as error:
                    imagegen.load_cached_provider({"CODEX_HOME": str(codex_home)})
        self.assertEqual(error.exception.code, "credential_cache_invalid")

    @unittest.skipUnless(os.name == "nt", "Windows junction only")
    def test_cache_loader_rejects_windows_junction_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / ".codex"
            plugin_root = codex_home / "plugins/cache/market/provider-imagegen/0.1.4"
            plugin_root.mkdir(parents=True)
            outside = root / "outside"
            outside_plugin = outside / "provider-imagegen"
            outside_plugin.mkdir(parents=True)
            (outside_plugin / "credential.json").write_text("{}", encoding="utf-8")
            junction = codex_home / "plugins/cache/market/.codex-provider"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("mklink /J is unavailable")
            try:
                with patch.object(
                    imagegen,
                    "__file__",
                    str(plugin_root / "scripts/provider_imagegen.py"),
                ):
                    with self.assertRaises(imagegen.ImagegenError) as error:
                        imagegen.load_cached_provider({"CODEX_HOME": str(codex_home)})
                self.assertEqual(error.exception.code, "credential_cache_invalid")
            finally:
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(junction)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

    def test_remote_http_credentials_are_rejected_before_network(self):
        provider = {
            "base_url": "http://provider.example/v1",
            "http_headers": {"Authorization": "Bearer test-secret"},
        }
        client = imagegen.ImageClient(provider, imagegen.resolve_headers(provider), timeout=1)
        with self.assertRaises(imagegen.ImagegenError) as error:
            client.post_json("generations", {"model": "gpt-image-2"})
        self.assertEqual(error.exception.code, "insecure_http_credentials")

    def test_login_session_only_provider_fails_without_auth_file_access(self):
        with self.assertRaises(imagegen.ImagegenError) as error:
            imagegen.resolve_headers({"requires_openai_auth": True})
        self.assertEqual(error.exception.code, "credential_unavailable")

    def test_cross_origin_private_image_url_is_rejected(self):
        provider = {"base_url": "https://provider.example/v1"}
        client = imagegen.ImageClient(provider, {}, timeout=1)
        private_info = [(imagegen.socket.AF_INET, imagegen.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        with patch.object(imagegen.socket, "getaddrinfo", return_value=private_info):
            with self.assertRaises(imagegen.ImagegenError) as error:
                client.download("https://cdn.example/image.png")
        self.assertEqual(error.exception.code, "image_url_not_public")

    def test_generation_calls_provider_and_verifies_transparency(self):
        provider = {
            "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "http_headers": {"Authorization": "Bearer test-secret"},
        }
        client = imagegen.ImageClient(provider, imagegen.resolve_headers(provider), timeout=10)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transparent.png"
            result = imagegen.run_generate(
                "isolated orange fox",
                {
                    "model": "gpt-image-2",
                    "size": "1024x1024",
                    "quality": "low",
                    "n": 1,
                    "background": "transparent",
                    "output_format": "png",
                },
                [output],
                client=client,
                force=False,
                dry_run=False,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["transparent_verified"])
            self.assertEqual(output.read_bytes(), self.transparent_png)
            self.assertEqual(_ImageHandler.seen_headers["authorization"], "Bearer test-secret")
            sent = json.loads(_ImageHandler.seen_body.decode("utf-8"))
            self.assertEqual(sent["background"], "transparent")

    def test_failed_transparency_validation_does_not_write_final_file(self):
        _ImageHandler.response_body = {
            "data": [{"b64_json": base64.b64encode(self.opaque_png).decode("ascii")}]
        }
        provider = {
            "base_url": f"http://127.0.0.1:{self.server.server_port}/v1",
            "http_headers": {"Authorization": "Bearer test-secret"},
        }
        client = imagegen.ImageClient(provider, imagegen.resolve_headers(provider), timeout=10)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transparent.png"
            with self.assertRaises(imagegen.ImagegenError) as error:
                imagegen.run_generate(
                    "isolated orange fox",
                    {
                        "model": "gpt-image-2",
                        "size": "1024x1024",
                        "quality": "low",
                        "n": 1,
                        "background": "transparent",
                        "output_format": "png",
                    },
                    [output],
                    client=client,
                    force=False,
                    dry_run=False,
                )
            self.assertEqual(error.exception.code, "transparent_alpha_missing")
            self.assertFalse(output.exists())

    def test_dry_run_does_not_read_credential_cache(self):
        args = imagegen.parse_arguments(
            [
                "generate",
                "--prompt",
                "test",
                "--background",
                "transparent",
                "--output-format",
                "png",
                "--out",
                "dry-run.png",
                "--dry-run",
            ]
        )
        with patch.object(imagegen, "load_cached_provider", side_effect=AssertionError("must not read")):
            result = imagegen.execute_generate(args)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["request"]["background"], "transparent")

    def test_multipart_accepts_repeated_image_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.png"
            path.write_bytes(self.opaque_png)
            body, content_type = imagegen.encode_multipart(
                {"model": "gpt-image-2", "prompt": "edit"},
                [("image[]", path), ("image[]", path)],
            )
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b'name="image[]"', body)

    def test_multipart_accepts_multiline_prompt_and_sanitizes_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input\nimage.png"
            path.write_bytes(self.opaque_png)
            body, _content_type = imagegen.encode_multipart(
                {"prompt": "line one\nline two"},
                [("image[]", path)],
            )
        self.assertIn(b"line one\nline two", body)
        self.assertNotIn(b'filename="input\nimage.png"', body)

    def test_truncated_raster_payloads_are_rejected(self):
        with self.assertRaises(imagegen.ImagegenError):
            imagegen.validate_image_bytes(self.opaque_png[:-12], "png", False)
        corrupt_png = bytearray(self.opaque_png)
        corrupt_png[16] ^= 1
        with self.assertRaises(imagegen.ImagegenError):
            imagegen.validate_image_bytes(bytes(corrupt_png), "png", False)
        with self.assertRaises(imagegen.ImagegenError):
            imagegen.validate_image_bytes(b"\xff\xd8\xff", "jpeg", False)
        with self.assertRaises(imagegen.ImagegenError):
            imagegen.validate_image_bytes(b"RIFF\x04\x00\x00\x00WEBP", "webp", False)

    def test_batch_preflights_all_jobs_before_resolving_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "prompts.jsonl"
            batch.write_text(
                json.dumps({"prompt": "first"}) + "\n" + json.dumps({"prompt": "bad", "out": "../escape.png"}) + "\n",
                encoding="utf-8",
            )
            args = imagegen.parse_arguments(
                ["generate-batch", "--input", str(batch), "--out-dir", str(root / "outputs")]
            )
            with patch.object(imagegen, "_client") as client, patch.object(imagegen, "run_generate") as run:
                with self.assertRaises(imagegen.ImagegenError) as error:
                    imagegen.execute_batch(args)
            self.assertEqual(error.exception.code, "batch_output_name_invalid")
            client.assert_not_called()
            run.assert_not_called()

    def test_batch_rejects_empty_output_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "prompts.jsonl"
            batch.write_text(json.dumps({"prompt": "first", "out": ""}) + "\n", encoding="utf-8")
            args = imagegen.parse_arguments(
                ["generate-batch", "--input", str(batch), "--out-dir", str(root / "outputs")]
            )
            with self.assertRaises(imagegen.ImagegenError) as error:
                imagegen.execute_batch(args)
        self.assertEqual(error.exception.code, "batch_output_name_invalid")

    def test_batch_runtime_failure_reports_completed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "prompts.jsonl"
            batch.write_text(
                json.dumps({"prompt": "first"}) + "\n" + json.dumps({"prompt": "second"}) + "\n",
                encoding="utf-8",
            )
            args = imagegen.parse_arguments(
                ["generate-batch", "--input", str(batch), "--out-dir", str(root / "outputs")]
            )
            first = {"ok": True, "files": [{"path": str(root / "outputs" / "image_1.png"), "bytes": 12}]}
            with patch.object(imagegen, "_client", return_value=object()), patch.object(
                imagegen,
                "run_generate",
                side_effect=[first, imagegen.ImagegenError("http", "http_error", http_status=500)],
            ):
                with self.assertRaises(imagegen.ImagegenError) as error:
                    imagegen.execute_batch(args)
        self.assertEqual(error.exception.code, "http_error")
        self.assertEqual(error.exception.diagnostic["completed_files"], first["files"])

    def test_atomic_no_clobber_handles_creation_race(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.png"
            with patch.object(imagegen.os, "link", side_effect=FileExistsError):
                with self.assertRaises(imagegen.ImagegenError) as error:
                    imagegen._write_atomic(target, self.opaque_png, force=False)
        self.assertEqual(error.exception.code, "output_exists")


if __name__ == "__main__":
    unittest.main()
