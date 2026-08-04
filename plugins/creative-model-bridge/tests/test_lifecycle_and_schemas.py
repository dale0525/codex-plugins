from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))

from bridge import Bridge  # noqa: E402
from server import TOOL_DEFINITIONS  # noqa: E402


class FakeResponse:
    def __init__(self, payload: object, request_id: str | None = None) -> None:
        if isinstance(payload, dict) and "choices" not in payload and isinstance(payload.get("output_text"), str):
            payload = {
                "id": payload.get("id", request_id or "schema-response"),
                "choices": [{"message": {"content": payload["output_text"]}}],
            }
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = 200
        self.headers = {"Content-Type": "application/json"}
        if request_id:
            self.headers["x-request-id"] = request_id

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses

    def __call__(self, request: object, timeout: float) -> FakeResponse:
        return self.responses.pop(0)


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


def _validate_schema(value: object, schema: dict[str, object], path: str = "$") -> None:
    """Small dependency-free validator for the exact schemas advertised here."""

    if "oneOf" in schema:
        errors: list[str] = []
        for candidate in schema["oneOf"]:
            try:
                _validate_schema(value, candidate, path)  # type: ignore[arg-type]
                break
            except AssertionError as error:
                errors.append(str(error))
        else:
            raise AssertionError(f"{path}: no oneOf branch matched: {errors}")
        return
    if "const" in schema:
        assert value == schema["const"], f"{path}: expected const {schema['const']!r}"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: value is outside enum"

    expected_types = schema.get("type")
    if expected_types is not None:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        assert any(_is_json_type(value, item) for item in expected_types), (
            f"{path}: expected {expected_types}, got {type(value).__name__}"
        )
    if isinstance(value, str):
        if "minLength" in schema:
            assert len(value) >= schema["minLength"], f"{path}: string is too short"
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value), f"{path}: string does not match pattern"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            assert value >= schema["minimum"], f"{path}: number is below minimum"
        if "maximum" in schema:
            assert value <= schema["maximum"], f"{path}: number is above maximum"
    if isinstance(value, dict):
        required = schema.get("required", [])
        assert all(key in value for key in required), f"{path}: required key is missing"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            assert set(value).issubset(properties), f"{path}: unexpected key"
        for key, child_schema in properties.items():
            if key in value:
                _validate_schema(value[key], child_schema, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]")  # type: ignore[arg-type]


def _is_json_type(value: object, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, False)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        metadata = path.lstat()
        digest.update(relative + b"\0" + str(metadata.st_mode).encode("ascii") + b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o555 if os.access(path, os.X_OK) else 0o444)
    root.chmod(0o555)


class OutputSchemaTests(unittest.TestCase):
    def test_context_file_schema_accepts_host_absolute_path_forms(self) -> None:
        schema = TOOL_DEFINITIONS[1]["inputSchema"]["properties"]["context_files"]
        for value in ("/tmp/source.txt", r"C:\\work\\source.txt", r"\\\\server\\share\\source.txt"):
            _validate_schema([value], schema)

    def test_actual_results_validate_and_counterexamples_fail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="creative-schema-") as temporary:
            config = Path(temporary) / "config.toml"
            _config(config)
            models = Bridge(
                config,
                opener=FakeOpener([FakeResponse({"data": [{"id": "opaque-model"}]}, "models-1")]),
            )
            generated = Bridge(
                config,
                opener=FakeOpener([FakeResponse({"output_text": "成稿", "usage": {"total_tokens": 3}}, "response-1")]),
            )
            with patch.dict(os.environ, {"BRIDGE_SCHEMA_KEY": "placeholder-key"}):
                models_result = models.creative_models()
                preview_result = generated.creative_preview({"task": "写作"})
                generated_result = generated.creative_generate({"task": "写作"})

        schemas = {tool["name"]: tool["outputSchema"] for tool in TOOL_DEFINITIONS}
        _validate_schema(models_result, schemas["creative_models"])
        _validate_schema(preview_result, schemas["creative_preview"])
        _validate_schema(generated_result, schemas["creative_generate"])

        invalid_models = copy.deepcopy(models_result)
        invalid_models["model"] = "invented-model"
        with self.assertRaises(AssertionError):
            _validate_schema(invalid_models, schemas["creative_models"])
        invalid_preview = copy.deepcopy(preview_result)
        invalid_preview["prompt_report"]["truncated"] = True
        with self.assertRaises(AssertionError):
            _validate_schema(invalid_preview, schemas["creative_preview"])
        invalid_preview = copy.deepcopy(preview_result)
        invalid_preview["network"] = True
        with self.assertRaises(AssertionError):
            _validate_schema(invalid_preview, schemas["creative_preview"])
        invalid_generated = copy.deepcopy(generated_result)
        invalid_generated["prompt_report"] = None
        with self.assertRaises(AssertionError):
            _validate_schema(invalid_generated, schemas["creative_generate"])


class LauncherLifecycleTests(unittest.TestCase):
    def test_manifest_requires_global_provisioning(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("mcpServers", manifest)
        self.assertFalse((PLUGIN_ROOT / ".mcp.json").exists())

    def test_read_only_copy_has_no_temp_leak_or_tree_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="creative-launcher-") as temporary:
            root = Path(temporary)
            copied_plugin = root / "plugin"
            shutil.copytree(PLUGIN_ROOT, copied_plugin)
            _make_read_only(copied_plugin)
            before_digest = _tree_digest(copied_plugin)
            temp_root = root / "launcher-tmp"
            temp_root.mkdir()
            override = temp_root / "bridge-override"
            override.write_text(
                "#!/bin/sh\nexec "
                + shlex.quote(sys.executable)
                + " -B -u "
                + shlex.quote(str(copied_plugin / "mcp/server.py"))
                + " \"$@\"\n",
                encoding="utf-8",
            )
            override.chmod(0o755)
            environment = os.environ.copy()
            environment.update({
                "TMPDIR": str(temp_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "CREATIVE_MODEL_BRIDGE_BIN": str(override),
                "CREATIVE_MODEL_BRIDGE_OFFLINE": "1",
            })
            command = [str(override)]
            result = subprocess.run(
                command,
                cwd=copied_plugin,
                env=environment,
                input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
                '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2, result.stderr)
            self.assertEqual(json.loads(lines[0])["id"], 1)
            self.assertEqual(json.loads(lines[1])["id"], 2)
            self.assertEqual(_tree_digest(copied_plugin), before_digest)
            self.assertEqual(list(temp_root.glob("creative-model-bridge.*")), [])


if __name__ == "__main__":
    unittest.main()
