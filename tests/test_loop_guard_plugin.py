from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "loop-guard"
SCRIPT = PLUGIN_ROOT / "scripts" / "loop_guard.py"
SPEC = importlib.util.spec_from_file_location("loop_guard_hook", SCRIPT)
assert SPEC and SPEC.loader
LOOP_GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOOP_GUARD)


def event(name: str, response=None, transcript="/tmp/transcript.jsonl"):
    value = {
        "hook_event_name": name,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "transcript_path": transcript,
        "tool_name": "Bash",
        "tool_input": {"command": "private-command --secret value"},
    }
    if response is not None:
        value["tool_response"] = response
    return value


class LoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def process(self, value, now):
        return LOOP_GUARD.process_event(value, PLUGIN_ROOT, self.data_dir, now=now)

    def test_observe_mode_records_candidate_without_blocking(self):
        failure = {"exit_code": 1, "output": "private failure output"}
        for attempt in range(3):
            self.assertIsNone(self.process(event("PostToolUse", failure), 10 + attempt))
        self.assertIsNone(self.process(event("PreToolUse"), 14))
        entries = [
            json.loads(line)
            for line in (self.data_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(entries[0]["event"], "repeat_failure_candidate")
        self.assertEqual(entries[1]["event"], "repeat_block_candidate")

    def test_enforce_mode_denies_fourth_identical_failure(self):
        (self.data_dir / "config.json").write_text(
            json.dumps({"mode": "enforce"}), encoding="utf-8"
        )
        failure = {"exit_code": 2, "output": "failure"}
        third = None
        for attempt in range(3):
            third = self.process(event("PostToolUse", failure), 20 + attempt)
        self.assertIn("additionalContext", third["hookSpecificOutput"])
        blocked = self.process(event("PreToolUse"), 24)
        self.assertEqual(
            blocked["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_success_and_user_prompt_reset_state(self):
        failure = {"isError": True, "error": "failure"}
        self.process(event("PostToolUse", failure), 30)
        self.process(event("PostToolUse", {"exit_code": 0}), 31)
        self.process(event("PostToolUse", failure), 32)
        self.process(event("UserPromptSubmit"), 33)
        self.process(event("PostToolUse", failure), 34)
        self.assertFalse((self.data_dir / "events.jsonl").exists())

    def test_ambiguous_scope_never_enforces(self):
        (self.data_dir / "config.json").write_text(
            json.dumps({"mode": "enforce"}), encoding="utf-8"
        )
        failure = {"exit_code": 1}
        for attempt in range(3):
            self.process(event("PostToolUse", failure, transcript=None), 40 + attempt)
        self.assertIsNone(self.process(event("PreToolUse", transcript=None), 44))

    def test_raw_payloads_are_not_persisted(self):
        failure = {"exit_code": 1, "error": "private-error-value"}
        for attempt in range(3):
            self.process(event("PostToolUse", failure), 50 + attempt)
        persisted = b"".join(
            path.read_bytes()
            for path in self.data_dir.iterdir()
            if path.is_file()
        )
        self.assertNotIn(b"private-command", persisted)
        self.assertNotIn(b"private-error-value", persisted)


if __name__ == "__main__":
    unittest.main()
