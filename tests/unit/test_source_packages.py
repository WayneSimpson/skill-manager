from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.application.skills.source_package import SourcePackageDiscovery


def write_skill(path: Path, name: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    skill = path / "SKILL.md"
    title = name or path.name
    skill.write_text(f"---\nname: {title}\n---\n\n# {title}\n", encoding="utf-8")
    return skill


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class SourcePackageDiscoveryTests(unittest.TestCase):
    def test_codex_loader_defaults_and_explicit_replacements(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = write_skill(root / "skills/nested/one")
            write_json(root / ".codex-plugin/plugin.json", {"name": "codex"})
            write_json(root / ".mcp.json", {"mcpServers": {"demo": {"url": "https://example.com"}}})
            write_json(root / "hooks/hooks.json", {"hooks": {"SessionStart": []}})
            write_json(root / ".app.json", {})
            package = SourcePackageDiscovery().resolve(skill)["package"]
            self.assertIsNotNone(package)
            self.assertEqual({c["kind"] for c in package["components"]}, {"skills", "mcp", "hooks", "apps"})
            other = write_skill(root / "custom/other")
            write_json(root / ".codex-plugin/plugin.json", {"name": "codex", "skills": "./custom"})
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")
            self.assertEqual(SourcePackageDiscovery().resolve(other)["status"], "resolved")

    def test_mcp_entry_changes_refresh_capability_fingerprint_without_secret_values(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = write_skill(root / "skills/one")
            write_json(root / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "safe"})
            config = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json", "mcpServers": {}}
            write_json(root / "mcp.json", config)
            before = SourcePackageDiscovery().resolve(skill)["package"]
            config["mcpServers"]["demo"] = {"type": "stdio", "command": "node", "env": {"TOKEN": "DO_NOT_EXPOSE"}}
            write_json(root / "mcp.json", config)
            after = SourcePackageDiscovery().resolve(skill)["package"]
            self.assertNotEqual(before["revision"], after["revision"])
            self.assertNotIn("DO_NOT_EXPOSE", json.dumps(after))
            self.assertEqual(next(c for c in after["components"] if c["kind"] == "mcp")["entries"], ["demo"])
    def test_standard_names_schema_and_metadata_are_validated_at_root(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = write_skill(root / "skills" / "one")
            for extra in ({"name": "has--double"}, {"name": "has..double"},
                          {"author": None}, {"keywords": None}, {"homepage": []},
                          {"$schema": "https://agent-plugins.org/schemas/2.0.0/plugin.schema.json"}):
                with self.subTest(extra=extra):
                    write_json(root / "plugin.json", {
                        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                        "name": "valid", **extra,
                    })
                    self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")

    def test_claude_and_cursor_defaults_replacements_and_root_skill(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = write_skill(root)
            write_json(root / ".claude-plugin/plugin.json", {"name": "claude"})
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "resolved")
            (root / ".claude-plugin/plugin.json").unlink()
            write_json(root / ".cursor-plugin/plugin.json", {"name": "cursor"})
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "resolved")
            (root / ".cursor-plugin/plugin.json").unlink()
            (root / "commands").mkdir()
            (root / "commands/default.md").write_text("default")
            (root / "commands/ignored.env").write_text("PRIVATE_VALUE")
            (root / "other.md").write_text("explicit")
            write_json(root / ".claude-plugin/plugin.json", {"name": "claude", "commands": "./other.md"})
            package = SourcePackageDiscovery().resolve(skill)["package"]
            commands = [c["path"] for c in package["components"] if c["kind"] == "commands"]
            self.assertEqual(commands, ["other.md"])

    def test_eight_parent_limit_and_nested_source_boundary(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root.joinpath(*[f"level{i}" for i in range(9)])
            skill = write_skill(directory)
            write_json(root / ".cursor-plugin/plugin.json", {"name": "outer", "skills": str(directory.relative_to(root))})
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")
            inner = write_skill(root / "nested/skills/one")
            write_json(root / ".cursor-plugin/plugin.json", {"name": "outer", "skills": "nested/skills"})
            (root / "nested/package.json").write_text("{}")
            self.assertEqual(SourcePackageDiscovery().resolve(inner)["status"], "unresolved")

    def test_invalid_vendor_config_is_reported_without_hiding_skills(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = write_skill(root / "skills/one")
            write_json(root / ".cursor-plugin/plugin.json", {"name": "cursor"})
            (root / "mcp.json").write_text("broken JSON")
            package = SourcePackageDiscovery().resolve(skill)["package"]
            self.assertFalse(any(c["kind"] == "mcp" for c in package["components"]))
            self.assertTrue(any("invalid JSON" in d for d in package["diagnostics"]))

    def test_in_package_skill_symlink_is_allowed_but_escape_is_not(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "plugin"
            one = write_skill(root / "skills/one")
            write_json(root / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "safe"})
            two = root / "skills/two"
            two.mkdir()
            (two / "SKILL.md").symlink_to(one)
            self.assertEqual(SourcePackageDiscovery().resolve(two)["status"], "resolved")
            outside = write_skill(Path(temporary) / "external")
            (two / "SKILL.md").unlink()
            (two / "SKILL.md").symlink_to(outside)
            self.assertEqual(SourcePackageDiscovery().resolve(two)["status"], "unresolved")
    def test_agent_plugin_manifest_discovers_portable_skills_and_mcp(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "portable"
            skill = write_skill(root / "skills" / "first", "first")
            write_skill(root / "skills" / "second", "second")
            write_json(
                root / "plugin.json",
                {
                    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                    "name": "portable-plugin",
                    "version": "1.2.3",
                },
            )
            write_json(
                root / "mcp.json",
                {
                    "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                    "mcpServers": {"demo": {"command": "do-not-run"}},
                },
            )

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            self.assertEqual(package["name"], "portable-plugin")
            self.assertEqual(package["version"], "1.2.3")
            self.assertEqual(package["root"], str(root.resolve()))
            self.assertTrue(any(item["kind"] == "skills" and item["harness"] is None for item in package["components"]))
            mcp = [item for item in package["components"] if item["kind"] == "mcp"]
            self.assertEqual(len(mcp), 1)
            self.assertFalse(mcp[0]["supported"])
            self.assertNotIn("do-not-run", json.dumps(package))

    def test_vendor_manifests_aggregate_without_making_vendor_components_portable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "vendors"
            skill = write_skill(root / "skills" / "shared", "shared")
            write_json(root / ".claude-plugin" / "plugin.json", {"name": "claude-plugin", "skills": "skills"})
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "cursor-plugin", "skills": "skills"})
            write_json(root / ".codex-plugin" / "plugin.json", {"name": "codex-plugin", "skills": "./skills"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            skills = [item for item in package["components"] if item["kind"] == "skills"]
            self.assertEqual({item["harness"] for item in skills}, {"claude", "cursor", "codex"})
            self.assertTrue(all(item["supported"] for item in skills))
            self.assertTrue(all(item["evidence"] == "declared_harness_manifest" for item in skills))

    def test_claude_defaults_and_custom_paths_are_structural_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "claude"
            skill = write_skill(root / "skills" / "default", "default")
            write_skill(root / "custom-skills" / "explicit", "explicit")
            (root / "hooks").mkdir(parents=True)
            write_json(root / ".claude-plugin" / "plugin.json", {"name": "claude-plugin", "skills": "custom-skills", "hooks": {"PreToolUse": [{"command": "touch sentinel"}]}})
            (root / "hooks" / "hooks.json").write_text("{\"hooks\": []}", encoding="utf-8")

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            paths = {item["path"] for item in package["components"]}
            self.assertIn("skills/default", paths)
            self.assertIn("custom-skills/explicit", paths)
            self.assertTrue(any(item["kind"] == "hooks" and item["path"].endswith("#hooks") for item in package["components"]))

    def test_cursor_custom_declarations_replace_defaults_and_codex_requires_dot_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "mixed"
            skill = write_skill(root / "custom" / "one", "one")
            write_skill(root / "skills" / "not-declared", "not-declared")
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "cursor.plugin", "skills": "custom"})
            write_json(root / ".codex-plugin" / "plugin.json", {"name": "codex", "skills": "custom", "agents": "agents"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            skills = [item for item in package["components"] if item["kind"] == "skills"]
            self.assertEqual({item["harness"] for item in skills}, {"cursor"})
            self.assertTrue(any("agents" in diagnostic for diagnostic in package["diagnostics"]))

    def test_codex_explicit_declarations_are_structural_and_do_not_use_defaults(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "codex"
            skill = write_skill(root / "explicit-skills" / "one", "one")
            (root / "hooks").mkdir(parents=True)
            (root / "hooks" / "hooks.json").write_text("{\"command\": \"do-not-run\"}", encoding="utf-8")
            write_json(
                root / ".codex-plugin" / "plugin.json",
                {
                    "name": "codex-plugin",
                    "version": "0.1.0",
                    "skills": "./explicit-skills",
                    "hooks": "./hooks/hooks.json",
                    "mcpServers": {"demo": {"command": "do-not-run"}},
                    "apps": "./apps",
                },
            )

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            self.assertEqual(package["name"], "codex-plugin")
            self.assertEqual(package["version"], "0.1.0")
            self.assertEqual({item["kind"] for item in package["components"]}, {"skills", "hooks", "mcp"})
            self.assertNotIn("do-not-run", json.dumps(package))

    def test_malformed_standard_mcp_does_not_hide_standard_skills(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "bad-mcp"
            skill = write_skill(root / "skills" / "one", "one")
            write_json(root / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "bad-mcp"})
            write_json(root / "mcp.json", {"$schema": "wrong", "mcpServers": []})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            self.assertTrue(any(item["kind"] == "skills" and item["harness"] is None for item in package["components"]))
            self.assertFalse(any(item["kind"] == "mcp" and item["harness"] is None for item in package["components"]))
            self.assertTrue(any("canonical MCP schema" in diagnostic for diagnostic in package["diagnostics"]))

    def test_invalid_standard_fields_do_not_grant_portable_evidence_to_vendor_components(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "invalid-standard"
            skill = write_skill(root / "skills" / "one", "one")
            write_json(
                root / "plugin.json",
                {
                    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                    "name": "valid-name",
                    "author": {"name": "ok", "secret": "not-allowed"},
                },
            )
            write_json(root / ".claude-plugin" / "plugin.json", {"name": "vendor-plugin", "skills": "skills"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "resolved")
            package = result["package"]
            assert package is not None
            self.assertTrue(all(item["harness"] == "claude" for item in package["components"] if item["kind"] == "skills"))
            self.assertNotIn("not-allowed", json.dumps(package))

    def test_cursor_root_skill_fallback_does_not_recurse_into_unrelated_directories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cursor-fallback"
            skill = write_skill(root / "nested" / "one", "one")
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "cursor-plugin"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "unresolved")
            self.assertIsNone(result["package"])

    def test_manifest_component_and_skill_symlinks_cannot_escape_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            outside = base / "outside"
            outside.mkdir()
            write_skill(outside / "outside-skill", "outside-skill")

            manifest_root = base / "manifest-link"
            skill = write_skill(manifest_root / "skills" / "one", "one")
            (manifest_root / ".claude-plugin").mkdir(parents=True)
            (manifest_root / ".claude-plugin" / "plugin.json").symlink_to(outside / "plugin.json")
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")

            component_root = base / "component-link"
            component_skill = write_skill(component_root / "skills" / "one", "one")
            write_json(component_root / ".cursor-plugin" / "plugin.json", {"name": "cursor", "skills": "./external"})
            (component_root / "external").symlink_to(outside, target_is_directory=True)
            self.assertEqual(SourcePackageDiscovery().resolve(component_skill)["status"], "unresolved")

            skill_root = base / "skill-link"
            (skill_root / "skills" / "one").mkdir(parents=True)
            (skill_root / "skills" / "one" / "SKILL.md").symlink_to(outside / "outside-skill" / "SKILL.md")
            write_json(skill_root / ".claude-plugin" / "plugin.json", {"name": "skill-link"})
            self.assertEqual(SourcePackageDiscovery().resolve(skill_root / "skills" / "one")["status"], "unresolved")

    def test_fifo_and_size_budgets_are_rejected_without_reading_unbounded_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "fifo"
            root.mkdir()
            skill_dir = root / "skills" / "one"
            skill_dir.mkdir(parents=True)
            os.mkfifo(skill_dir / "SKILL.md")
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "fifo"})
            self.assertEqual(SourcePackageDiscovery().resolve(skill_dir)["status"], "unresolved")

            oversized = Path(temp_dir) / "oversized"
            oversized_skill = write_skill(oversized / "skills" / "one", "one")
            plugin = oversized / ".claude-plugin" / "plugin.json"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("{" + '"name":"oversized",' + '"padding":"' + ("x" * (1024 * 1024)) + '"}', encoding="utf-8")
            result = SourcePackageDiscovery().resolve(oversized_skill)
            self.assertEqual(result["status"], "unresolved")
            self.assertIn("budget", result["reason"])

    def test_hook_scripts_are_never_executed_and_component_semantics_are_explicit(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sentinel"
            skill = write_skill(root / "skills" / "one", "one")
            sentinel = root / "executed"
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "sentinel", "hooks": {"PreToolUse": [{"command": f"touch {sentinel}"}]}, "mcpServers": {"demo": {"command": "secret-command"}}})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertFalse(sentinel.exists())
            package = result["package"]
            assert package is not None
            self.assertTrue(any(item["path"].endswith("#hooks") for item in package["components"]))
            self.assertTrue(any("unvalidated" in diagnostic for diagnostic in package["diagnostics"]))
            self.assertNotIn("secret-command", json.dumps(package))

    def test_new_discovery_instance_sees_component_removal(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "refresh"
            skill = write_skill(root / "skills" / "one", "one")
            second = write_skill(root / "skills" / "two", "two")
            write_json(root / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "refresh"})
            first = SourcePackageDiscovery().resolve(skill)
            self.assertEqual(first["status"], "resolved")
            second.unlink()
            refreshed = SourcePackageDiscovery().resolve(skill)
            self.assertEqual(refreshed["status"], "resolved")
            assert first["package"] is not None and refreshed["package"] is not None
            self.assertNotEqual(first["package"]["revision"], refreshed["package"]["revision"])

    def test_unrelated_ancestor_manifest_does_not_adopt_skill(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill = write_skill(root / "parent" / "unrelated" / "orphan", "orphan")
            write_json(root / "parent" / ".claude-plugin" / "plugin.json", {"name": "other"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "unresolved")
            self.assertIsNone(result["package"])

    def test_traversal_and_external_symlinks_are_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "safe"
            skill = write_skill(root / "skills" / "inside", "inside")
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "cursor", "skills": "../outside"})

            result = SourcePackageDiscovery().resolve(skill)

            self.assertEqual(result["status"], "unresolved")
            self.assertIsNone(result["package"])

    def test_same_root_has_stable_identity_and_revision_ignores_skill_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "stable"
            first = write_skill(root / "skills" / "first", "first")
            second = write_skill(root / "skills" / "second", "second")
            write_json(root / ".codex-plugin" / "plugin.json", {"name": "stable", "skills": "./skills"})

            discovery = SourcePackageDiscovery()
            one = discovery.resolve(first)
            two = discovery.resolve(second)
            self.assertEqual(one, two)
            assert one["package"] is not None
            one["package"]["components"].clear()
            self.assertTrue(discovery.resolve(first)["package"]["components"])
            package = one["package"]
            first.write_text("changed body", encoding="utf-8")
            self.assertEqual(package["revision"], discovery.resolve(first)["package"]["revision"])

            write_skill(root / "skills" / "third", "third")
            updated = SourcePackageDiscovery().resolve(first)
            self.assertEqual(updated["status"], "resolved")
            self.assertEqual(updated["package"]["id"], package["id"])
            self.assertNotEqual(updated["package"]["revision"], package["revision"])

    def test_root_is_parsed_once_but_each_skill_still_needs_to_be_declared(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "declared"
            listed = write_skill(root / "skills" / "listed", "listed")
            unlisted = write_skill(root / "other" / "unlisted", "unlisted")
            write_json(root / ".cursor-plugin" / "plugin.json", {"name": "declared", "skills": "./skills"})

            discovery = SourcePackageDiscovery()
            self.assertEqual(discovery.resolve(unlisted)["status"], "unresolved")
            listed_result = discovery.resolve(listed)

            self.assertEqual(listed_result["status"], "resolved")

    def test_standalone_skill_and_none_are_unresolved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            skill = write_skill(Path(temp_dir) / "standalone", "standalone")
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")
            self.assertEqual(SourcePackageDiscovery().resolve(None)["status"], "unresolved")

    def test_package_and_git_boundaries_are_not_treated_as_harness_manifests(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "boundaries"
            skill = write_skill(root / "skills" / "one", "one")
            (root / "package.json").write_text('{"name":"untrusted-package","main":"run.js"}', encoding="utf-8")
            self.assertEqual(SourcePackageDiscovery().resolve(skill)["status"], "unresolved")

            git_root = Path(temp_dir) / "git-boundary"
            git_skill = write_skill(git_root / "skills" / "one", "one")
            (git_root / ".git").mkdir()
            self.assertEqual(SourcePackageDiscovery().resolve(git_skill)["status"], "unresolved")

    def test_stop_paths_are_checked_before_source_inspection(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "excluded"
            skill = write_skill(root / "skills" / "one", "one")
            write_json(root / ".claude-plugin" / "plugin.json", {"name": "excluded"})

            result = SourcePackageDiscovery(stop_paths=(root,)).resolve(skill)

            self.assertEqual(result["status"], "unresolved")
            self.assertIsNone(result["package"])


if __name__ == "__main__":
    unittest.main()
