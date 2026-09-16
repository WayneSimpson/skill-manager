from __future__ import annotations

from unittest.mock import Mock, patch
import unittest

from skill_manager.application.skills.package_deployment_service import PackageDeploymentError
from skill_manager.application.skills.package_deployment import PackageDeploymentPlan
from skill_manager.application.skills.queries import SkillsQueryService, _actions


class PackageQueryDeploymentTests(unittest.TestCase):
    def test_opencode_actions_do_not_advertise_unsupported_enable_toggle(self) -> None:
        plan = PackageDeploymentPlan("a" * 64, "opencode", {})
        plan.strategy = "native-install"
        plan.support = "supported"
        plan.ownership = "managed"
        plan.actions = [{"action": "reconcile-whole-package"}]

        class OpenCodeAdapter:
            def uninstall(self):
                pass

        self.assertEqual(
            _actions(plan, {"enabled": None}, "installed", OpenCodeAdapter()),
            ("remove",),
        )

    def test_update_returns_deployments_for_replacement_package(self) -> None:
        old_package_id = "a" * 64
        replacement_package_id = "b" * 64
        deployment = {
            "deploymentId": "deployment-1",
            "managedPackageId": old_package_id,
            "harness": "claude",
        }
        service = Mock()
        service.list_deployments.return_value = {deployment["deploymentId"]: deployment}
        query = SkillsQueryService.__new__(SkillsQueryService)
        query.managed_packages = Mock()
        query.read_models = Mock()
        query._replacement_options = Mock(return_value=[{'packageId': replacement_package_id}])
        factory = Mock(return_value=Mock())

        with patch(
            "skill_manager.application.skills.queries.PackageDeploymentService",
            return_value=service,
        ):
            query.get_package_deployments = Mock(return_value={"packageId": replacement_package_id})
            result = query.mutate_package_deployment(
                old_package_id,
                "claude",
                "update",
                factory,
                replacement_package_id=replacement_package_id,
            )

        self.assertEqual(result["packageId"], replacement_package_id)
        query.get_package_deployments.assert_called_once_with(replacement_package_id, factory)
        service.update.assert_called_once_with("deployment-1", replacement_package_id, factory.return_value)

    def test_deployment_view_contains_factory_failures_instead_of_raising(self) -> None:
        class RaisingFactory:
            def __call__(self, _harness):
                raise RuntimeError("native configuration is unavailable")

            @staticmethod
            def diagnostics(_harness):
                return ("native-adapter-unavailable",)

        query = SkillsQueryService.__new__(SkillsQueryService)

        result = query._deployment_view(
            "a" * 64,
            "claude",
            None,
            Mock(),
            RaisingFactory(),
        )

        self.assertEqual(result["state"], "manual")
        self.assertEqual(result["ownership"], "absent")
        self.assertIn("native-inspection-failed", result["blockers"])
        self.assertIn("native-adapter-unavailable", result["preflight"])

    def test_update_validates_replacement_before_constructing_native_adapter(self) -> None:
        package_id = "a" * 64
        deployment = {"deploymentId": "deployment-1", "managedPackageId": package_id, "harness": "claude"}
        service = Mock()
        service.list_deployments.return_value = {deployment["deploymentId"]: deployment}
        query = SkillsQueryService.__new__(SkillsQueryService)
        query.managed_packages = Mock()
        query.read_models = Mock()
        factory = Mock(side_effect=RuntimeError("must not be constructed"))

        with patch(
            "skill_manager.application.skills.queries.PackageDeploymentService",
            return_value=service,
        ):
            with self.assertRaisesRegex(PackageDeploymentError, "explicit replacement package ID"):
                query.mutate_package_deployment(package_id, "claude", "update", factory)

        factory.assert_not_called()

    def test_mutation_reports_factory_failure_as_a_deployment_error(self) -> None:
        package_id = "a" * 64
        service = Mock()
        service.list_deployments.return_value = {}
        query = SkillsQueryService.__new__(SkillsQueryService)
        query.managed_packages = Mock()
        query.read_models = Mock()
        factory = Mock(side_effect=RuntimeError("native configuration is unavailable"))

        with patch(
            "skill_manager.application.skills.queries.PackageDeploymentService",
            return_value=service,
        ):
            with self.assertRaisesRegex(PackageDeploymentError, "Native adapter is unavailable"):
                query.mutate_package_deployment(package_id, "claude", "deploy", factory)


if __name__ == "__main__":
    unittest.main()
