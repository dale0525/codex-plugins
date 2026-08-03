from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))

from bridge import (  # noqa: E402
    BRIDGE_VERSION,
    Bridge,
    BridgeError,
    ConfigLoader,
    ConfigError,
    FileContextError,
    MAX_FILE_BYTES,
    MAX_TOTAL_CHARS,
    REQUEST_SCHEMA,
    SYSTEM_PROMPT,
    TransportDiagnostic,
    _extract_output_text,
)
from server import TOOL_DEFINITIONS, handle  # noqa: E402


class FakeResponse:
    def __init__(self, payload: object, status: int = 200, request_id: str | None = None) -> None:
        if isinstance(payload, dict) and "choices" not in payload and ("output_text" in payload or "output" in payload):
            try:
                payload = {
                    "id": payload.get("id", request_id or "fixture-response"),
                    "choices": [{"message": {"content": _extract_output_text(payload)}}],
                    **({"usage": payload["usage"]} if isinstance(payload.get("usage"), dict) else {}),
                }
            except BridgeError:
                pass
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        if request_id:
            self.headers["x-request-id"] = request_id

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[object] = []

    def __call__(self, request: object, timeout: float) -> FakeResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def config_file(root: Path, *, provider: str = "provider-a", default: str | None = "default/opaque:model", wire_api: str = "responses", env_key: str | None = "BRIDGE_TEST_KEY", bearer: str | None = None) -> Path:
    default_line = f'CREATIVE_MODEL_DEFAULT = "{default}"\n' if default is not None else ""
    env_line = f'env_key = "{env_key}"\n' if env_key is not None else ""
    bearer_line = f'experimental_bearer_token = "{bearer}"\n' if bearer is not None else ""
    path = root / "config.toml"
    path.write_text(
        "[shell_environment_policy.set]\n"
        f'CREATIVE_MODEL_PROVIDER = "{provider}"\n'
        + default_line
        + "\n[model_providers."
        + provider
        + "]\n"
        + 'base_url = "https://provider.test/v1"\n'
        + f'wire_api = "{wire_api}"\n'
        + env_line
        + bearer_line,
        encoding="utf-8",
    )
    return path


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="creative-bridge-")
        self.root = Path(self.temp.name)
        self.config = config_file(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def bridge(self, opener: FakeOpener | None = None) -> Bridge:
        return Bridge(self.config, opener=opener)

    def test_config_path_precedence_and_home_fallback(self) -> None:
        explicit = self.root / "explicit.toml"
        codex_home = self.root / "codex-home"
        default_home = self.root / "default-home"
        explicit.write_text("", encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
            self.assertEqual(ConfigLoader(explicit)._path(), explicit)
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
            self.assertEqual(ConfigLoader()._path(), codex_home / "config.toml")
        with patch.dict(os.environ, {}, clear=True), patch("bridge.Path.home", return_value=default_home):
            self.assertEqual(ConfigLoader()._path(), default_home / ".codex/config.toml")
        with patch.dict(os.environ, {"CODEX_HOME": ""}, clear=True), patch("bridge.Path.home", return_value=default_home):
            self.assertEqual(ConfigLoader()._path(), default_home / ".codex/config.toml")

    def request(self, **extra: object) -> dict[str, object]:
        value: dict[str, object] = {"task": "写一个短场景", "context_text": [{"label": "source", "text": "原始材料"}]}
        value.update(extra)
        return value

    def test_default_and_explicit_opaque_model_precedence(self) -> None:
        opener = FakeOpener([FakeResponse({"output_text": "成稿"}, request_id="resp-1")])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            result = self.bridge(opener).creative_generate(self.request())
        self.assertEqual(result["model"], "default/opaque:model")
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["model"], "default/opaque:model")

        opener = FakeOpener([FakeResponse({"output_text": "成稿"}, request_id="resp-2")])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            result = self.bridge(opener).creative_generate(self.request(model="vendor/name:v9+opaque"))
        self.assertEqual(result["model"], "vendor/name:v9+opaque")

    def test_preview_and_generate_have_identical_prompt_and_preview_is_offline(self) -> None:
        preview = self.bridge(FakeOpener([])).creative_preview(self.request(temperature=0.4))
        self.assertFalse(preview["network"])
        self.assertEqual(preview["payload"]["messages"][-1]["content"], preview["prompt"])
        opener = FakeOpener([FakeResponse({"output_text": "verbatim"})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            self.bridge(opener).creative_generate(self.request(temperature=0.4))
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["messages"][-1]["content"], preview["payload"]["messages"][-1]["content"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM_PROMPT})

    def test_default_max_output_tokens_is_60000_and_explicit_value_wins(self) -> None:
        default_preview = self.bridge().creative_preview(self.request())
        self.assertEqual(default_preview["payload"]["max_tokens"], 60000)
        explicit_preview = self.bridge().creative_preview(self.request(max_output_tokens=123))
        self.assertEqual(explicit_preview["payload"]["max_tokens"], 123)

    def test_system_none_omits_instruction_and_absent_temperature_is_omitted(self) -> None:
        opener = FakeOpener([FakeResponse({"output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            self.bridge(opener).creative_generate(self.request(system_mode="none"))
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("system", {message["role"] for message in body["messages"]})
        self.assertNotIn("temperature", body)
        self.assertNotIn("Codex", json.dumps(body, ensure_ascii=False))

    def test_file_order_encoding_hash_and_report(self) -> None:
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_bytes("第一".encode("gb18030"))
        second.write_bytes(b"\xff\xfeN\x00e\x00")
        result = self.bridge().creative_preview(self.request(context_files=[str(second), str(first)]))
        report = result["prompt_report"]["context_files"]
        self.assertEqual([item["path"] for item in report], [str(second.resolve()), str(first.resolve())])
        self.assertEqual(report[0]["encoding"], "utf-16")
        self.assertIn(report[1]["encoding"], {"gb18030", "euc_kr"})
        self.assertIn("N", result["prompt"])
        self.assertIn("第一", result["prompt"])
        self.assertEqual(report[0]["chars"], 2)

    def test_supported_legacy_encodings_decode_without_binary_fallback(self) -> None:
        samples = {
            "gb18030.txt": ("简体中文", "gb18030"),
            "big5.txt": ("繁體中文", "big5"),
            "shift-jis.txt": ("日本語かな", "shift_jis"),
            "euc-kr.txt": ("한국어", "euc_kr"),
        }
        for filename, (text, encoding) in samples.items():
            path = self.root / filename
            path.write_bytes(text.encode(encoding))
            preview = self.bridge().creative_preview(self.request(context_files=[str(path)]))
            self.assertIn(text, preview["prompt"])

    def test_relative_context_file_is_rejected_at_runtime(self) -> None:
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=["relative.txt"]))

    def test_binary_signatures_and_high_byte_noise_are_rejected(self) -> None:
        samples = {
            "image.png": b"\x89PNG\r\n\x1a\n" + b"x" * 64,
            "archive.zip": b"PK\x03\x04" + b"x" * 64,
            "document.pdf": b"%PDF-1.7\n" + b"x" * 64,
            "noise.bin": bytes([0xC8]) * 128,
        }
        for filename, raw in samples.items():
            path = self.root / filename
            path.write_bytes(raw)
            with self.assertRaises(FileContextError):
                self.bridge().creative_preview(self.request(context_files=[str(path)]))

    def test_valid_utf8_c1_controls_are_rejected_as_binary(self) -> None:
        path = self.root / "c1-control.txt"
        path.write_bytes("visible".encode("utf-8") + b"\xc2\x85" + "text".encode("utf-8"))
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=[str(path)]))

    def test_symlink_is_not_followed_when_platform_supports_no_follow(self) -> None:
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("platform has no O_NOFOLLOW")
        target = self.root / "target.txt"
        link = self.root / "link.txt"
        target.write_text("safe", encoding="utf-8")
        link.symlink_to(target)
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=[str(link)]))

    def test_file_limits_and_non_regular_rejected(self) -> None:
        too_big = self.root / "large.txt"
        too_big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=[str(too_big)]))
        first = self.root / "one.txt"
        second = self.root / "two.txt"
        first.write_text("x" * (MAX_TOTAL_CHARS // 2 + 1), encoding="utf-8")
        second.write_text("x" * (MAX_TOTAL_CHARS // 2), encoding="utf-8")
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=[str(first), str(second)]))
        with self.assertRaises(FileContextError):
            self.bridge().creative_preview(self.request(context_files=[str(self.root)]))

    def test_final_assembled_prompt_limit_and_mixed_component_accounting(self) -> None:
        exact_task = "x" * (MAX_TOTAL_CHARS - len("任务:\n"))
        preview = self.bridge().creative_preview({"task": exact_task})
        self.assertEqual(preview["prompt_report"]["user_chars"], MAX_TOTAL_CHARS)
        with self.assertRaises(BridgeError):
            self.bridge().creative_preview({"task": exact_task + "x"})
        mixed = {
            "task": "t" * 46_000,
            "constraints": ["c" * 46_000],
            "output_spec": "o" * 46_000,
            "context_text": [{"label": "material", "text": "m" * 46_000}],
        }
        with self.assertRaises(BridgeError):
            self.bridge().creative_preview(mixed)

    def test_prompt_order_report_and_schema_match_runtime(self) -> None:
        request = self.request(
            constraints=["constraint"],
            output_spec={"format": "markdown"},
        )
        preview = self.bridge().creative_preview(request)
        prompt = preview["prompt"]
        self.assertLess(prompt.index("任务:"), prompt.index("约束:"))
        self.assertLess(prompt.index("约束:"), prompt.index("输出规格:"))
        self.assertLess(prompt.index("输出规格:"), prompt.index("上下文文字:"))
        report = preview["prompt_report"]
        self.assertEqual(report["system_prompt"], SYSTEM_PROMPT)
        self.assertEqual(report["section_order"], ["task", "constraints", "output_spec", "context_text", "context_files"])
        self.assertEqual(report["user_chars"], len(prompt))
        self.assertFalse(report["truncated"])
        self.assertEqual(TOOL_DEFINITIONS[1]["inputSchema"], REQUEST_SCHEMA)
        self.assertEqual(TOOL_DEFINITIONS[2]["inputSchema"], REQUEST_SCHEMA)
        self.assertFalse(REQUEST_SCHEMA["properties"]["context_text"]["items"]["additionalProperties"])
        self.assertIn("outputSchema", TOOL_DEFINITIONS[0])
        self.assertIn("outputSchema", TOOL_DEFINITIONS[1])
        self.assertIn("outputSchema", TOOL_DEFINITIONS[2])
        schemas = [tool["outputSchema"] for tool in TOOL_DEFINITIONS]
        self.assertEqual(
            [set(schema["properties"]) for schema in schemas],
            [
                {"text", "provider", "model", "usage", "request_id", "prompt_report", "models"},
                {"text", "provider", "model", "usage", "request_id", "prompt_report", "prompt", "payload", "network"},
                {"text", "provider", "model", "usage", "request_id", "prompt_report"},
            ],
        )
        self.assertTrue(all(schema["additionalProperties"] is False for schema in schemas))
        self.assertEqual(len({id(schema) for schema in schemas}), 3)
        prompt_schema = schemas[2]["properties"]["prompt_report"]
        self.assertFalse(prompt_schema["additionalProperties"])
        self.assertFalse(prompt_schema["properties"]["context_text"]["items"]["additionalProperties"])
        self.assertFalse(prompt_schema["properties"]["context_files"]["items"]["additionalProperties"])
        payload_schema = schemas[1]["properties"]["payload"]
        self.assertFalse(payload_schema["additionalProperties"])
        self.assertEqual(payload_schema["required"], ["model", "messages", "max_tokens", "stream", "stream_options"])
        with self.assertRaises(BridgeError):
            self.bridge().creative_preview(self.request(context_text=[{"label": "x", "text": "y", "extra": 1}]))
        with self.assertRaises(BridgeError):
            self.bridge().creative_preview(self.request(unknown=1))

    def test_secret_is_not_in_results_or_errors(self) -> None:
        secret = "not-a-secret-value"
        config = config_file(self.root, env_key=None, bearer=secret)
        opener = FakeOpener([FakeResponse({"output_text": "ok"})])
        with patch.dict(os.environ, {}, clear=True):
            result = Bridge(config, opener=opener).creative_generate(self.request())
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        missing = config_file(self.root, env_key="MISSING_BRIDGE_KEY", bearer=None)
        with self.assertRaises(ConfigError) as context:
            Bridge(missing).creative_generate(self.request())
        self.assertNotIn(secret, str(context.exception))

    def test_missing_env_key_does_not_fall_back_to_development_bearer(self) -> None:
        config = config_file(self.root, env_key="MISSING_BRIDGE_KEY", bearer="development-value")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ConfigError):
            self.bridge().creative_generate(self.request())

    def test_configured_env_key_prefers_environment_over_dev_field(self) -> None:
        config = config_file(self.root, env_key="BRIDGE_TEST_KEY", bearer="development-value")
        opener = FakeOpener([FakeResponse({"output_text": "ok"})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "environment-value"}, clear=True):
            Bridge(config, opener=opener).creative_generate(self.request())
        self.assertEqual(opener.requests[0].headers["Authorization"], "Bearer environment-value")

    def test_fixed_channel_is_fallback_for_configured_env_key(self) -> None:
        config = config_file(self.root, env_key="BRIDGE_TEST_KEY", bearer="development-value")
        opener = FakeOpener([FakeResponse({"output_text": "ok"})])
        with patch.dict(os.environ, {"CREATIVE_MODEL_API_KEY": "fixed-channel"}, clear=True):
            Bridge(config, opener=opener).creative_generate(self.request())
        self.assertEqual(opener.requests[0].headers["Authorization"], "Bearer fixed-channel")

    def test_configured_env_key_wins_over_fixed_channel(self) -> None:
        config = config_file(self.root, env_key="BRIDGE_TEST_KEY")
        opener = FakeOpener([FakeResponse({"output_text": "ok"})])
        with patch.dict(
            os.environ,
            {"BRIDGE_TEST_KEY": "provider-channel", "CREATIVE_MODEL_API_KEY": "fixed-channel"},
            clear=True,
        ):
            Bridge(config, opener=opener).creative_generate(self.request())
        self.assertEqual(opener.requests[0].headers["Authorization"], "Bearer provider-channel")

    def test_fixed_channel_wins_over_development_bearer_without_env_key(self) -> None:
        config = config_file(self.root, env_key=None, bearer="development-value")
        opener = FakeOpener([FakeResponse({"output_text": "ok"})])
        with patch.dict(os.environ, {"CREATIVE_MODEL_API_KEY": "fixed-channel"}, clear=True):
            Bridge(config, opener=opener).creative_generate(self.request())
        self.assertEqual(opener.requests[0].headers["Authorization"], "Bearer fixed-channel")

    def test_models_uses_provider_response_without_inventing(self) -> None:
        opener = FakeOpener([FakeResponse({"object": "list", "data": [{"id": "opaque-one"}, {"id": "opaque-two"}]}, request_id="models-1")])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            result = self.bridge(opener).creative_models()
        self.assertEqual(result["models"], ["opaque-one", "opaque-two"])
        self.assertEqual(result["request_id"], "models-1")
        self.assertEqual(opener.requests[0].method, "GET")

    def test_missing_provider_and_wrong_wire_api(self) -> None:
        path = config_file(self.root, provider="missing", env_key="BRIDGE_TEST_KEY")
        path.write_text(path.read_text(encoding="utf-8").replace("[model_providers.missing]", "[model_providers.other]"), encoding="utf-8")
        with self.assertRaises(ConfigError):
            Bridge(path).creative_preview(self.request())
        wrong = config_file(self.root, wire_api="unsupported")
        with self.assertRaises(ConfigError):
            Bridge(wrong).creative_preview(self.request())

    def test_toml_quoted_and_dotted_provider_keys_use_tomllib(self) -> None:
        config = self.root / "quoted.toml"
        config.write_text(
            "[shell_environment_policy.set]\n"
            'CREATIVE_MODEL_PROVIDER = "quoted.provider"\n'
            'CREATIVE_MODEL_DEFAULT = "vendor/model:opaque"\n\n'
            '[model_providers."quoted.provider"]\n'
            'base_url = "https://provider.test/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "BRIDGE_TEST_KEY"\n',
            encoding="utf-8",
        )
        result = Bridge(config).creative_preview({"task": "quoted"})
        self.assertEqual(result["provider"], "quoted.provider")
        self.assertEqual(result["model"], "vendor/model:opaque")

    def test_http_failures_no_retry_and_malformed_payload(self) -> None:
        for error, expected in [
            (urllib.error.HTTPError("https://provider.test/v1/responses", 401, "", {}, None), "401"),
            (urllib.error.HTTPError("https://provider.test/v1/responses", 429, "", {}, None), "429"),
            (TimeoutError("timed out"), "timed out"),
        ]:
            opener = FakeOpener([error])
            with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
                self.bridge(opener).creative_generate(self.request())
            self.assertIn(expected, str(context.exception))
            self.assertEqual(len(opener.requests), 1)
        opener = FakeOpener([FakeResponse({"output": []})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError):
            self.bridge(opener).creative_generate(self.request())

    def test_transport_diagnostics_are_opt_in_typed_and_value_free(self) -> None:
        verify_error = ssl.SSLCertVerificationError("secret verify_message")
        verify_error.verify_code = 20
        verify_error.verify_message = "secret verify_message"
        opener = FakeOpener([urllib.error.URLError(verify_error)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
            Bridge(self.config, opener=opener, transport_diagnostics=True).creative_generate(self.request())
        diagnostic = context.exception.transport_diagnostic
        self.assertIsInstance(diagnostic, TransportDiagnostic)
        assert diagnostic is not None
        self.assertEqual(
            set(diagnostic.as_dict()),
            {"phase", "outer_type", "reason_type", "errno", "ssl_verify_code", "ssl_reason"},
        )
        self.assertEqual(diagnostic.phase, "responses")
        self.assertEqual(diagnostic.outer_type, "URLError")
        self.assertEqual(diagnostic.reason_type, "SSLCertVerificationError")
        self.assertEqual(diagnostic.ssl_verify_code, 20)
        self.assertEqual(diagnostic.ssl_reason, "UNABLE_TO_GET_ISSUER")
        rendered = json.dumps(diagnostic.as_dict(), ensure_ascii=False)
        self.assertNotIn("secret verify_message", rendered)
        self.assertNotIn("provider.test", rendered)

        opener = FakeOpener([urllib.error.URLError(verify_error)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
            self.bridge(opener).creative_generate(self.request())
        self.assertIsNone(context.exception.transport_diagnostic)
        self.assertNotIn("secret verify_message", str(context.exception))

    def test_transport_diagnostics_redact_secret_exception_chain_and_preserve_errno(self) -> None:
        secret_error = OSError(111, "provider secret body")
        opener = FakeOpener([urllib.error.URLError(secret_error)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
            Bridge(self.config, opener=opener, transport_diagnostics=True).creative_models()
        diagnostic = context.exception.transport_diagnostic
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.phase, "models")
        self.assertEqual(diagnostic.reason_type, "OSError")
        self.assertEqual(diagnostic.errno, 111)
        rendered = json.dumps(diagnostic.as_dict(), ensure_ascii=False)
        self.assertNotIn("provider secret body", rendered)
        self.assertNotIn("provider.test", rendered)
        self.assertNotIn("provider secret body", str(context.exception))

    def test_responses_compatible_text_shapes_preserve_order_and_verbatim_text(self) -> None:
        cases = [
            (
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "A"},
                                {"type": "output_text", "text": "\n B"},
                            ],
                        }
                    ]
                },
                "A\n B",
            ),
            (
                {"output": [{"type": "message", "content": "A"}, {"type": "text", "text": "B"}]},
                "AB",
            ),
            (
                {
                    "output": [
                        {
                            "type": "message",
                            "content": {
                                "type": "text",
                                "text": {"value": "A"},
                            },
                        }
                    ]
                },
                "A",
            ),
            (
                {
                    "choices": [
                        {"message": {"content": "A"}},
                        {"delta": {"content": [{"type": "text", "text": "B"}]}},
                    ]
                },
                "A",
            ),
            ({"output_text": ["A", "\nB"]}, "A\nB"),
            ({"response": {"data": {"output_text": "wrapped"}}}, "wrapped"),
            (
                {
                    "response": {
                        "reasoning": {"summary": [{"type": "summary_text", "text": "hidden"}]},
                        "output": [{"type": "message", "role": "assistant", "content": [{"type": "text", "text": "normal"}]}],
                    }
                },
                "normal",
            ),
            (
                {"result": {"tools": [{"type": "function", "name": "lookup"}], "user": {"id": "opaque"}, "output_text": "normal"}},
                "normal",
            ),
            (
                {
                    "reasoning": {"summary": [{"type": "summary_text", "text": "hidden"}]},
                    "tools": [{"type": "function", "name": "lookup"}],
                    "user": {"id": "opaque-user"},
                    "output_text": "normal",
                },
                "normal",
            ),
            ({"output_text": "normal", "reasoning": None}, "normal"),
            (
                {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "refusal": None,
                            "content": [{"type": "output_text", "text": "normal"}],
                        }
                    ]
                },
                "normal",
            ),
        ]
        for payload, expected in cases:
            self.assertEqual(_extract_output_text(payload), expected)

    def test_response_status_and_error_gate_prevents_provider_errors_becoming_text(self) -> None:
        secret = "provider failure details must not appear"
        payload = {
            "id": "provider-failure-id",
            "status": "failed",
            "error": {"message": secret},
            "output": [{"type": "message", "content": [{"type": "text", "text": "provider message"}]}],
        }
        opener = FakeOpener([FakeResponse(payload)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
            self.bridge(opener).creative_generate(self.request())
        diagnostic = str(context.exception)
        self.assertNotIn(secret, diagnostic)
        self.assertIn("response_status", diagnostic)
        self.assertIn("failed", diagnostic)

        opener = FakeOpener([FakeResponse(payload)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            response = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "creative_generate", "arguments": self.request()},
                },
                self.bridge(opener),
            )
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(secret, response["result"]["content"][0]["text"])

    def test_non_completed_response_statuses_block_text_but_incomplete_text_is_allowed(self) -> None:
        for status in ("cancelled", "queued", "in_progress"):
            with self.assertRaises(BridgeError):
                _extract_output_text({"status": status, "text": "must not be returned"})
        with self.assertRaises(BridgeError):
            _extract_output_text({"status": "completed", "error": {"message": "provider error"}, "output_text": "not text"})
        self.assertEqual(_extract_output_text({"status": "incomplete", "output_text": "partial text"}), "partial text")

    def test_nested_response_envelopes_gate_status_error_and_propagate_mcp_safely(self) -> None:
        secret = "nested provider error must not appear"
        rejected = [
            {"response": {"status": "failed", "error": {"message": secret}, "output_text": "bad"}},
            {"result": {"status": "cancelled", "output_text": "bad"}},
            {"data": {"status": "queued", "output_text": "bad"}},
        ]
        for payload in rejected:
            with self.assertRaises(BridgeError) as context:
                _extract_output_text(payload, request_id="nested-failure", http_status=200)
            self.assertIn("response_status", str(context.exception))
        with self.assertRaises(BridgeError) as context:
            _extract_output_text(rejected[0], request_id="nested-failure", http_status=200)
        self.assertIn('"response_status":"failed"', str(context.exception))
        self.assertNotIn(secret, str(context.exception))

        opener = FakeOpener([FakeResponse(rejected[0])])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            response = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "creative_generate", "arguments": self.request()},
                },
                self.bridge(opener),
            )
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(secret, response["result"]["content"][0]["text"])

        self.assertEqual(
            _extract_output_text({"response": {"status": "incomplete", "output_text": "partial"}}),
            "partial",
        )

    def test_array_items_gate_status_error_and_propagate_mcp_safely(self) -> None:
        secret = "array provider failure must not appear"
        payloads = [
            ({"response": [{"status": "failed", "output_text": secret}]}, "failed"),
            ({"result": [{"status": "cancelled", "output_text": secret}]}, "cancelled"),
            ({"data": [{"status": "queued", "output_text": secret}]}, "queued"),
            ({"choices": [{"status": "in_progress", "text": secret}]}, "in_progress"),
            ({"output": [{"error": {"message": secret}, "output_text": secret}]}, None),
        ]
        for payload, status in payloads:
            with self.assertRaises(BridgeError) as context:
                _extract_output_text(payload, request_id="array-failure", http_status=200)
            diagnostic = str(context.exception)
            self.assertNotIn(secret, diagnostic)
            if status is not None:
                self.assertIn(f'"response_status":"{status}"', diagnostic)

        opener = FakeOpener([FakeResponse(payloads[0][0])])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            response = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "creative_generate", "arguments": self.request()},
                },
                self.bridge(opener),
            )
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn(secret, response["result"]["content"][0]["text"])

        self.assertEqual(
            _extract_output_text({"response": [{"status": "incomplete", "output_text": "partial"}]}),
            "partial",
        )

    def test_empty_response_error_contains_safe_shape_diagnostic_and_mcp_propagates_it(self) -> None:
        secret_text = "COMPLETE_CREATIVE_TEXT_SHOULD_NOT_APPEAR"
        secret_key = "provider-secret-value"
        payload = {
            "id": "response-diagnostic-1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "text": secret_text}],
                },
                {
                    "type": "tool_call",
                    "content": {"type": "function_call_output", "output": secret_key},
                },
            ],
            "api_key": secret_key,
        }
        opener = FakeOpener([FakeResponse(payload, status=207)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}), self.assertRaises(BridgeError) as context:
            self.bridge(opener).creative_generate(self.request())
        diagnostic = str(context.exception)
        self.assertIn("request_id", diagnostic)
        request_digest = hashlib.sha256(b"response-diagnostic-1").hexdigest()
        self.assertIn(f'"request_id_sha256":"{request_digest}"', diagnostic)
        self.assertNotIn("response-diagnostic-1", diagnostic)
        self.assertIn("\"http_status\":207", diagnostic)
        self.assertIn("\"response_status\":\"completed\"", diagnostic)
        self.assertIn("top_level_fields", diagnostic)
        self.assertIn("output", diagnostic)
        self.assertIn("content", diagnostic)
        self.assertIn("fields", diagnostic)
        self.assertIn("refusal", diagnostic)
        self.assertNotIn(secret_text, diagnostic)
        self.assertNotIn(secret_key, diagnostic)

        opener = FakeOpener([FakeResponse(payload, status=207)])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            response = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "creative_generate", "arguments": self.request()},
                },
                self.bridge(opener),
            )
        self.assertTrue(response["result"]["isError"])
        mcp_text = response["result"]["content"][0]["text"]
        self.assertIn(f'"request_id_sha256":"{request_digest}"', mcp_text)
        self.assertNotIn("response-diagnostic-1", mcp_text)
        self.assertIn("top_level_fields", mcp_text)
        self.assertNotIn(secret_text, mcp_text)
        self.assertNotIn(secret_key, mcp_text)

    def test_unknown_response_status_and_error_values_are_not_leaked(self) -> None:
        status_secret = "MALICIOUS_STATUS_VALUE_SHOULD_NOT_APPEAR"
        error_secret = "PROVIDER_ERROR_TEXT_SHOULD_NOT_APPEAR"
        payload = {
            "id": "response-incomplete-1",
            "status": status_secret,
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": error_secret}],
                }
            ],
            "incomplete_details": {"reason": error_secret},
            "error": {"code": "provider_error", "message": error_secret},
        }
        for malicious_status in (status_secret, [status_secret], {"value": status_secret}):
            payload["status"] = malicious_status
            with self.assertRaises(BridgeError) as context:
                _extract_output_text(payload, request_id="response-incomplete-1", http_status=200)
            diagnostic = str(context.exception)
            self.assertIn('"http_status":200', diagnostic)
            self.assertIn('"response_status":null', diagnostic)
            self.assertIn("incomplete_details", diagnostic)
            self.assertIn("error", diagnostic)
            self.assertIn("summary", diagnostic)
            self.assertNotIn(status_secret, diagnostic)
            self.assertNotIn(error_secret, diagnostic)

    def test_non_assistant_roles_and_tool_markers_are_never_extracted(self) -> None:
        rejected = [
            {"type": "reasoning", "text": "reasoning"},
            {"type": "tool_call", "output_text": "tool"},
            {"response": {"type": "reasoning", "content": "reasoning"}},
            {"response": {"role": "user", "content": "user"}},
            {"response": {"role": None, "content": "unknown role"}},
            {"response": {"tool_call_id": "opaque", "text": "tool"}},
            {"result": {"function_output": "opaque", "text": "function"}},
            {"data": {"refusal": "non-empty refusal", "content": "refusal"}},
            {"response": {"reasoning_content": "opaque", "text": "reasoning"}},
            {"tool_call_id": "opaque", "text": "tool"},
            {"function_output": "opaque", "text": "function"},
            {"refusal": "non-empty refusal", "content": "refusal"},
            {"reasoning_content": "opaque", "text": "reasoning"},
            {"output": [{"role": "tool", "text": "tool output"}]},
            {"output": [{"role": "system", "content": [{"type": "text", "text": "system"}]}]},
            {"output": [{"role": "developer", "text": "developer"}]},
            {"output": [{"role": "user", "text": "user"}]},
            {"output": [{"role": None, "text": "unknown role"}]},
            {"output": [{"type": "tool", "text": "tool"}]},
            {"output": [{"type": "refusal", "text": "refusal"}]},
            {"output": [{"type": "reasoning", "summary": [{"type": "text", "text": "reasoning"}]}]},
            {"output": [{"summary": [{"type": "text", "text": "reasoning"}], "text": "reasoning"}]},
            {"output": [{"tool_call_id": "opaque", "text": "tool"}]},
            {"output": [{"type": "function_output", "text": "function output"}]},
            {"output_text": {"type": "refusal", "value": "refusal"}},
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"role": "tool", "type": "text", "text": "nested tool"}],
                    }
                ]
            },
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "refusal": "non-empty refusal",
                        "content": [{"type": "text", "text": "should not extract"}],
                    }
                ]
            },
        ]
        for payload in rejected:
            with self.assertRaises(BridgeError):
                _extract_output_text(payload)

        nested_assistant = {
            "output": [
                {
                    "role": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"role": "assistant", "type": "text", "text": "assistant only"}],
                    },
                }
            ]
        }
        self.assertEqual(_extract_output_text(nested_assistant), "assistant only")

    def test_request_id_uuid_is_preserved_but_other_ids_are_fingerprinted(self) -> None:
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        with self.assertRaises(BridgeError) as context:
            _extract_output_text({"output": []}, request_id=uuid, http_status=200)
        diagnostic = str(context.exception)
        self.assertIn(f'"request_id":"{uuid}"', diagnostic)
        self.assertNotIn("request_id_sha256", diagnostic)

    def test_wrapper_reasoning_diagnostic_descends_known_fields_without_values(self) -> None:
        secret = "wrapper-reasoning-secret"
        payload = {
            "result": {
                "data": {
                    "response": {
                        "type": "reasoning",
                        "content": [{"type": "text", "text": secret}],
                        "private_wrapper_key": secret,
                    }
                }
            }
        }
        with self.assertRaises(BridgeError) as context:
            _extract_output_text(payload, request_id="wrapper-id", http_status=200)
        diagnostic = str(context.exception)
        self.assertIn("result", diagnostic)
        self.assertIn("data", diagnostic)
        self.assertIn("response", diagnostic)
        self.assertIn("content", diagnostic)
        self.assertIn("reasoning", diagnostic)
        self.assertIn("<unknown_fields:", diagnostic)
        self.assertNotIn(secret, diagnostic)
        self.assertNotIn("private_wrapper_key", diagnostic)

    def test_response_shape_diagnostic_is_bounded_and_redacts_unknown_keys(self) -> None:
        deep: dict[str, object] = {"type": "reasoning"}
        cursor = deep
        for _ in range(20):
            child: dict[str, object] = {"type": "reasoning"}
            cursor["content"] = [child]
            cursor = child
        payload: dict[str, object] = {
            "status": "incomplete",
            "output": [deep] * 100,
            "incomplete_details": {"reason": "max_output_tokens"},
        }
        for index in range(10_000):
            payload[f"secret key {index}"] = "response value must not appear"
        with self.assertRaises(BridgeError) as context:
            _extract_output_text(payload, request_id="shape-test", http_status=200)
        diagnostic = str(context.exception)
        self.assertLessEqual(len(diagnostic), 12_000)
        self.assertIn("<unknown_fields:", diagnostic)
        self.assertIn("truncated", diagnostic)
        self.assertNotIn("secret key 0", diagnostic)
        self.assertNotIn("response value must not appear", diagnostic)

    def test_mcp_handle_returns_structured_tool_result(self) -> None:
        bridge = self.bridge()
        response = handle({"jsonrpc": "2.0", "id": 0, "method": "initialize"}, bridge)
        self.assertEqual(response["result"]["serverInfo"]["version"], BRIDGE_VERSION)
        response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bridge)
        self.assertEqual(response["result"]["tools"][0]["name"], "creative_models")
        response = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "creative_preview", "arguments": self.request()}}, bridge)
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["network"], False)


if __name__ == "__main__":
    unittest.main()
