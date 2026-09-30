import json
from pathlib import Path
import unittest

from tests.support.app_harness import AppTestHarness

V1_CONFIG = """{
  // managed outside Skill Manager
  "agent": {
    "reviewer": {"description": "Reviews code", "mode": "subagent", "prompt": "Old."}
  }
}
"""


def seed(spec) -> None:
    config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(V1_CONFIG, encoding="utf-8")


class OpenCodeAgentApplyApiTests(unittest.TestCase):
    def test_capability_reports_manual_restart_for_unprobed_runtime(self):
        with AppTestHarness(fixture_factory=seed) as app:
            capability = app.get_json("/api/agents/opencode/apply-capability")
            self.assertEqual(capability["mechanism"], "restart-manual")
            self.assertFalse(capability["reloadAvailable"])
            self.assertFalse(capability["managedRuntime"])
            self.assertTrue(capability["confirmRequired"])
            self.assertIn("restart", capability["detail"].lower())

    def test_apply_requires_confirmation_and_refuses_automated_manual_restart(self):
        with AppTestHarness(fixture_factory=seed) as app:
            app.post_json("/api/agents/opencode/apply", {"confirm": False},
                          expected_status=422)
            app.post_json("/api/agents/opencode/apply", {"confirm": True},
                          expected_status=409)

    def test_pending_state_flows_through_save_and_manual_acknowledgement(self):
        with AppTestHarness(fixture_factory=seed) as app:
            config = app.spec.xdg_config_home / "opencode" / "opencode.jsonc"
            self.assertFalse(app.get_json("/api/agents/opencode/apply-status")["pending"])

            context = app.get_json("/api/agents/opencode/editor-context")
            app.put_json("/api/agents/opencode/reviewer", {
                "fields": {"description": "New"},
                "expectedSourceHash": context["sourceHash"],
            })
            status = app.get_json("/api/agents/opencode/apply-status")
            self.assertTrue(status["pending"])
            self.assertEqual(status["target"], str(config))
            self.assertNotEqual(status["savedHash"], status["appliedHash"])

            # No-op save leaves durable pending state untouched.
            fresh = app.get_json("/api/agents/opencode/editor-context")
            app.put_json("/api/agents/opencode/reviewer", {
                "fields": {},
                "expectedSourceHash": fresh["sourceHash"],
            })
            self.assertTrue(app.get_json("/api/agents/opencode/apply-status")["pending"])

            app.post_json("/api/agents/opencode/apply/acknowledge-manual",
                          {"confirm": False}, expected_status=422)
            app.post_json("/api/agents/opencode/apply/acknowledge-manual",
                          {"confirm": True})
            status = app.get_json("/api/agents/opencode/apply-status")
            self.assertFalse(status["pending"])
            self.assertEqual(status["savedHash"], status["appliedHash"])
            self.assertIn("New", config.read_text())  # Saved config intact throughout.


if __name__ == "__main__":
    unittest.main()


class OpenCodeAgentApplyAggregateTests(unittest.TestCase):
    def test_legacy_declaring_file_edit_aggregates_into_apply_status(self):
        def seed_legacy(spec):
            config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(V1_CONFIG, encoding="utf-8")
            legacy = spec.home / ".opencode" / "opencode.jsonc"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(
                '{"agent": {"legacy-agent": {"description": "From legacy",'
                ' "mode": "subagent"}}}', encoding="utf-8",
            )

        with AppTestHarness(fixture_factory=seed_legacy) as app:
            legacy = app.spec.home / ".opencode" / "opencode.jsonc"
            self.assertFalse(app.get_json("/api/agents/opencode/apply-status")["pending"])

            context = app.get_json("/api/agents/opencode/editor-context")
            # Edit the agent DECLARED in the legacy (non-write-target) file.
            import hashlib

            legacy_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
            app.put_json("/api/agents/opencode/legacy-agent", {
                "fields": {"description": "Edited in place"},
                "expectedSourceHash": legacy_hash,
            })

            status = app.get_json("/api/agents/opencode/apply-status")
            self.assertTrue(status["pending"])
            self.assertIn(str(legacy), status["pendingTargets"])
            targets = {item["target"]: item for item in status["targets"]}
            self.assertTrue(targets[str(legacy)]["pending"])

            # Manual acknowledgement clears every pending target including legacy.
            capability = app.get_json("/api/agents/opencode/apply-capability")
            self.assertEqual(capability["mechanism"], "restart-manual")
            self.assertFalse(capability["canExecute"])
            app.post_json("/api/agents/opencode/apply", {"confirm": True},
                          expected_status=409)
            result = app.post_json("/api/agents/opencode/apply/acknowledge-manual",
                                   {"confirm": True})
            self.assertIn(str(legacy), result["acknowledgedTargets"])
            self.assertFalse(app.get_json("/api/agents/opencode/apply-status")["pending"])
            self.assertIn("Edited in place", legacy.read_text())

    def test_apply_refused_when_nothing_pending_or_target_stale(self):
        with AppTestHarness(fixture_factory=seed) as app:
            config = app.spec.xdg_config_home / "opencode" / "opencode.jsonc"
            # Nothing pending: refused without executing anything.
            app.post_json("/api/agents/opencode/apply", {"confirm": True},
                          expected_status=409)

            # Make pending, then mutate the file externally: stale refusal.
            context = app.get_json("/api/agents/opencode/editor-context")
            app.put_json("/api/agents/opencode/reviewer", {
                "fields": {"description": "New"},
                "expectedSourceHash": context["sourceHash"],
            })
            self.assertTrue(app.get_json("/api/agents/opencode/apply-status")["pending"])
            config.write_text(V1_CONFIG.replace("Reviews code", "Externally changed"))
            app.post_json("/api/agents/opencode/apply/acknowledge-manual",
                          {"confirm": True}, expected_status=409)
            self.assertTrue(app.get_json("/api/agents/opencode/apply-status")["pending"])


if __name__ == "__main__":
    unittest.main()
