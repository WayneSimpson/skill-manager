import json
from pathlib import Path
import unittest

from tests.support.app_harness import AppTestHarness


def seed_agents(spec) -> None:
    config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "{\n"
        "  // managed outside Skill Manager\n"
        '  "agent": {\n'
        '    "reviewer": {"description": "V1 reviewer", "mode": "subagent",'
        ' "model": "anthropic/claude-sonnet-4", "variant": "high"},\n'
        '    "broken": "not an object"\n'
        "  },\n"
        '  "agents": {\n'
        '    "v2-writer": {"description": "V2 writer", "system": "Write docs.",'
        ' "model": "anthropic/claude-haiku-4-5#low",'
        ' "permissions": [{"action": "edit", "resource": "*", "effect": "deny"}],'
        ' "disabled": true},\n'
        '    "reviewer": {"description": "V2 reviewer", "system": "New system."}\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )


class OpenCodeAgentsApiTests(unittest.TestCase):
    def test_normalises_and_reads_agents_without_touching_config(self):
        with AppTestHarness(fixture_factory=seed_agents) as app:
            config = app.spec.xdg_config_home / "opencode" / "opencode.jsonc"
            before = config.read_bytes()

            listing = app.get_json("/api/agents/opencode")
            self.assertEqual(listing["writeTarget"], str(config))
            self.assertEqual([source["status"] for source in listing["sources"] if source["name"] == "xdg-jsonc"],
                             ["loaded"])
            by_generation = {}
            for agent in listing["agents"]:
                by_generation.setdefault(agent["name"], []).append(agent)
            self.assertEqual(set(by_generation), {"reviewer", "broken", "v2-writer"})

            v1_reviewer = next(a for a in by_generation["reviewer"] if a["schemaGeneration"] == "v1")
            self.assertFalse(v1_reviewer["readOnly"])  # editable with explicit generation
            self.assertEqual(v1_reviewer["editability"], "config")
            self.assertIn("defined-in-both-v1-and-v2-sections", v1_reviewer["readOnlyReasons"])
            self.assertEqual(v1_reviewer["instructions"], None)  # V1 fixture has no prompt here
            self.assertEqual(v1_reviewer["variant"], "high")
            self.assertEqual(v1_reviewer["additionalOptions"], {"variant": "high"})

            v2_reviewer = next(a for a in by_generation["reviewer"] if a["schemaGeneration"] == "v2")
            self.assertFalse(v2_reviewer["readOnly"])
            self.assertEqual(v2_reviewer["instructions"], "New system.")
            self.assertEqual(v2_reviewer["prompt"], "New system.")  # legacy alias
            self.assertIn("defined-in-both-v1-and-v2-sections", v2_reviewer["readOnlyReasons"])

            writer = by_generation["v2-writer"][0]
            self.assertFalse(writer["readOnly"])
            self.assertEqual(writer["schemaGeneration"], "v2")
            self.assertEqual(writer["instructions"], "Write docs.")
            self.assertEqual(writer["model"], "anthropic/claude-haiku-4-5")
            self.assertEqual(writer["variant"], "low")
            self.assertEqual(writer["modelRaw"], "anthropic/claude-haiku-4-5#low")
            self.assertEqual(writer["permissions"],
                             [{"action": "edit", "resource": "*", "effect": "deny"}])
            self.assertIs(writer["disabled"], True)
            self.assertTrue(writer["source"]["isWriteTarget"])

            broken = by_generation["broken"][0]
            self.assertFalse(broken["valid"])
            self.assertEqual(broken["schemaGeneration"], "v1")
            self.assertTrue(broken["readOnly"])
            self.assertEqual(broken["editability"], "read-only")
            self.assertIn("definition-not-an-object", broken["readOnlyReasons"])

            detail = app.get_json("/api/agents/opencode/v2-writer")
            self.assertEqual(detail["description"], "V2 writer")

            self.assertTrue(any("both" in item for item in listing["diagnostics"]))
            self.assertEqual(config.read_bytes(), before)

    def test_unknown_agent_returns_404(self):
        with AppTestHarness(fixture_factory=seed_agents) as app:
            app.get_json("/api/agents/opencode/missing", expected_status=404)

    def test_ambiguous_same_name_agent_is_never_silently_selected(self):
        with AppTestHarness(fixture_factory=seed_agents) as app:
            # "reviewer" exists in both generations: without disambiguation the
            # detail endpoint must refuse to guess rather than pick a winner.
            conflict = app.get_json("/api/agents/opencode/reviewer", expected_status=409)
            self.assertIn("schemaGeneration", str(conflict))
            self.assertIn("v1", str(conflict))
            self.assertIn("v2", str(conflict))

            # Explicit generation selection returns that generation deterministically.
            v1 = app.get_json("/api/agents/opencode/reviewer?schemaGeneration=v1")
            self.assertEqual(v1["schemaGeneration"], "v1")
            self.assertEqual(v1["description"], "V1 reviewer")
            v2 = app.get_json("/api/agents/opencode/reviewer?schemaGeneration=v2")
            self.assertEqual(v2["schemaGeneration"], "v2")
            self.assertEqual(v2["instructions"], "New system.")

            # Invalid or non-matching generation values fail clearly, not silently.
            app.get_json("/api/agents/opencode/reviewer?schemaGeneration=v3", expected_status=400)
            app.get_json("/api/agents/opencode/v2-writer?schemaGeneration=v1", expected_status=404)

            # Unambiguous agents keep working without a generation parameter.
            writer = app.get_json("/api/agents/opencode/v2-writer")
            self.assertEqual(writer["schemaGeneration"], "v2")


if __name__ == "__main__":
    unittest.main()
