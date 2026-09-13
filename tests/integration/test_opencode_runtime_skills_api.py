from __future__ import annotations

from pathlib import Path
import unittest

from skill_manager.application.skills.runtime import RuntimeSkillClientError, RuntimeSkillRecord
from skill_manager.directory_links import is_directory_link

from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package


class _RuntimeClient:
    def __init__(self, records=(), error: Exception | None = None) -> None:
        self.records = tuple(records)
        self.error = error
        self.calls: list[dict[str, object]] = []

    def fetch(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.records


class OpenCodeRuntimeSkillsApiTests(unittest.TestCase):
    def test_ordinary_inventory_scans_never_contact_runtime_server(self) -> None:
        client = _RuntimeClient()

        with AppTestHarness(runtime_skill_client=client) as harness:
            skills = harness.get_json("/api/skills")
            status = harness.get_json("/api/opencode/runtime-skills/status")

        self.assertEqual(skills["rows"], [])
        self.assertEqual(client.calls, [])
        self.assertEqual(status["status"], "disconnected")

    def test_refresh_requires_consent_before_contacting_runtime_server(self) -> None:
        client = _RuntimeClient()

        with AppTestHarness(runtime_skill_client=client) as harness:
            result = harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": False,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
                expected_status=422,
            )

        self.assertIn("consent", result["error"])
        self.assertEqual(client.calls, [])

    def test_readable_runtime_skill_can_be_managed_without_replacing_plugin_files(self) -> None:
        holder: dict[str, Path] = {}

        def seed(spec) -> None:
            plugin_package = seed_skill_package(
                spec.root / "plugin" / "skills",
                "runtime-copy",
                "Runtime Copy",
                body="runtime body",
                support_files={"scripts/run.py": "original bytes"},
            )
            holder["package"] = plugin_package

        client = _RuntimeClient()
        with AppTestHarness(fixture_factory=seed, runtime_skill_client=client) as harness:
            plugin_package = holder["package"]
            original_skill = (plugin_package / "SKILL.md").read_bytes()
            client.records = (
                RuntimeSkillRecord(
                    name="Runtime Copy",
                    description="Runtime description",
                    location=plugin_package / "SKILL.md",
                    content=None,
                ),
            )
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
            )
            row = next(row for row in harness.get_json("/api/skills")["rows"] if row["name"] == "Runtime Copy")

            harness.post_json(f"/api/skills/{row['skillRef']}/manage")
            managed = next(row for row in harness.get_json("/api/skills")["rows"] if row["name"] == "Runtime Copy")
            harness.post_json(f"/api/skills/{managed['skillRef']}/enable", {"harness": "claude"})

            shared_package = harness.spec.skills_store_root / "runtime-copy"

            self.assertEqual((plugin_package / "SKILL.md").read_bytes(), original_skill)
            self.assertEqual((plugin_package / "scripts" / "run.py").read_text(encoding="utf-8"), "original bytes")
            self.assertEqual((shared_package / "scripts" / "run.py").read_text(encoding="utf-8"), "original bytes")
            self.assertTrue(is_directory_link(harness.spec.claude_root / "runtime-copy"))
            self.assertFalse(is_directory_link(plugin_package))

    def test_runtime_manage_preflights_disabled_opencode_before_writing(self) -> None:
        holder: dict[str, Path] = {}

        def seed(spec) -> None:
            holder["package"] = seed_skill_package(
                spec.root / "cache" / "plugin" / "skills",
                "runtime-preflight",
                "Runtime Preflight",
                body="runtime body",
            )

        client = _RuntimeClient()
        with AppTestHarness(fixture_factory=seed, runtime_skill_client=client) as harness:
            source_before = {
                path.relative_to(holder["package"]).as_posix(): path.read_bytes()
                for path in holder["package"].rglob("*")
                if path.is_file()
            }
            manifest_path = harness.spec.skills_store_root.parent / "manifest.json"
            manifest_before = manifest_path.read_bytes() if manifest_path.exists() else None
            store_before = {
                path.relative_to(harness.spec.skills_store_root).as_posix(): path.read_bytes()
                for path in harness.spec.skills_store_root.rglob("*")
                if path.is_file()
            }
            client.records = (
                RuntimeSkillRecord(
                    name="Runtime Preflight",
                    description="Runtime description",
                    location=holder["package"] / "SKILL.md",
                    content=None,
                ),
            )
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
            )
            row = next(row for row in harness.get_json("/api/skills")["rows"] if row["name"] == "Runtime Preflight")
            harness.put_json("/api/settings/harnesses/opencode/support", {"enabled": False})
            harness.post_json(f"/api/skills/{row['skillRef']}/manage", expected_status=400)

            source_after = {
                path.relative_to(holder["package"]).as_posix(): path.read_bytes()
                for path in holder["package"].rglob("*")
                if path.is_file()
            }
            manifest_after = manifest_path.read_bytes() if manifest_path.exists() else None
            store_after = {
                path.relative_to(harness.spec.skills_store_root).as_posix(): path.read_bytes()
                for path in harness.spec.skills_store_root.rglob("*")
                if path.is_file()
            }

        self.assertEqual(source_after, source_before)
        self.assertEqual(manifest_after, manifest_before)
        self.assertEqual(store_after, store_before)

    def test_static_and_runtime_sightings_for_one_package_are_deduplicated(self) -> None:
        holder: dict[str, Path] = {}

        def seed(spec) -> None:
            package = seed_skill_package(spec.opencode_root, "same", "Same Skill", body="same")
            holder["package"] = package

        with AppTestHarness(fixture_factory=seed) as harness:
            package = holder["package"]
            client = _RuntimeClient(
                [RuntimeSkillRecord("Same Skill", "", package / "SKILL.md", None)]
            )
            harness.container.opencode_runtime_skills.client = client
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
            )
            skills = harness.get_json("/api/skills")
            row = next(row for row in skills["rows"] if row["name"] == "Same Skill")
            detail = harness.get_json(f"/api/skills/{row['skillRef']}")

        self.assertEqual(len([item for item in skills["rows"] if item["name"] == "Same Skill"]), 1)
        self.assertEqual({item["scope"] for item in detail["locations"]}, {"canonical", "runtime"})
        runtime_location = next(item for item in detail["locations"] if item["scope"] == "runtime")
        self.assertEqual(runtime_location["sourceKind"], "runtime")

    def test_runtime_only_entries_expose_capability_reason_and_embedded_content(self) -> None:
        client = _RuntimeClient(
            [
                RuntimeSkillRecord(
                    name="Unavailable Runtime",
                    description="",
                    location=None,
                    content=None,
                ),
                RuntimeSkillRecord(
                    name="Embedded Runtime",
                    description='Embedded: "description"',
                    location=Path("/builtin/customize-opencode.md"),
                    content="# Embedded Runtime\n\nembedded body",
                    slash="/customize",
                ),
            ]
        )

        with AppTestHarness(runtime_skill_client=client) as harness:
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://localhost:4096",
                    "directory": "/tmp/plugins",
                },
            )
            skills = harness.get_json("/api/skills")
            unavailable = next(row for row in skills["rows"] if row["name"] == "Unavailable Runtime")
            embedded = next(row for row in skills["rows"] if row["name"] == "Embedded Runtime")
            detail = harness.get_json(f"/api/skills/{embedded['skillRef']}")
            harness.post_json(f"/api/skills/{embedded['skillRef']}/manage")
            after_manage = harness.get_json("/api/skills")

        self.assertFalse(unavailable["actions"]["canManage"])
        self.assertIn("cannot be copied", unavailable["actions"]["canManageReason"])
        self.assertTrue(embedded["actions"]["canManage"])
        self.assertIn("embedded body", detail["documentMarkdown"])
        self.assertEqual(
            [row["displayStatus"] for row in after_manage["rows"] if row["name"] == "Embedded Runtime"],
            ["Managed"],
        )

    def test_standalone_runtime_document_does_not_copy_unrelated_parent_files(self) -> None:
        holder: dict[str, Path] = {}

        def seed(spec) -> None:
            parent = spec.root / "cache" / "plugin" / "skills"
            parent.mkdir(parents=True)
            document = parent / "customize-opencode.md"
            document.write_text("# Customize\n", encoding="utf-8")
            (parent / "unrelated.txt").write_text("do not copy", encoding="utf-8")
            holder["document"] = document

        client = _RuntimeClient()
        with AppTestHarness(fixture_factory=seed, runtime_skill_client=client) as harness:
            client.records = (
                RuntimeSkillRecord(
                    name="Customize",
                    description='A "quoted" description',
                    location=holder["document"],
                    content=None,
                    slash="/customize",
                ),
            )
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/project",
                },
            )
            row = next(row for row in harness.get_json("/api/skills")["rows"] if row["name"] == "Customize")
            harness.post_json(f"/api/skills/{row['skillRef']}/manage")
            managed_row = next(row for row in harness.get_json("/api/skills")["rows"] if row["name"] == "Customize")
            managed = harness.spec.skills_store_root / next(
                path.name for path in harness.spec.skills_store_root.iterdir() if path.is_dir()
            )
            files = {path.relative_to(managed).as_posix() for path in managed.rglob("*") if path.is_file()}
            document_text = (managed / "SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(files, {"SKILL.md"})
        self.assertEqual(managed_row["displayStatus"], "Managed")
        self.assertEqual(managed_row["description"], 'A "quoted" description')
        self.assertIn('name: "Customize"', document_text)
        self.assertIn('description: "A \\"quoted\\" description"', document_text)
        self.assertIn('slash: "/customize"', document_text)

    def test_failed_refresh_preserves_static_inventory_and_marks_runtime_error(self) -> None:
        client = _RuntimeClient(error=RuntimeSkillClientError("runtime skill server timed out"))

        def seed(spec) -> None:
            seed_skill_package(spec.opencode_root, "static", "Static Skill")

        with AppTestHarness(fixture_factory=seed, runtime_skill_client=client) as harness:
            result = harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
                expected_status=502,
            )
            skills = harness.get_json("/api/skills")
            status = harness.get_json("/api/opencode/runtime-skills/status")

        self.assertIn("timed out", result["error"])
        self.assertIn("Static Skill", [row["name"] for row in skills["rows"]])
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["skillCount"], 0)

    def test_disconnect_clears_runtime_snapshot_without_touching_static_skills(self) -> None:
        client = _RuntimeClient(
            [RuntimeSkillRecord("Runtime Skill", "", None, "# Runtime Skill")]
        )

        with AppTestHarness(runtime_skill_client=client) as harness:
            harness.post_json(
                "/api/opencode/runtime-skills/refresh",
                {
                    "consent": True,
                    "serverUrl": "http://127.0.0.1:4096",
                    "directory": "/tmp/plugins",
                },
            )
            result = harness.post_json("/api/opencode/runtime-skills/disconnect")
            skills = harness.get_json("/api/skills")

        self.assertEqual(result["status"], "disconnected")
        self.assertEqual(result["skillCount"], 0)
        self.assertNotIn("Runtime Skill", [row["name"] for row in skills["rows"]])


if __name__ == "__main__":
    unittest.main()
