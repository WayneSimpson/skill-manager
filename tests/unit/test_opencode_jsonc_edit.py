from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.opencode.jsonc_edit import insert_agent, patch_agent

V1_CONFIG = """{
  // Global config managed elsewhere
  "model": "anthropic/claude-sonnet-4",
  "provider": { /* providers stay */ },
  "agent": {
    // Keep this reviewer
    "reviewer": {
      "description": "Reviews code",
      "mode": "subagent",
      "prompt": "Old prompt.",
      "custom": true
    },
    "other": {"description": "Untouched"}
  },
  "mcp": {"kept": true}
}
"""

V2_CONFIG = """{
  "agents": {
    "reviewer": {
      "system": "Old system.",
      "model": "anthropic/claude-sonnet-4-5#high",
      "request": {"body": {"temperature": 0.1}}
    }
  },
  // trailing comment
}
"""


class JsoncEditTests(unittest.TestCase):
    def test_patch_replaces_only_target_agent_and_keeps_comments(self):
        updated = patch_agent("agent", "reviewer", {
            "description": "Reviews code carefully",
            "mode": "subagent",
            "prompt": "New prompt.",
            "custom": True,
        })(V1_CONFIG)
        self.assertIn('"prompt": "New prompt."', updated)
        self.assertIn('"description": "Reviews code carefully"', updated)
        self.assertIn("// Global config managed elsewhere", updated)
        self.assertIn("// Keep this reviewer", updated)
        self.assertIn('"other": {"description": "Untouched"}', updated)
        self.assertIn('"mcp": {"kept": true}', updated)
        self.assertIn("/* providers stay */", updated)
        self.assertNotIn("Old prompt.", updated)
        # Unrelated agent and trailing structure remain byte-identical after it.
        tail = updated[updated.index('"other"'):]
        self.assertEqual(tail, V1_CONFIG[V1_CONFIG.index('"other"'):])

    def test_patch_supports_rename(self):
        updated = patch_agent("agent", "reviewer", {"description": "x"},
                              rename_to="senior-reviewer")(V1_CONFIG)
        self.assertIn('"senior-reviewer"', updated)
        self.assertNotIn('"reviewer"', updated)
        self.assertIn('"other": {"description": "Untouched"}', updated)

    def test_patch_v2_agent_preserves_siblings_and_trailing_comment(self):
        updated = patch_agent("agents", "reviewer", {
            "system": "New system.",
            "model": "anthropic/claude-sonnet-4-5#low",
            "request": {"body": {"temperature": 0.1}},
        })(V2_CONFIG)
        self.assertIn('"system": "New system."', updated)
        self.assertIn("#low", updated)
        self.assertIn("// trailing comment", updated)

    def test_insert_into_existing_section_preserves_everything_else(self):
        updated = insert_agent("agent", "new-agent", {"description": "Fresh"})(V1_CONFIG)
        self.assertIn('"new-agent"', updated)
        self.assertIn('"reviewer"', updated)
        self.assertIn("// Keep this reviewer", updated)
        self.assertIn('"mcp": {"kept": true}', updated)
        before, after = updated.split('"new-agent"', 1)
        self.assertIn("// Global config managed elsewhere", before)
        # Bytes after the inserted member still match the original tail.
        original_tail = V1_CONFIG[V1_CONFIG.index('"mcp"'):]
        self.assertTrue(updated.endswith(original_tail), updated[-120:])

    def test_insert_creates_missing_section_and_handles_empty_root(self):
        updated = insert_agent("agents", "first", {"description": "First"})(
            '{\n  "model": "x"\n}\n')
        self.assertIn('"agents"', updated)
        self.assertIn('"model": "x"', updated)

        empty = insert_agent("agent", "first", {"description": "First"})("{}")
        self.assertIn('"agent"', empty)
        self.assertIn('"first"', empty)

    def test_insert_into_empty_section_object(self):
        updated = insert_agent("agent", "first", {"description": "First"})(
            '{\n  "agent": {},\n  "model": "x"\n}\n')
        self.assertIn('"first"', updated)
        self.assertIn('"model": "x"', updated)
        self.assertNotIn('"agent": {}', updated)

    def test_plain_json_files_round_trip(self):
        plain = '{"agent":{"reviewer":{"description":"Reviews"}},\n "model":"x"}'
        updated = patch_agent("agent", "reviewer", {"description": "Better"})(plain)
        self.assertIn('"description": "Better"', updated)
        self.assertIn('"model":"x"', updated)

    def test_missing_targets_raise(self):
        from skill_manager.opencode.jsonc_edit import JsoncEditError
        with self.assertRaises(JsoncEditError):
            patch_agent("agent", "missing", {})(V1_CONFIG)
        with self.assertRaises(JsoncEditError):
            patch_agent("agents", "reviewer", {})(V1_CONFIG)
        with self.assertRaises(JsoncEditError):
            insert_agent("agent", "x", {})("not json")


if __name__ == "__main__":
    unittest.main()
