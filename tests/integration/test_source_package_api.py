from pathlib import Path
import json
import unittest
from unittest.mock import patch

from skill_manager.application.skills.runtime import RuntimeSkillRecord
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from tests.integration.test_opencode_runtime_skills_api import _RuntimeClient
from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package


class SourcePackageApiTests(unittest.TestCase):
    def test_configured_file_source_survives_adoption_without_runtime(self):
        def seed(spec):
            root = spec.root / "configured-plugin"
            seed_skill_package(root / "skills", "configured", "Configured")
            (root / "plugin.json").write_text(json.dumps({
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "configured",
            }))
            config = spec.xdg_config_home / "opencode/opencode.jsonc"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"skills": {"paths": [str(root / "skills")]}}))

        with AppTestHarness(fixture_factory=seed) as harness:
            row = harness.get_json("/api/skills")["rows"][0]
            before = harness.get_json(f"/api/skills/{row['skillRef']}")["sourcePackage"]
            self.assertEqual(before["status"], "resolved")
            harness.post_json(f"/api/skills/{row['skillRef']}/manage")
            row = harness.get_json("/api/skills")["rows"][0]
            after = harness.get_json(f"/api/skills/{row['skillRef']}")["sourcePackage"]
            self.assertEqual(after["status"], "resolved")
            self.assertEqual(before["sourcePath"], after["sourcePath"])
            self.assertEqual(before["package"]["id"], after["package"]["id"])

    def test_packages_are_grouped_and_source_provenance_survives_adoption(self):
        client = _RuntimeClient()
        with AppTestHarness(runtime_skill_client=client) as harness:
            root = harness.spec.root / "source-plugin"
            first = seed_skill_package(root / "skills", "first", "First")
            second = seed_skill_package(root / "skills", "second", "Second")
            manifest = root / "plugin.json"
            manifest.write_text(json.dumps({
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "example-package",
            }))
            client.records = tuple(RuntimeSkillRecord(name, "", folder / "SKILL.md", None)
                                   for name, folder in (("First", first), ("Second", second)))
            harness.post_json("/api/opencode/runtime-skills/refresh", {
                "consent": True, "serverUrl": "http://localhost:4096", "directory": "/project",
            })
            calls = []
            original = SourcePackageDiscovery._parse_root

            def counted(scanner, *args):
                calls.append(args[0])
                return original(scanner, *args)

            with patch.object(SourcePackageDiscovery, "_parse_root", counted):
                listing = harness.get_json("/api/skills/source-packages")
            self.assertEqual(calls, [root])
            self.assertEqual(len(listing["packages"]), 1)
            self.assertEqual(len(listing["skills"]), 2)
            package_id = listing["packages"][0]["id"]
            self.assertEqual({s["packageId"] for s in listing["skills"]}, {package_id})
            row = next(s for s in harness.get_json("/api/skills")["rows"] if s["name"] == "First")
            detail = harness.get_json(f"/api/skills/{row['skillRef']}")["sourcePackage"]
            self.assertEqual(detail["sourcePath"], str(first))
            self.assertEqual(detail["package"]["root"], str(root))
            self.assertEqual(detail["sourceKind"], "runtime")
            self.assertNotEqual(detail["sourceRevision"], detail["package"]["revision"])

            harness.post_json(f"/api/skills/{row['skillRef']}/manage")
            managed = next(s for s in harness.get_json("/api/skills")["rows"] if s["name"] == "First")
            adopted = harness.get_json(f"/api/skills/{managed['skillRef']}")["sourcePackage"]
            self.assertEqual(adopted["package"]["id"], package_id)
            self.assertEqual(adopted["sourcePath"], str(first))
            harness.post_json(f"/api/skills/{managed['skillRef']}/enable", {"harness": "claude"})
            self.assertEqual(len(client.calls), 1)

            # Capability reads do not retain the inventory's one-second cache.
            before = adopted["package"]["revision"]
            manifest.write_text(json.dumps({
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "example-package", "version": "2.0",
            }))
            changed = harness.get_json(f"/api/skills/{managed['skillRef']}")["sourcePackage"]
            self.assertNotEqual(changed["package"]["revision"], before)
            manifest.unlink()
            unresolved = harness.get_json(f"/api/skills/{managed['skillRef']}")["sourcePackage"]
            self.assertEqual(unresolved["status"], "unresolved")
            self.assertIsNone(unresolved["package"])

    def test_content_only_adoption_cannot_invent_package_from_store(self):
        client = _RuntimeClient([RuntimeSkillRecord("Content Only", "", None, "some content")])
        with AppTestHarness(runtime_skill_client=client) as harness:
            harness.post_json("/api/opencode/runtime-skills/refresh", {
                "consent": True, "serverUrl": "http://localhost:4096", "directory": "/project",
            })
            row = harness.get_json("/api/skills")["rows"][0]
            for _ in range(2):
                detail = harness.get_json(f"/api/skills/{row['skillRef']}")["sourcePackage"]
                self.assertEqual(detail["status"], "unresolved")
                self.assertIsNone(detail["sourcePath"])
                if row["displayStatus"] == "Unmanaged":
                    harness.post_json(f"/api/skills/{row['skillRef']}/manage")
                    row = harness.get_json("/api/skills")["rows"][0]
            self.assertEqual(len(client.calls), 1)
