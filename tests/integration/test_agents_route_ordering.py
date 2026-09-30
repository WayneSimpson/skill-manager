"""Regression: static agent-management endpoints must not be swallowed by /{name}."""
import unittest

from tests.support.app_harness import AppTestHarness

V1_CONFIG = """{
  "agent": {
    "reviewer": {"description": "Reviews code", "mode": "subagent", "prompt": "Old."},
    "model-catalogue": {"description": "Externally authored agent with a reserved name"},
    "apply": {"description": "Agent named after a POST-only route", "mode": "subagent", "prompt": "Fine."}
  }
}
"""


def seed(spec) -> None:
    config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(V1_CONFIG, encoding="utf-8")


class AgentRouteOrderingTests(unittest.TestCase):
    """Every static path must resolve to its handler, not the agent-detail."""

    def setUp(self):
        import tempfile
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._harness = AppTestHarness(fixture_factory=seed)
        self.addCleanup(self._harness.__exit__, None, None, None)

    def test_model_catalogue_returns_catalogue_dto_not_agent_detail(self):
        result = self._harness.get_json("/api/agents/opencode/model-catalogue")
        self.assertIn("source", result)
        self.assertIn("providers", result)
        self.assertIn("totalModels", result)
        # Proves it's NOT the agent-detail handler (which would return name/readOnly).
        self.assertNotIn("readOnly", result)
        self.assertNotIn("schemaGeneration", result)

    def test_variant_options_returns_options_not_agent_detail(self):
        result = self._harness.get_json(
            "/api/agents/opencode/variant-options?model=anthropic%2Fclaude-sonnet-4-5"
        )
        self.assertIn("options", result)
        self.assertIsInstance(result["options"], list)

    def test_permission_actions_returns_action_lists_not_agent_detail(self):
        result = self._harness.get_json("/api/agents/opencode/permission-actions")
        self.assertIn("v1", result)
        self.assertIn("v2", result)
        self.assertIn("effects", result)
        self.assertIn("bash", result["v1"])
        self.assertIn("shell", result["v2"])

    def test_editor_context_returns_context_not_agent_detail(self):
        result = self._harness.get_json("/api/agents/opencode/editor-context")
        self.assertIn("writeTarget", result)
        self.assertIn("create", result)

    def test_apply_capability_returns_capability_not_agent_detail(self):
        result = self._harness.get_json("/api/agents/opencode/apply-capability")
        self.assertIn("mechanism", result)
        self.assertIn("canExecute", result)

    def test_apply_status_returns_status_not_agent_detail(self):
        result = self._harness.get_json("/api/agents/opencode/apply-status")
        self.assertIn("pending", result)
        self.assertIn("target", result)

    def test_ordinary_agent_name_still_resolves(self):
        result = self._harness.get_json("/api/agents/opencode/reviewer")
        self.assertEqual(result["name"], "reviewer")
        self.assertIn("readOnly", result)

    def test_reserved_name_agent_remains_discoverable_but_read_only(self):
        # The externally-authored agent named "model-catalogue" IS in the listing
        # (discoverable, not hidden), but the static route wins the GET path.
        # Its read-only reason truthfully explains the conflict.
        listing = self._harness.get_json("/api/agents/opencode")
        by_name = {a["name"]: a for a in listing["agents"]}
        self.assertIn("model-catalogue", by_name)
        self.assertIn("reviewer", by_name)

        reserved_agent = by_name["model-catalogue"]
        self.assertTrue(reserved_agent["readOnly"])
        self.assertIn("reserved-api-route-name", reserved_agent["readOnlyReasons"])

        # The static route wins — GET returns the catalogue, not the agent.
        catalogue = self._harness.get_json("/api/agents/opencode/model-catalogue")
        self.assertIn("providers", catalogue)
        self.assertNotIn("readOnly", catalogue)

    def test_create_with_reserved_name_is_rejected(self):
        self._harness.post_json("/api/agents/opencode", {
            "schemaGeneration": "v1",
            "fields": {"name": "apply-status", "description": "Blocked"},
            "expectedSourceHash": "any",
        }, expected_status=422)

    def test_rename_to_reserved_name_is_rejected(self):
        from skill_manager.opencode.agents import RESERVED_AGENT_ROUTE_NAMES
        self.assertIn("model-catalogue", RESERVED_AGENT_ROUTE_NAMES)

        context = self._harness.get_json("/api/agents/opencode/editor-context")
        self._harness.put_json("/api/agents/opencode/reviewer", {
            "fields": {"renameTo": "permission-actions"},
            "expectedSourceHash": context["sourceHash"],
        }, expected_status=422)

    def test_apply_post_only_route_name_is_not_reserved(self):
        # POST-only /opencode/apply does not shadow GET/PUT /opencode/{name}
        # because route matching is method-aware.
        detail = self._harness.get_json("/api/agents/opencode/apply")
        self.assertEqual(detail["name"], "apply")
        self.assertFalse(detail["readOnly"])

        # And it is an ordinary editable target (rename allowed since not reserved).
        from skill_manager.opencode.agents import RESERVED_AGENT_ROUTE_NAMES
        self.assertNotIn("apply", RESERVED_AGENT_ROUTE_NAMES)

    def test_unknown_agent_returns_404_not_static_response(self):
        self._harness.get_json("/api/agents/opencode/nonexistent-agent", expected_status=404)


if __name__ == "__main__":
    unittest.main()
