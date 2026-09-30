import json
from pathlib import Path
import unittest

from tests.support.app_harness import AppTestHarness

V1_WITH_COMMENTS = """{
  // managed outside Skill Manager
  "model": "anthropic/claude-sonnet-4",
  "agent": {
    "reviewer": {"description": "Reviews code", "mode": "subagent",
                 "prompt": "Old.", "variant": "high", "custom": true},
    "other": {"description": "Untouched"}
  }
}
"""


def seed(spec) -> None:
    config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(V1_WITH_COMMENTS, encoding="utf-8")


class OpenCodeAgentsMutationApiTests(unittest.TestCase):
    def test_editor_context_and_variant_options(self):
        with AppTestHarness(fixture_factory=seed) as app:
            context = app.get_json("/api/agents/opencode/editor-context")
            self.assertEqual(context["create"]["targetGeneration"], "v1")
            self.assertFalse(context["create"]["requiresGenerationChoice"])
            self.assertTrue(context["sourceHash"])
            options = app.get_json(
                "/api/agents/opencode/variant-options?model=anthropic%2Fclaude-sonnet-4-5%23high"
            )
            self.assertIn("options", options)

    def test_preview_then_create_v1_subagent_preserves_config(self):
        with AppTestHarness(fixture_factory=seed) as app:
            config = app.spec.xdg_config_home / "opencode" / "opencode.jsonc"
            context = app.get_json("/api/agents/opencode/editor-context")
            preview = app.post_json("/api/agents/opencode/preview-create", {
                "schemaGeneration": "v1",
                "fields": {"name": "summarizer", "description": "Summarises",
                           "instructions": "Summarise.", "model": "anthropic/claude-haiku-4",
                           "variant": "low"},
            })
            self.assertEqual(preview["mode"], "create")
            self.assertEqual(preview["new"]["prompt"], "Summarise.")
            self.assertEqual(config.read_text(), V1_WITH_COMMENTS)  # preview writes nothing

            saved = app.post_json("/api/agents/opencode", {
                "schemaGeneration": "v1",
                "fields": {"name": "summarizer", "description": "Summarises",
                           "instructions": "Summarise.", "model": "anthropic/claude-haiku-4",
                           "variant": "low"},
                "expectedSourceHash": context["sourceHash"],
            })
            self.assertTrue(saved["pendingApply"])
            self.assertEqual(saved["agent"]["mode"], "subagent")
            text = config.read_text()
            self.assertIn("// managed outside Skill Manager", text)
            self.assertIn('"other"', text)
            self.assertIn('"model": "anthropic/claude-sonnet-4"', text)

    def test_update_with_stale_hash_is_blocked(self):
        with AppTestHarness(fixture_factory=seed) as app:
            config = app.spec.xdg_config_home / "opencode" / "opencode.jsonc"
            context = app.get_json("/api/agents/opencode/editor-context")
            config.write_text(V1_WITH_COMMENTS.replace("Reviews code", "Changed"))
            app.put_json("/api/agents/opencode/reviewer", {
                "fields": {"description": "New"},
                "expectedSourceHash": context["sourceHash"],
            }, expected_status=409)
            self.assertIn("Changed", config.read_text())

    def test_ambiguous_update_requires_generation_choice(self):
        def seed_both(spec):
            config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(
                '{"agent": {"shared": {"description": "v1"}},'
                ' "agents": {"shared": {"description": "v2"}}}')

        with AppTestHarness(fixture_factory=seed_both) as app:
            context = app.get_json("/api/agents/opencode/editor-context")
            self.assertTrue(context["create"]["requiresGenerationChoice"])
            app.put_json("/api/agents/opencode/shared", {
                "fields": {"description": "x"},
                "expectedSourceHash": context["sourceHash"],
            }, expected_status=409)
            # Explicit generation succeeds against only that definition.
            saved = app.put_json("/api/agents/opencode/shared?schemaGeneration=v2", {
                "fields": {"description": "edited"},
                "expectedSourceHash": context["sourceHash"],
            })
            self.assertEqual(saved["agent"]["schemaGeneration"], "v2")


if __name__ == "__main__":
    unittest.main()
