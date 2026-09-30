import unittest

from skill_manager.opencode.agent_permissions import (
    PermissionRule,
    parse_v1_permission,
    parse_v2_permissions,
    serialize_v1_permission,
    serialize_v2_permissions,
    to_generation,
    from_generation,
    known_action_for_generation,
)


class V1PermissionRoundTripTests(unittest.TestCase):
    def test_simple_string_rules_round_trip(self):
        raw = {"edit": "deny", "bash": "ask", "read": "allow"}
        rules = parse_v1_permission(raw)
        self.assertEqual(len(rules), 3)
        self.assertEqual(serialize_v1_permission(rules), raw)

    def test_pattern_scoped_rules_round_trip(self):
        raw = {
            "bash": {"git push": "ask", "grep *": "allow"},
            "edit": "deny",
            "task": {"reviewer": "allow", "other": "deny"},
        }
        rules = parse_v1_permission(raw)
        result = serialize_v1_permission(rules)
        self.assertEqual(result, raw)

    def test_mcp_wildcard_keys_round_trip(self):
        raw = {
            "n8n_nccio_*": "deny",
            "playwright*": "ask",
            "mcp__custom__tool": "allow",
        }
        rules = parse_v1_permission(raw)
        result = serialize_v1_permission(rules)
        self.assertEqual(result, raw)
        self.assertTrue(all(r.custom for r in rules))

    def test_unknown_scalar_value_passthrough(self):
        raw = {"weird_action": 42, "another": [1, 2]}
        rules = parse_v1_permission(raw)
        result = serialize_v1_permission(rules)
        self.assertEqual(result, raw)
        self.assertTrue(all(r.raw_value is not None for r in rules))

    def test_none_and_invalid_input_return_empty(self):
        self.assertEqual(parse_v1_permission(None), [])
        self.assertEqual(parse_v1_permission("not a dict"), [])
        self.assertEqual(serialize_v1_permission([]), None)


class V2PermissionRoundTripTests(unittest.TestCase):
    def test_ordered_rules_round_trip_preserving_order(self):
        raw = [
            {"action": "shell", "resource": "*", "effect": "ask"},
            {"action": "shell", "resource": "git status", "effect": "allow"},
            {"action": "edit", "resource": "*", "effect": "deny"},
            {"action": "subagent", "resource": "reviewer", "effect": "allow"},
        ]
        rules = parse_v2_permissions(raw)
        result = serialize_v2_permissions(rules)
        self.assertEqual(result, raw)
        self.assertEqual([r.order for r in rules], [0, 1, 2, 3])

    def test_last_match_order_preserved(self):
        # Broad rule first, exception after — last matching rule wins in V2.
        raw = [
            {"action": "shell", "resource": "*", "effect": "ask"},
            {"action": "shell", "resource": "git status", "effect": "allow"},
        ]
        rules = parse_v2_permissions(raw)
        self.assertEqual(rules[0].order, 0)
        self.assertEqual(rules[1].order, 1)
        self.assertEqual(serialize_v2_permissions(rules), raw)

    def test_unknown_actions_and_shapes_round_trip(self):
        raw = [
            {"action": "mcp__custom__tool", "resource": "*", "effect": "deny"},
            {"action": "future_action", "resource": "pattern", "effect": "allow",
             "extra": "field"},
        ]
        rules = parse_v2_permissions(raw)
        result = serialize_v2_permissions(rules)
        self.assertEqual(result[0], raw[0])
        self.assertEqual(result[1], raw[1])  # Extra field preserved via raw_value.
        self.assertTrue(any(r.custom for r in rules))

    def test_none_and_invalid_input_return_empty(self):
        self.assertEqual(parse_v2_permissions(None), [])
        self.assertEqual(parse_v2_permissions("not a list"), [])
        self.assertEqual(serialize_v2_permissions([]), None)


class CrossGenerationMappingTests(unittest.TestCase):
    def test_v1_bash_maps_to_v2_shell(self):
        self.assertEqual(known_action_for_generation("bash", "v2"), "shell")
        self.assertEqual(known_action_for_generation("bash", "v1"), "bash")

    def test_v2_shell_maps_to_v1_bash(self):
        self.assertEqual(known_action_for_generation("shell", "v1"), "bash")
        self.assertEqual(known_action_for_generation("shell", "v2"), "shell")

    def test_v1_task_maps_to_v2_subagent(self):
        self.assertEqual(known_action_for_generation("task", "v2"), "subagent")
        self.assertEqual(known_action_for_generation("subagent", "v1"), "task")

    def test_shared_names_do_not_change(self):
        for action in ("read", "edit", "glob", "grep", "skill", "webfetch"):
            self.assertEqual(known_action_for_generation(action, "v1"), action)
            self.assertEqual(known_action_for_generation(action, "v2"), action)

    def test_no_cross_generation_leakage_in_serialisation(self):
        # V1 rules serialise with V1 names, V2 with V2 names.
        v1_raw = {"bash": "ask", "task": "allow"}
        v1_rules = parse_v1_permission(v1_raw)
        v1_result = serialize_v1_permission(v1_rules)
        self.assertIn("bash", v1_result)
        self.assertNotIn("shell", v1_result)

        v2_raw = [
            {"action": "shell", "resource": "*", "effect": "ask"},
            {"action": "subagent", "resource": "*", "effect": "allow"},
        ]
        v2_rules = parse_v2_permissions(v2_raw)
        v2_result = serialize_v2_permissions(v2_rules)
        actions = [r["action"] for r in v2_result]
        self.assertIn("shell", actions)
        self.assertIn("subagent", actions)
        self.assertNotIn("bash", actions)
        self.assertNotIn("task", actions)

    def test_to_and_from_generation_dispatch(self):
        v1_raw = {"edit": "deny"}
        rules = to_generation("v1", v1_raw)
        self.assertEqual(from_generation("v1", rules), v1_raw)

        v2_raw = [{"action": "edit", "resource": "*", "effect": "deny"}]
        rules = to_generation("v2", v2_raw)
        self.assertEqual(from_generation("v2", rules), v2_raw)


class MissingPermissionSemanticsTests(unittest.TestCase):
    def test_missing_v1_permission_is_empty_not_deny(self):
        self.assertEqual(parse_v1_permission(None), [])
        self.assertEqual(serialize_v1_permission([]), None)

    def test_missing_v2_permissions_is_empty_not_deny(self):
        self.assertEqual(parse_v2_permissions(None), [])
        self.assertEqual(serialize_v2_permissions([]), None)

    def test_removing_all_rules_serialises_to_none(self):
        raw = {"edit": "deny", "bash": "ask"}
        rules = parse_v1_permission(raw)
        self.assertEqual(serialize_v1_permission(rules), raw)
        self.assertIsNone(serialize_v1_permission([]))


if __name__ == "__main__":
    unittest.main()


class V2RawRuleEditTests(unittest.TestCase):
    """Editing a V2 rule with extra/future fields preserves the extras."""

    RAW = {"action": "future_tool", "resource": "*", "effect": "deny",
           "timeout": 30, "priority": "high"}

    def test_changing_effect_preserves_extra_fields(self):
        rules = parse_v2_permissions([dict(self.RAW)])
        # Simulate the user changing the effect through the editor.
        from dataclasses import replace
        edited = [replace(rules[0], effect="allow")]
        result = serialize_v2_permissions(edited)
        self.assertEqual(result[0]["effect"], "allow")
        self.assertEqual(result[0]["timeout"], 30)
        self.assertEqual(result[0]["priority"], "high")
        self.assertEqual(result[0]["action"], "future_tool")

    def test_changing_resource_preserves_extra_fields(self):
        rules = parse_v2_permissions([dict(self.RAW)])
        from dataclasses import replace
        edited = [replace(rules[0], resource="src/**")]
        result = serialize_v2_permissions(edited)
        self.assertEqual(result[0]["resource"], "src/**")
        self.assertEqual(result[0]["timeout"], 30)

    def test_clearing_resource_removes_it_but_keeps_extras(self):
        rules = parse_v2_permissions([dict(self.RAW)])
        from dataclasses import replace
        edited = [replace(rules[0], resource=None)]
        result = serialize_v2_permissions(edited)
        self.assertNotIn("resource", result[0])
        self.assertEqual(result[0]["timeout"], 30)

    def test_changing_action_preserves_extra_fields(self):
        rules = parse_v2_permissions([dict(self.RAW)])
        from dataclasses import replace
        edited = [replace(rules[0], action="renamed_tool")]
        result = serialize_v2_permissions(edited)
        self.assertEqual(result[0]["action"], "renamed_tool")
        self.assertEqual(result[0]["priority"], "high")

    def test_unrelated_raw_rule_order_preserved(self):
        other_raw = {"action": "shell", "resource": "*", "effect": "ask"}
        rules = parse_v2_permissions([dict(self.RAW), dict(other_raw)])
        from dataclasses import replace
        edited = [replace(rules[0], effect="allow"), rules[1]]
        result = serialize_v2_permissions(edited)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["action"], "future_tool")
        self.assertEqual(result[0]["effect"], "allow")
        self.assertEqual(result[0]["timeout"], 30)
        self.assertEqual(result[1], other_raw)
