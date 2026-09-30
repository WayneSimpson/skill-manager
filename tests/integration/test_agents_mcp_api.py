"""Integration tests for the MCP permissions API flow (fixture config only)."""
import json
import unittest

from tests.support.app_harness import AppTestHarness

CONFIG = {
    "mcp": {
        "clickup": {"type": "http", "url": "https://clickup.test"},
        "n8n_nccio": {"type": "http", "url": "https://n8n.test"},
        "playwright": {"type": "http", "url": "https://pw.test"},
    },
    "agent": {
        "helper": {
            "description": "Existing agent with MCP wildcards",
            "mode": "subagent",
            "prompt": "Help.",
            "permission": {
                "n8n_nccio_*": "allow",
                "playwright*": "ask",
                "orphan_tool_*": "deny",
            },
        },
    },
}


def seed(spec) -> None:
    config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(CONFIG), encoding="utf-8")


class McpServersApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._harness = AppTestHarness(fixture_factory=seed)
        self.addCleanup(self._harness.__exit__, None, None, None)

    def test_config_fallback_lists_configured_servers_with_wildcards(self):
        # No runtime server configured in the harness → config-derived fallback.
        result = self._harness.get_json("/api/agents/opencode/mcp-servers")
        self.assertEqual(result["source"], "config")
        by_name = {s["name"]: s for s in result["servers"]}
        self.assertEqual(set(by_name), {"clickup", "n8n_nccio", "playwright"})
        self.assertEqual(by_name["n8n_nccio"]["wildcard"], "n8n_nccio_*")
        self.assertEqual(by_name["playwright"]["status"], "unknown")

    def test_agent_with_mcp_wildcard_rules_persists_v1_permission_object(self):
        # Create a NEW agent whose only MCP override is an explicit Allow for
        # n8n_nccio; V1 must persist the permission-object syntax.
        context = self._harness.get_json("/api/agents/opencode/editor-context")
        created = self._harness.post_json("/api/agents/opencode", {
            "schemaGeneration": "v1",
            "fields": {
                "name": "fresh-agent",
                "description": "New",
                "permissionRules": [
                    {"action": "n8n_nccio_*", "effect": "allow", "resource": None},
                ],
            },
            "expectedSourceHash": context["sourceHash"],
        })
        agent = created["agent"]
        self.assertEqual(agent["name"], "fresh-agent")
        permission = agent["permissions"]
        self.assertEqual(permission, {"n8n_nccio_*": "allow"})

    def test_update_existing_wildcard_rule_changes_exact_rule_only(self):
        # Existing agent: n8n_nccio_*: allow → deny. The unrelated orphan and
        # legacy playwright* rules must remain byte-identical.
        context = self._harness.get_json("/api/agents/opencode/editor-context")
        updated = self._harness.put_json("/api/agents/opencode/helper", {
            "schemaGeneration": "v1",
            "fields": {
                "permissionRules": [
                    {"action": "n8n_nccio_*", "effect": "deny", "resource": None},
                    {"action": "playwright*", "effect": "ask", "resource": None},
                    {"action": "orphan_tool_*", "effect": "deny", "resource": None},
                ],
            },
            "expectedSourceHash": context["sourceHash"],
        })
        permission = updated["agent"]["permissions"]
        self.assertEqual(permission["n8n_nccio_*"], "deny")
        self.assertEqual(permission["playwright*"], "ask")  # Untouched legacy form.
        self.assertEqual(permission["orphan_tool_*"], "deny")  # Untouched orphan.

    def test_inherit_removes_only_the_exact_override(self):
        context = self._harness.get_json("/api/agents/opencode/editor-context")
        updated = self._harness.put_json("/api/agents/opencode/helper", {
            "schemaGeneration": "v1",
            "fields": {
                "permissionRules": [
                    # Explicit inherit marker removes ONLY the n8n_nccio_*
                    # override; other rules are preserved untouched.
                    {"action": "n8n_nccio_*", "effect": "inherit", "resource": None},
                    {"action": "playwright*", "effect": "ask", "resource": None},
                    {"action": "orphan_tool_*", "effect": "deny", "resource": None},
                ],
            },
            "expectedSourceHash": context["sourceHash"],
        })
        permission = updated["agent"]["permissions"]
        self.assertNotIn("n8n_nccio_*", permission)
        self.assertEqual(permission["playwright*"], "ask")
        self.assertEqual(permission["orphan_tool_*"], "deny")

    def test_v2_agent_persists_ordered_rules_with_extras_preserved(self):
        CONFIG_V2 = dict(CONFIG)
        CONFIG_V2["agents"] = {
            "v2helper": {
                "description": "V2 agent",
                "system": "Help.",
                "permissions": [
                    {"action": "n8n_nccio_*", "effect": "allow", "resource": "*", "priority": 5},
                    {"action": "read", "effect": "deny"},
                ],
            },
        }
        import tempfile
        from tests.support.app_harness import AppTestHarness as Harness

        def seed_v2(spec) -> None:
            config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(CONFIG_V2), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            with Harness(fixture_factory=seed_v2) as harness:
                detail = harness.get_json("/api/agents/opencode/v2helper")
                self.assertEqual(detail["schemaGeneration"], "v2")
                permissions = detail["permissions"]
                self.assertEqual(permissions[0]["action"], "n8n_nccio_*")
                self.assertEqual(permissions[0]["resource"], "*")  # V2 server-wide form.
                self.assertEqual(permissions[0]["priority"], 5)  # Unknown field kept.
                self.assertEqual(permissions[1]["action"], "read")

    def test_v2_create_persists_compliant_resource_star_mcp_rule(self):
        import tempfile
        from tests.support.app_harness import AppTestHarness as Harness

        def seed_empty(spec) -> None:
            config = spec.xdg_config_home / "opencode" / "opencode.jsonc"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"mcp": {"servers": {
                "context7": {"type": "http"},
            }}}), encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            with Harness(fixture_factory=seed_empty) as harness:
                context = harness.get_json("/api/agents/opencode/editor-context")
                created = harness.post_json("/api/agents/opencode", {
                    "schemaGeneration": "v2",
                    "fields": {
                        "name": "v2-fresh",
                        "description": "V2 new",
                        "permissionRules": [
                            {"action": "context7_*", "effect": "deny",
                             "resource": "*", "order": 0},
                        ],
                    },
                    "expectedSourceHash": context["sourceHash"],
                })
                permissions = created["agent"]["permissions"]
                self.assertEqual(permissions, [
                    {"action": "context7_*", "effect": "deny", "resource": "*"},
                ])


if __name__ == "__main__":
    unittest.main()
