from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.harness.resolution import resolve_context
from skill_manager.opencode.agents import discover_config_agents


def context_for(root: Path):
    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "home" / ".config"),
        "XDG_DATA_HOME": str(root / "home" / ".local" / "share"),
        "XDG_STATE_HOME": str(root / "home" / ".local" / "state"),
    }
    return resolve_context(env)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class OpenCodeAgentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"

    def test_reads_agents_from_jsonc_write_target_with_unknown_fields(self):
        config = write(
            self.home / ".config/opencode/opencode.jsonc",
            """{
  // Agents configured outside Skill Manager.
  "agent": {
    "reviewer": {
      "description": "Reviews code",
      "prompt": "{file:./prompts/review.txt}",
      "mode": "subagent",
      "model": "anthropic/claude-sonnet-4",
      "variant": "high",
      "temperature": 0.2,
      "steps": 8,
      "hidden": false,
      "permission": { "edit": "deny" },
      "customFutureOption": { "nested": true }
    }
  }
}
""",
        )
        discovery = discover_config_agents(context_for(self.root))
        self.assertEqual(len(discovery.agents), 1)
        agent = discovery.agents[0]
        self.assertEqual(agent.name, "reviewer")
        self.assertEqual(agent.schema_generation, "v1")
        self.assertEqual(agent.description, "Reviews code")
        self.assertEqual(agent.instructions, "{file:./prompts/review.txt}")
        self.assertEqual(agent.mode, "subagent")
        self.assertEqual(agent.model, "anthropic/claude-sonnet-4")
        self.assertEqual(agent.variant, "high")
        self.assertEqual(agent.temperature, 0.2)
        self.assertEqual(agent.steps, 8)
        self.assertIs(agent.hidden, False)
        self.assertEqual(agent.permissions, {"edit": "deny"})
        self.assertEqual(agent.additional_options,
                         {"variant": "high", "customFutureOption": {"nested": True}})
        self.assertEqual(agent.source.path, config)
        self.assertEqual(agent.source.format, "jsonc")
        self.assertTrue(agent.source.is_write_target)
        self.assertEqual(agent.editability, "config")
        self.assertEqual(agent.read_only_reasons, ())
        self.assertEqual(discovery.write_target, config)

    def test_normalises_v2_agents_section_with_model_variant_and_structured_model(self):
        write(
            self.home / ".config/opencode/opencode.jsonc",
            """{
  "agents": {
    "reviewer": {
      "description": "Reviews changes",
      "system": "Report findings in severity order.",
      "mode": "subagent",
      "model": "anthropic/claude-sonnet-4-5#high",
      "permissions": [{"action": "edit", "resource": "*", "effect": "deny"}],
      "disabled": false,
      "steps": 8,
      "hidden": false,
      "request": {"body": {"temperature": 0.1}}
    },
    "writer": {
      "description": "Writes docs",
      "model": {"providerID": "anthropic", "model": "claude-haiku-4-5", "variant": "low"},
      "unknownFuture": 42
    },
    "unparseable-model": {"model": {"providerID": 7}}
  }
}
""",
        )
        discovery = discover_config_agents(context_for(self.root))
        by_name = {agent.name: agent for agent in discovery.agents}
        self.assertEqual(len(discovery.agents), 3)

        reviewer = by_name["reviewer"]
        self.assertEqual(reviewer.schema_generation, "v2")
        self.assertEqual(reviewer.instructions, "Report findings in severity order.")
        self.assertEqual(reviewer.model, "anthropic/claude-sonnet-4-5")
        self.assertEqual(reviewer.variant, "high")
        self.assertEqual(reviewer.model_raw, "anthropic/claude-sonnet-4-5#high")
        self.assertEqual(reviewer.permissions,
                         [{"action": "edit", "resource": "*", "effect": "deny"}])
        self.assertIs(reviewer.disabled, False)
        self.assertEqual(reviewer.additional_options,
                         {"request": {"body": {"temperature": 0.1}}})

        writer = by_name["writer"]
        self.assertEqual(writer.model, "anthropic/claude-haiku-4-5")
        self.assertEqual(writer.variant, "low")
        self.assertEqual(writer.model_raw,
                         {"providerID": "anthropic", "model": "claude-haiku-4-5", "variant": "low"})
        self.assertEqual(writer.additional_options, {"unknownFuture": 42})

        unparseable = by_name["unparseable-model"]
        self.assertIsNone(unparseable.model)
        self.assertIsNone(unparseable.variant)
        self.assertEqual(unparseable.model_raw, {"providerID": 7})

    def test_v1_reasoning_effort_surfaces_as_variant_while_raw_is_preserved(self):
        write(
            self.home / ".config/opencode/opencode.jsonc",
            '{"agent": {"deep": {"description": "Deep thought",'
            ' "model": "openai/gpt-5", "reasoningEffort": "high"}}}',
        )
        agent = discover_config_agents(context_for(self.root)).agents[0]
        self.assertEqual(agent.schema_generation, "v1")
        self.assertEqual(agent.variant, "high")
        self.assertEqual(agent.additional_options, {"reasoningEffort": "high"})

    def test_both_sections_preserved_with_generation_specific_entries(self):
        write(
            self.home / ".config/opencode/opencode.jsonc",
            """{
  "agent": {"reviewer": {"description": "V1 reviewer", "prompt": "Old prompt."}},
  "agents": {"reviewer": {"description": "V2 reviewer", "system": "New system."}}
}
""",
        )
        discovery = discover_config_agents(context_for(self.root))
        self.assertEqual(len(discovery.agents), 2)
        generations = {agent.schema_generation for agent in discovery.agents}
        self.assertEqual(generations, {"v1", "v2"})
        for agent in discovery.agents:
            # Both generation-specific entries stay editable: the backend
            # requires an explicit schemaGeneration, so nothing is guessed.
            self.assertEqual(agent.editability, "config")
            self.assertIn("defined-in-both-v1-and-v2-sections", agent.read_only_reasons)
        self.assertTrue(any("both" in item for item in discovery.diagnostics))

    def test_precedence_and_per_agent_source_attribution(self):
        legacy = write(
            self.home / ".opencode/opencode.jsonc",
            '{"agent": {"legacy-agent": {"description": "From legacy", "mode": "all"}}}',
        )
        modern = write(
            self.home / ".config/opencode/opencode.jsonc",
            '{"agent": {"modern-agent": {"description": "From modern", "mode": "subagent"}}}',
        )
        discovery = discover_config_agents(context_for(self.root))
        by_name = {agent.name: agent for agent in discovery.agents}
        self.assertEqual(set(by_name), {"legacy-agent", "modern-agent"})
        self.assertEqual(by_name["legacy-agent"].source.path, legacy)
        self.assertFalse(by_name["legacy-agent"].source.is_write_target)
        # Task 12: a supported config-defined definition is editable in its
        # declaring file even when that file is not the selected write target.
        self.assertEqual(by_name["legacy-agent"].editability, "config")
        self.assertEqual(by_name["legacy-agent"].read_only_reasons, ())
        self.assertEqual(by_name["modern-agent"].source.path, modern)
        self.assertTrue(by_name["modern-agent"].source.is_write_target)
        self.assertEqual(by_name["modern-agent"].editability, "config")
        # Established resolver precedence is unchanged: the XDG JSONC source stays
        # authoritative over the XDG JSON source for the same agent name.
        json_source = write(
            self.home / ".config/opencode/opencode.json",
            '{"agent": {"modern-agent": {"description": "From JSON"}}}',
        )
        discovery = discover_config_agents(context_for(self.root))
        agent = next(item for item in discovery.agents if item.name == "modern-agent")
        self.assertEqual(agent.description, "From modern")
        self.assertEqual(agent.source.path, modern)
        self.assertEqual(agent.source.format, "jsonc")
        self.assertTrue(agent.source.is_write_target)
        self.assertEqual(
            next(item for item in discovery.sources if item.path == json_source).status,
            "loaded",
        )

    def test_non_object_and_wrongly_typed_definitions_are_preserved_read_only(self):
        write(
            self.home / ".config/opencode/opencode.jsonc",
            '{"agent": {"broken": "just a string", "odd-types": {"temperature": "warm", "steps": 5}},'
            ' "agents": {"v2-broken": 12}}',
        )
        discovery = discover_config_agents(context_for(self.root))
        by_name = {agent.name: agent for agent in discovery.agents}
        broken = by_name["broken"]
        self.assertFalse(broken.valid)
        self.assertEqual(broken.schema_generation, "v1")
        self.assertIsNone(broken.description)
        self.assertEqual(broken.editability, "read-only")
        self.assertIn("definition-not-an-object", broken.read_only_reasons)
        self.assertFalse(by_name["v2-broken"].valid)
        self.assertEqual(by_name["v2-broken"].schema_generation, "v2")
        odd = by_name["odd-types"]
        self.assertTrue(odd.valid)
        self.assertIsNone(odd.temperature)  # Wrongly typed known value stays in extras.
        self.assertEqual(odd.steps, 5)
        self.assertEqual(odd.additional_options, {"temperature": "warm"})

    def test_missing_agent_section_and_non_object_section_are_safe(self):
        discovery = discover_config_agents(context_for(self.root))
        self.assertEqual(discovery.agents, ())
        write(self.home / ".config/opencode/opencode.jsonc", '{"agent": "not an object"}')
        discovery = discover_config_agents(context_for(self.root))
        self.assertEqual(discovery.agents, ())
        self.assertTrue(any("agent" in item for item in discovery.diagnostics))
        write(self.home / ".config/opencode/opencode.jsonc", '{"agents": 7}')
        discovery = discover_config_agents(context_for(self.root))
        self.assertEqual(discovery.agents, ())
        self.assertTrue(any("agents" in item for item in discovery.diagnostics))

    def test_discovery_never_writes_and_reports_static_limitation(self):
        config = write(
            self.home / ".config/opencode/opencode.jsonc",
            '{"agent": {"reviewer": {"description": "Reviews"}}}',
        )
        before = config.read_bytes()
        discover_config_agents(context_for(self.root))
        self.assertEqual(config.read_bytes(), before)
        self.assertIn("static", discover_config_agents(context_for(self.root)).limitation.lower())


if __name__ == "__main__":
    unittest.main()
