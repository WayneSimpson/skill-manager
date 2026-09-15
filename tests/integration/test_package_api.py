from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from skill_manager.application.skills.package_resolution import PackageResolution
from skill_manager.application.skills.package_resolution import PackageSource
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package


def _seed_package(spec) -> None:
    root = spec.root / "external-package"
    seed_skill_package(root / "skills", "one", "One")
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "example"}))
    config = spec.xdg_config_home / "opencode/opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skills": {"paths": [str(root / "skills")]}}))


def _resolved_fixture(skill_ref: str, *, work_dir):
    root = work_dir / "upstream"
    seed_skill_package(root / "skills", "one", "One")
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "example"}))
    return PackageResolution(
        "resolved",
        skill_ref,
        source=PackageSource("github", "github:example/package", revision="a" * 40),
        artifact_root=root,
        capabilities=SourcePackageDiscovery().inspect_root(root)["package"],
    )


class PackageApiTests(unittest.TestCase):
    def test_context_is_local_only_and_standalone_is_not_package_backed(self) -> None:
        with AppTestHarness(fixture_factory=lambda spec: seed_skill_package(spec.claude_root, "one", "One")) as app:
            row = app.get_json("/api/skills")["rows"][0]
            context = app.get_json(f"/api/skills/{row['skillRef']}/package-context")

        self.assertFalse(context["packageBacked"])
        self.assertEqual(context["observation"]["status"], "unresolved")
        self.assertIsNone(context["resolution"])
        self.assertIsNone(context["managedPackage"])

    def test_explicit_resolution_does_not_expose_temporary_artifact_paths(self) -> None:
        with AppTestHarness(fixture_factory=_seed_package) as app:
            row = app.get_json("/api/skills")["rows"][0]
            with patch.object(app.container.skills_queries, "resolve_package_source", side_effect=_resolved_fixture):
                context = app.post_json(f"/api/skills/{row['skillRef']}/resolve-package")

        self.assertTrue(context["packageBacked"])
        self.assertEqual(context["resolution"]["status"], "resolved")
        self.assertEqual(context["resolution"]["source"]["revision"], "a" * 40)
        self.assertNotIn("artifact_root", context["resolution"])
        self.assertNotIn("artifactRoot", context["resolution"])

    def test_failed_adoption_is_retained_without_fallback(self) -> None:
        with AppTestHarness(fixture_factory=_seed_package) as app:
            row = app.get_json("/api/skills")["rows"][0]
            failure = lambda skill_ref, *, work_dir: PackageResolution(
                "unavailable", skill_ref, reason="Source is inaccessible while offline"
            )
            with patch.object(app.container.skills_queries, "resolve_package_source", side_effect=failure):
                app.post_json(f"/api/skills/{row['skillRef']}/manage-package", expected_status=409)
            context = app.get_json(f"/api/skills/{row['skillRef']}/package-context")

        self.assertTrue(context["packageBacked"])
        self.assertEqual(context["resolution"]["status"], "unavailable")
        self.assertIn("inaccessible", context["resolution"]["reason"])
        self.assertIsNone(context["managedPackage"])

    def test_deployment_endpoint_rejects_browser_plans_and_reports_harnesses(self) -> None:
        with AppTestHarness(fixture_factory=_seed_package) as app:
            row = app.get_json("/api/skills")["rows"][0]
            with patch.object(app.container.skills_queries, "resolve_package_source", side_effect=_resolved_fixture):
                app.post_json(f"/api/skills/{row['skillRef']}/manage-package")
            package_id = app.get_json("/api/skills/managed-packages")["packages"][0]["id"]

            deployment_view = app.get_json(f"/api/skills/managed-packages/{package_id}/deployments")
            self.assertEqual({item["harness"] for item in deployment_view["harnesses"]}, {"claude", "codex", "cursor", "opencode"})
            app.post_json(
                f"/api/skills/managed-packages/{package_id}/deployments/codex",
                {"action": "deploy", "plan": {"strategy": "native-install"}},
                expected_status=422,
            )

    def test_explicit_github_resolution_can_manage_without_a_local_manifest(self):
        with AppTestHarness(fixture_factory=lambda spec: seed_skill_package(spec.claude_root, 'one', 'One')) as app:
            ref = app.get_json('/api/skills')['rows'][0]['skillRef']
            self.assertFalse(app.get_json(f'/api/skills/{ref}/package-context')['packageBacked'])
            with patch.object(app.container.skills_queries, 'resolve_package_source', side_effect=_resolved_fixture):
                resolved = app.post_json(f'/api/skills/{ref}/resolve-package')
                self.assertIsNone(resolved['observation']['package'])
                self.assertEqual(resolved['resolution']['status'], 'resolved')
                managed = app.post_json(f'/api/skills/{ref}/manage-package')
            self.assertEqual(app.get_json(f'/api/skills/{ref}/package-context')['managedPackage']['id'], managed['id'])

    def test_resolution_failures_remain_explicit_and_never_adopt_a_leaf(self):
        with AppTestHarness(fixture_factory=_seed_package) as app:
            ref = app.get_json('/api/skills')['rows'][0]['skillRef']
            for status in ('unresolved', 'ambiguous', 'unavailable'):
                with self.subTest(status=status), patch.object(app.container.skills_queries, 'resolve_package_source',
                        return_value=PackageResolution(status, ref, reason='Source requires review')):
                    result = app.post_json(f'/api/skills/{ref}/resolve-package')
                    self.assertEqual(result['resolution']['status'], status)
                    app.post_json(f'/api/skills/{ref}/manage-package', expected_status=409)
                    self.assertIsNone(app.get_json(f'/api/skills/{ref}/package-context')['managedPackage'])
                    self.assertEqual(app.get_json('/api/skills/managed-packages')['packages'], [])


if __name__ == "__main__":
    unittest.main()
