from __future__ import annotations

import json
import os
from pathlib import Path
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
    ConfigError,
    FileContextError,
    MAX_FILE_BYTES,
    MAX_TOTAL_CHARS,
    REQUEST_SCHEMA,
    SYSTEM_PROMPT,
)
from server import TOOL_DEFINITIONS, handle  # noqa: E402


class FakeResponse:
    def __init__(self, payload: object, status: int = 200, request_id: str | None = None) -> None:
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status
        self.headers = {"x-request-id": request_id} if request_id else {}

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
        self.assertEqual(preview["payload"]["input"], preview["prompt"])
        opener = FakeOpener([FakeResponse({"output_text": "verbatim"})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            self.bridge(opener).creative_generate(self.request(temperature=0.4))
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["input"], preview["payload"]["input"])
        self.assertEqual(body["instructions"], SYSTEM_PROMPT)

    def test_system_none_omits_instruction_and_absent_temperature_is_omitted(self) -> None:
        opener = FakeOpener([FakeResponse({"output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]})])
        with patch.dict(os.environ, {"BRIDGE_TEST_KEY": "placeholder-key"}):
            self.bridge(opener).creative_generate(self.request(system_mode="none"))
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("instructions", body)
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
        self.assertEqual(payload_schema["required"], ["model", "input", "max_output_tokens"])
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
        wrong = config_file(self.root, wire_api="chat_completions")
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
