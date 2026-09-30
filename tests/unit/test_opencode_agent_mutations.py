import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from skill_manager.application.agents import (
    OpenCodeAgentMutationService,
    OpenCodeAgentQueryService,
)
from skill_manager.errors import MutationError
from skill_manager.jsonc import strip_jsonc
from skill_manager.harness import HarnessKernelService

V1_BODY = """{
  // top comment
  "model": "anthropic/claude-sonnet-4",
  "agent": {
    "reviewer": {
      "description": "Reviews code",
      "mode": "subagent",
      "prompt": "Old prompt.",
      "model": "anthropic/claude-sonnet-4",
      "variant": "high",
      "custom": {"keep": true}
    },
    "other": {"description": "Untouched"}
  },
  "mcp": {"kept": true}
}
"""

V2_BODY = """{
  "agents": {
    "reviewer": {
      "description": "Reviews",
      "system": "Old system.",
      "mode": "subagent",
      "model": "anthropic/claude-sonnet-4-5#high",
      "request": {"body": {"temperature": 0.1}}
    }
  }
}
"""


def env_for(root: Path) -> dict[str, str]:
    home = root / "home"
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


class OpenCodeAgentMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = env_for(self.root)
        from skill_manager.harness import HarnessSupportStore
        support = HarnessSupportStore(self.root / "state" / "settings.json")
        self.kernel = HarnessKernelService.from_environment(env, support_store=support)
        self.config = self.root / "home/.config/opencode/opencode.jsonc"
        self.config.parent.mkdir(parents=True)
        self.backup_root = self.root / "state/opencode-agent-backups"
        self.service = OpenCodeAgentMutationService(self.kernel, self.backup_root)
        self.queries = OpenCodeAgentQueryService(self.kernel)

    def write_config(self, body: str) -> str:
        self.config.write_text(body, encoding="utf-8")
        return body

    def source_hash(self) -> str:
        return self.service.source_hash(self.config)

    # 1. Create V1 sub-agent with explicit subagent mode.
    def test_create_v1_subagent_forces_subagent_mode(self):
        self.write_config(V1_BODY)
        result = self.service.create_agent(
            "v1",
            {"name": "summarizer", "description": "Summarises", "instructions": "Summarise.",
             "model": "anthropic/claude-haiku-4", "variant": "low"},
            expected_hash=self.source_hash(),
        )
        self.assertTrue(result["pendingApply"])
        text = self.config.read_text()
        parsed = json.loads(strip_jsonc(text))
        agent = parsed["agent"]["summarizer"]
        self.assertEqual(agent["mode"], "subagent")
        self.assertEqual(agent["prompt"], "Summarise.")
        self.assertEqual(agent["variant"], "low")
        self.assertIn("// top comment", text)
        self.assertIn('"other"', text)
        self.assertIn('"mcp": {"kept": true}', text)

    # 2. Create V2 sub-agent.
    def test_create_v2_subagent_uses_v2_keys(self):
        self.write_config(V2_BODY)
        self.service.create_agent(
            "v2",
            {"name": "writer", "description": "Writes", "instructions": "Write.",
             "model": "anthropic/claude-sonnet-4-5", "variant": "high"},
            expected_hash=self.source_hash(),
        )
        parsed = json.loads(strip_jsonc(self.config.read_text()))
        agent = parsed["agents"]["writer"]
        self.assertEqual(agent["mode"], "subagent")
        self.assertEqual(agent["system"], "Write.")
        self.assertEqual(agent["model"], "anthropic/claude-sonnet-4-5#high")

    def test_create_rejects_non_subagent_mode(self):
        self.write_config(V1_BODY)
        with self.assertRaises(MutationError):
            self.service.create_agent(
                "v1", {"name": "x", "description": "d", "mode": "primary"},
                expected_hash=self.source_hash(),
            )

    def test_create_blocks_name_collisions_across_generations(self):
        self.write_config(V1_BODY)
        for generation in ("v1", "v2"):
            with self.assertRaises(MutationError):
                self.service.create_agent(
                    generation, {"name": "reviewer", "description": "d"},
                    expected_hash=self.source_hash(),
                )

    # 3. Edit V1 fields preserving unknowns/comments/unrelated config.
    def test_edit_v1_preserves_unknowns_comments_and_unrelated_config(self):
        self.write_config(V1_BODY)
        before_other = V1_BODY[V1_BODY.index('"other"'):]
        self.service.update_agent(
            "reviewer", "v1",
            {"description": "Reviews carefully", "instructions": "New prompt.",
             "model": "anthropic/claude-opus-4", "variant": None},
            expected_hash=self.source_hash(),
        )
        text = self.config.read_text()
        parsed = json.loads(strip_jsonc(text))
        agent = parsed["agent"]["reviewer"]
        self.assertEqual(agent["description"], "Reviews carefully")
        self.assertEqual(agent["prompt"], "New prompt.")
        self.assertEqual(agent["model"], "anthropic/claude-opus-4")
        self.assertNotIn("variant", agent)
        self.assertEqual(agent["custom"], {"keep": True})
        self.assertIn("// top comment", text)
        self.assertTrue(text.endswith(before_other), text[-100:])

    # 4. Edit V2 preserving model raw fidelity and unknowns.
    def test_edit_v2_preserves_request_and_rebuilds_model_string(self):
        self.write_config(V2_BODY)
        self.service.update_agent(
            "reviewer", "v2",
            {"variant": "low", "mode": "all"},
            expected_hash=self.source_hash(),
        )
        agent = json.loads(self.config.read_text())["agents"]["reviewer"]
        self.assertEqual(agent["model"], "anthropic/claude-sonnet-4-5#low")
        self.assertEqual(agent["mode"], "all")
        self.assertEqual(agent["request"], {"body": {"temperature": 0.1}})
        self.assertEqual(agent["system"], "Old system.")

    def test_edit_v2_structured_model_object_is_patched_in_place(self):
        self.write_config(json.dumps({"agents": {"writer": {
            "model": {"providerID": "anthropic", "model": "claude-haiku-4-5",
                      "variant": "low"}}}}))
        self.service.update_agent(
            "writer", "v2", {"model": "anthropic/claude-haiku-4-5", "variant": "high"},
            expected_hash=self.source_hash(),
        )
        agent = json.loads(self.config.read_text())["agents"]["writer"]
        self.assertEqual(agent["model"], {"providerID": "anthropic",
                                          "model": "claude-haiku-4-5", "variant": "high"})

    def test_clearing_structured_v2_model_removes_whole_field(self):
        self.write_config(json.dumps({"agents": {"writer": {
            "model": {"providerID": "anthropic", "model": "claude-haiku-4-5",
                      "variant": "low"}}}}))
        self.service.update_agent(
            "writer", "v2", {"model": None, "variant": None},
            expected_hash=self.source_hash(),
        )
        agent = json.loads(self.config.read_text())["agents"]["writer"]
        self.assertNotIn("model", agent)  # No orphaned structured fragment survives.

    def test_clearing_only_structured_v2_variant_preserves_model_identity(self):
        self.write_config(json.dumps({"agents": {"writer": {
            "model": {"providerID": "anthropic", "model": "claude-haiku-4-5",
                      "variant": "low", "customOption": True}}}}))
        self.service.update_agent(
            "writer", "v2", {"variant": None},
            expected_hash=self.source_hash(),
        )
        agent = json.loads(self.config.read_text())["agents"]["writer"]
        self.assertEqual(agent["model"], {"providerID": "anthropic",
                                          "model": "claude-haiku-4-5",
                                          "customOption": True})

    # 5. Rename + collision blocking.
    def test_rename_blocks_collisions(self):
        self.write_config(V1_BODY)
        with self.assertRaises(MutationError):
            self.service.update_agent(
                "reviewer", "v1", {"renameTo": "other"}, expected_hash=self.source_hash(),
            )
        result = self.service.update_agent(
            "reviewer", "v1", {"renameTo": "senior-reviewer"},
            expected_hash=self.source_hash(),
        )
        self.assertEqual(result["agent"]["name"], "senior-reviewer")
        parsed = json.loads(strip_jsonc(self.config.read_text()))
        self.assertNotIn("reviewer", parsed["agent"])
        self.assertEqual(parsed["agent"]["senior-reviewer"]["description"], "Reviews code")

    def test_rename_blocks_cross_generation_collision(self):
        self.write_config('{"agent": {"keep": {"description": "v1"}},'
                          ' "agents": {"reviewer": {"description": "v2"}}}')
        with self.assertRaises(MutationError):
            self.service.update_agent(
                "reviewer", "v2", {"renameTo": "keep"}, expected_hash=self.source_hash(),
            )

    # 6. Same-name V1/V2 ambiguity requires explicit generation.
    def test_ambiguous_agent_requires_generation_for_edit(self):
        self.write_config('{"agent": {"shared": {"description": "v1"}},'
                          ' "agents": {"shared": {"description": "v2"}}}')
        with self.assertRaises(MutationError) as caught:
            self.service.update_agent("shared", None, {"description": "x"},
                                      expected_hash=self.source_hash())
        self.assertEqual(caught.exception.status, 409)
        result = self.service.update_agent("shared", "v2", {"description": "edited"},
                                           expected_hash=self.source_hash())
        self.assertEqual(result["agent"]["schemaGeneration"], "v2")
        self.assertEqual(json.loads(self.config.read_text())["agents"]["shared"]["description"],
                         "edited")
        self.assertEqual(json.loads(self.config.read_text())["agent"]["shared"]["description"], "v1")

    def test_invalid_definition_is_not_editable(self):
        self.write_config('{"agent": {"broken": "string"}}')
        with self.assertRaises(MutationError):
            self.service.update_agent("broken", "v1", {"description": "x"},
                                      expected_hash=self.source_hash())

    # 7. Stale detection.
    def test_stale_source_hash_blocks_write(self):
        self.write_config(V1_BODY)
        stale = self.source_hash()
        self.write_config(V1_BODY.replace("Reviews code", "Changed concurrently"))
        with self.assertRaises(MutationError) as caught:
            self.service.update_agent("reviewer", "v1", {"description": "x"},
                                      expected_hash=stale)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("Changed concurrently", self.config.read_text())

    # 8. Backup outside config dir with restrictive permissions.
    def test_backup_is_outside_config_directory_with_restricted_permissions(self):
        self.write_config(V1_BODY)
        result = self.service.update_agent("reviewer", "v1", {"description": "New"},
                                           expected_hash=self.source_hash())
        backups = list(self.backup_root.iterdir())
        self.assertEqual(len(backups), 1)
        self.assertFalse(self.config.parent in backups[0].parents)
        self.assertIn("reviewer", result["backup"]["file"])
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertEqual(backups[0].read_text(), V1_BODY)
        self.assertEqual(stat.S_IMODE(self.backup_root.stat().st_mode), 0o700)

    # 9. Candidate validation failure leaves original untouched.
    def test_candidate_validation_failure_leaves_original(self):
        body = self.write_config(V1_BODY)
        from unittest.mock import patch
        with patch.object(self.service, "_validate_candidate",
                          side_effect=MutationError("validation failed", 422)):
            with self.assertRaises(MutationError):
                self.service.update_agent("reviewer", "v1", {"description": "x"},
                                          expected_hash=self.source_hash())
        self.assertEqual(self.config.read_text(), body)
        self.assertEqual(list(self.backup_root.iterdir()) if self.backup_root.exists() else [], [])

    # 10/11. Read-back verification failure triggers rollback.
    def test_readback_failure_restores_original(self):
        self.write_config(V1_BODY)
        from unittest.mock import patch
        with patch.object(self.service, "_verify_readback", return_value=False):
            with self.assertRaises(MutationError) as caught:
                self.service.update_agent("reviewer", "v1", {"description": "x"},
                                          expected_hash=self.source_hash())
        self.assertIn("rolled back", str(caught.exception).lower())
        self.assertEqual(self.config.read_text(), V1_BODY)

    def test_rollback_failure_reports_backup_path(self):
        self.write_config(V1_BODY)
        from unittest.mock import patch

        calls = {"count": 0}

        def failing_second_write(path: Path, content: str) -> None:
            calls["count"] += 1
            if calls["count"] >= 2:  # First call saves, restore call fails.
                raise OSError("disk full")

        with patch.object(self.service, "_verify_readback", return_value=False), \
                patch.object(self.service, "_atomic_write", side_effect=failing_second_write):
            with self.assertRaises(MutationError) as caught:
                self.service.update_agent("reviewer", "v1", {"description": "x"},
                                          expected_hash=self.source_hash())
        self.assertIn("backup", str(caught.exception).lower())

    # 12. No-op save.
    def test_noop_save_reports_no_change(self):
        self.write_config(V1_BODY)
        result = self.service.update_agent("reviewer", "v1", {},
                                           expected_hash=self.source_hash())
        self.assertFalse(result["changed"])
        self.assertEqual(self.config.read_text(), V1_BODY)
        self.assertEqual(list(self.backup_root.iterdir()) if self.backup_root.exists() else [], [])

    # Preview diff.
    def test_preview_update_returns_diff_without_writing(self):
        self.write_config(V1_BODY)
        preview = self.service.preview_update("reviewer", "v1",
                                              {"description": "Brand new"})
        self.assertEqual(preview["mode"], "update")
        self.assertEqual(preview["old"]["description"], "Reviews code")
        self.assertEqual(preview["new"]["description"], "Brand new")
        self.assertTrue(any("Brand new" in line for line in preview["textDiff"]))
        self.assertEqual(self.config.read_text(), V1_BODY)
        self.assertFalse(self.backup_root.exists())

    # Editor context generation decision.
    def test_editor_context_generation_decision(self):
        self.write_config(V1_BODY)
        context = self.service.editor_context()
        self.assertEqual(context["create"]["targetGeneration"], "v1")
        self.assertFalse(context["create"]["requiresGenerationChoice"])

        self.write_config('{"agent": {"a": {}}, "agents": {"b": {}}}')
        context = self.service.editor_context()
        self.assertTrue(context["create"]["requiresGenerationChoice"])

        self.write_config('{"model": "x"}')
        context = self.service.editor_context()
        self.assertTrue(context["create"]["requiresGenerationChoice"])

    # Variant options from authoritative catalog.
    def test_variant_options_come_from_model_catalog_and_missing_means_free_text(self):
        cache = self.root / "home/.cache/opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(json.dumps({
            "anthropic": {"models": {"claude-sonnet-4-5": {
                "id": "claude-sonnet-4-5",
                "reasoning_options": [{"type": "effort", "values": ["low", "high"]}],
            }}}}))
        self.assertEqual(
            self.service.variant_options("anthropic/claude-sonnet-4-5#high"), ["low", "high"])
        self.assertEqual(self.service.variant_options("anthropic/unknown-model"), [])
        self.assertEqual(self.service.variant_options("not-a-model"), [])

    def test_variant_options_missing_catalog_is_safe(self):
        self.assertEqual(self.service.variant_options("anthropic/claude-sonnet-4-5"), [])

    # 15. Editing writes only the declaring file.
    def test_edit_targets_declaring_file_not_write_target(self):
        legacy = self.root / "home/.opencode/opencode.jsonc"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"agent": {"legacy-agent": {"description": "From legacy"}}}',
                          encoding="utf-8")
        self.write_config(V1_BODY)
        result = self.service.update_agent("legacy-agent", "v1",
                                           {"description": "Edited in place"},
                                           expected_hash=self.service.source_hash(legacy))
        self.assertEqual(result["agent"]["description"], "Edited in place")
        self.assertIn('"legacy-agent"', legacy.read_text())
        self.assertIn("Edited in place", legacy.read_text())
        self.assertIn("Reviews code", self.config.read_text())  # write target untouched


if __name__ == "__main__":
    unittest.main()
