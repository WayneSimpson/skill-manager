import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.application.agents import OpenCodeAgentApplyService, OpenCodeAgentMutationService
from skill_manager.errors import MutationError
from skill_manager.harness import HarnessKernelService, HarnessSupportStore
from skill_manager.opencode.apply_lifecycle import (
    AgentApplyStateStore,
    ManagedRuntimeRegistry,
    OpenCodeCapabilityDetector,
    parse_cli_commands,
)
from tests.unit.test_opencode_apply_lifecycle import FakeController, V1_HELP, RELOAD_HELP, make_kernel

V1_CONFIG = """{
  "agent": {
    "reviewer": {"description": "Reviews code", "mode": "subagent", "prompt": "Old."}
  }
}
"""


class AgentApplyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = make_kernel(self.root)
        self.config = self.root / "home/.config/opencode/opencode.jsonc"
        self.config.parent.mkdir(parents=True)
        self.store = AgentApplyStateStore(self.root / "state" / "apply.json")
        self.registry = ManagedRuntimeRegistry()

    def write_config(self, body: str) -> None:
        self.config.write_text(body, encoding="utf-8")

    def saved_hash(self) -> str:
        return hashlib.sha256(self.config.read_bytes()).hexdigest()

    def service_with(self, *, command_registry=None, reload_executor=None,
                     reload_verifier=None, controller=None):
        if controller is not None:
            self.registry.register(controller)
        return OpenCodeAgentApplyService(
            self.kernel,
            self.store,
            OpenCodeCapabilityDetector(command_registry=command_registry,
                                       managed_registry=self.registry),
            managed_registry=self.registry,
            reload_executor=reload_executor,
            reload_verifier=reload_verifier,
        )

    def mutation_service(self):
        return OpenCodeAgentMutationService(
            self.kernel, self.root / "state" / "backups", apply_store=self.store,
        )

    # 3/4. Pending state follows changed vs no-op saves and survives reload.
    def test_changed_save_marks_pending_and_noop_save_does_not(self):
        self.write_config(V1_CONFIG)
        mutations = self.mutation_service()
        mutations.update_agent("reviewer", "v1", {"description": "New"},
                               expected_hash=mutations.source_hash(self.config))
        self.assertTrue(self.store.status(str(self.config))["pending"])

        # A no-op save writes nothing: pending state is unchanged (still pending
        # until applied), and a fresh store observes the same durable state.
        mutations.update_agent("reviewer", "v1", {},
                               expected_hash=mutations.source_hash(self.config))
        self.assertTrue(
            AgentApplyStateStore(self.root / "state" / "apply.json")
            .status(str(self.config))["pending"]
        )

    # 5. Confirmation is server-enforced.
    def test_apply_requires_explicit_confirmation(self):
        self.write_config(V1_CONFIG)
        service = self.service_with(command_registry=lambda: parse_cli_commands(V1_HELP))
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=False)
        self.assertEqual(caught.exception.status, 422)
        with self.assertRaises(MutationError):
            service.apply(confirm=None)

    # 1/2/6. Current unmanaged runtime: restart-required, no reload, no auto-restart.
    def test_unmanaged_runtime_reports_manual_and_refuses_automated_apply(self):
        self.write_config(V1_CONFIG)
        for registry in (None, lambda: parse_cli_commands(V1_HELP)):
            service = self.service_with(command_registry=registry)
            capability = service.capability()
            self.assertEqual(capability["mechanism"], "restart-manual")
            self.assertFalse(capability["reloadAvailable"])
            self.assertFalse(capability["canExecute"])
            self.store.mark_saved(str(self.config), self.saved_hash())
            with self.assertRaises(MutationError) as caught:
                service.apply(confirm=True)
            self.assertEqual(caught.exception.status, 409)
            self.assertIn("manual", str(caught.exception).lower())

    # 2. Nothing pending: refuse before touching any executor/controller.
    def test_apply_with_nothing_pending_never_executes(self):
        self.write_config(V1_CONFIG)
        executor = lambda command: (_ for _ in ()).throw(AssertionError("executor called"))
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=executor,
            reload_verifier=lambda *args: True,
        )
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("no saved change pending", str(caught.exception))

    # 2. Saved file changed since save: refuse before any runtime action.
    def test_apply_with_stale_pending_file_never_executes(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), "an-old-hash")
        executor = lambda command: (_ for _ in ()).throw(AssertionError("executor called"))
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=executor,
            reload_verifier=lambda *args: True,
        )
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("changed after it was saved", str(caught.exception))
        self.assertTrue(self.store.status(str(self.config))["pending"])

    # 8. Reload branch is preferred when capability says it is safely available.
    def test_reload_branch_executes_only_with_confirmation_and_verifies(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        calls = []

        def executor(command: str) -> bool:
            calls.append(command)
            return True

        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=executor,
            reload_verifier=lambda target, saved_hash: True,
        )
        self.assertEqual(service.capability()["mechanism"], "reload")
        with self.assertRaises(MutationError):
            service.apply(confirm=False)  # Never reaches the executor.

        result = service.apply(confirm=True)
        self.assertEqual(calls, ["reload"])
        self.assertTrue(result["applied"])
        self.assertFalse(self.store.status(str(self.config))["pending"])

    # 7. Managed restart only for owned handles; others raise before any action.
    def test_managed_restart_requires_owned_handle_and_verification(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        controller = FakeController()
        controller.owned.append("handle-1")
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(V1_HELP), controller=controller,
        )
        self.assertEqual(service.capability()["mechanism"], "restart-managed")

        # A handle nobody owns is refused by the registry before restart.
        with self.assertRaises(PermissionError):
            self.registry.restart("foreign-handle")

        result = service.apply(confirm=True)
        self.assertEqual(controller.restart_calls, ["handle-1"])
        self.assertTrue(result["applied"])
        self.assertFalse(self.store.status(str(self.config))["pending"])

    # 9/10. Failed managed restart/restart-verification keeps pending and config.
    def test_failed_managed_restart_keeps_pending_and_config(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        before = self.config.read_text()
        controller = FakeController()
        controller.owned.append("handle-1")

        controller.restart_ok = False
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(V1_HELP), controller=controller,
        )
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 502)
        self.assertTrue(self.store.status(str(self.config))["pending"])
        self.assertEqual(self.config.read_text(), before)

        # Restart succeeds but verification cannot prove the config is active.
        controller.restart_ok = True
        controller.verify_result = False
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("could not be verified", str(caught.exception))
        self.assertTrue(self.store.status(str(self.config))["pending"])
        self.assertEqual(self.config.read_text(), before)

    # 11. Reload execution failure OR runtime-verification failure keeps pending.
    def test_reload_verification_failure_keeps_pending(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=lambda command: False,
            reload_verifier=lambda *args: True,
        )
        with self.assertRaises(MutationError):
            service.apply(confirm=True)
        self.assertTrue(self.store.status(str(self.config))["pending"])

        # Executor succeeds but the runtime verifier cannot confirm the config.
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=lambda command: True,
            reload_verifier=lambda target, saved_hash: False,
        )
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("could not be verified", str(caught.exception))
        self.assertTrue(self.store.status(str(self.config))["pending"])

    # 3. Reload without a runtime verifier is not safely executable at all.
    def test_reload_without_verifier_is_not_executable(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        executor = lambda command: (_ for _ in ()).throw(AssertionError("executor called"))
        service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=executor,
        )
        self.assertFalse(service.capability()["canExecute"])
        self.assertIn("automated apply is unavailable", service.capability()["detail"])
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(self.store.status(str(self.config))["pending"])

    # Manual acknowledgement is an explicit user assertion, not a fake execution.
    def test_manual_acknowledge_requires_confirmation_and_clears_pending(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        service = self.service_with(command_registry=lambda: parse_cli_commands(V1_HELP))
        with self.assertRaises(MutationError):
            service.acknowledge_manual(confirm=False)
        result = service.acknowledge_manual(confirm=True)
        self.assertTrue(result["acknowledged"])
        self.assertFalse(self.store.status(str(self.config))["pending"])

    # 5. Acknowledgement is refused for reload/managed mechanisms.
    def test_manual_acknowledge_is_refused_for_executable_mechanisms(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), self.saved_hash())
        reload_service = self.service_with(
            command_registry=lambda: parse_cli_commands(RELOAD_HELP),
            reload_executor=lambda command: True,
            reload_verifier=lambda *args: True,
        )
        with self.assertRaises(MutationError) as caught:
            reload_service.acknowledge_manual(confirm=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("current mechanism is reload", str(caught.exception))

        controller = FakeController()
        controller.owned.append("handle-1")
        managed_service = self.service_with(
            command_registry=lambda: parse_cli_commands(V1_HELP), controller=controller,
        )
        with self.assertRaises(MutationError):
            managed_service.acknowledge_manual(confirm=True)
        self.assertTrue(self.store.status(str(self.config))["pending"])

    # 5. Manual acknowledgement covers every pending target after preflight.
    def test_manual_acknowledge_covers_legacy_declaring_target(self):
        self.write_config(V1_CONFIG)
        legacy = self.root / "home/.opencode/opencode.jsonc"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"agent": {"legacy-agent": {"description": "x"}}}',
                          encoding="utf-8")
        self.store.mark_saved(str(self.config), self.saved_hash())
        self.store.mark_saved(str(legacy),
                             __import__("hashlib").sha256(legacy.read_bytes()).hexdigest())
        service = self.service_with(command_registry=lambda: parse_cli_commands(V1_HELP))
        status = service.status()
        self.assertTrue(status["pending"])
        self.assertEqual(sorted(status["pendingTargets"]),
                         sorted([str(self.config), str(legacy)]))

        result = service.acknowledge_manual(confirm=True)
        self.assertEqual(sorted(result["acknowledgedTargets"]),
                         sorted([str(self.config), str(legacy)]))
        self.assertFalse(self.store.pending_targets())

    # 2. Acknowledgement preflight also refuses a stale pending file.
    def test_manual_acknowledge_refuses_stale_file(self):
        self.write_config(V1_CONFIG)
        self.store.mark_saved(str(self.config), "old-hash")
        service = self.service_with(command_registry=lambda: parse_cli_commands(V1_HELP))
        with self.assertRaises(MutationError) as caught:
            service.acknowledge_manual(confirm=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(self.store.status(str(self.config))["pending"])

    def test_status_reports_target_and_pending_state(self):
        self.write_config(V1_CONFIG)
        service = self.service_with(command_registry=lambda: parse_cli_commands(V1_HELP))
        status = service.status()
        self.assertEqual(status["target"], str(self.config))
        self.assertFalse(status["pending"])
        self.store.mark_saved(str(self.config), "x")
        self.assertTrue(service.status()["pending"])


if __name__ == "__main__":
    unittest.main()


class SaveTransactionStateFailureTests(unittest.TestCase):
    """Finding A: a failing pending-state store rolls the config back."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kernel = make_kernel(self.root)
        self.config = self.root / "home/.config/opencode/opencode.jsonc"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            '{"agent": {"reviewer": {"description": "Original", "mode": "subagent"}}}',
            encoding="utf-8",
        )

    def test_mark_saved_failure_restores_original_config(self):
        from tests.unit.test_opencode_apply_lifecycle import AgentApplyStateError

        class ExplodingStore(AgentApplyStateStore):
            def mark_saved(self, target, saved_hash):
                raise AgentApplyStateError("state store exploded")

        before = self.config.read_text()
        service = OpenCodeAgentMutationService(
            self.kernel, self.root / "state" / "backups",
            apply_store=ExplodingStore(self.root / "state" / "apply.json"),
        )
        with self.assertRaises(MutationError) as caught:
            service.update_agent(
                "reviewer", "v1", {"description": "Changed"},
                expected_hash=service.source_hash(self.config),
            )
        self.assertEqual(caught.exception.status, 500)
        self.assertIn("rolled back", str(caught.exception))
        self.assertIn("pending state", str(caught.exception).lower())
        # The configuration is byte-identical to the original: no false success
        # and no changed config behind a reported failure.
        self.assertEqual(self.config.read_text(), before)

    def test_mark_saved_oserror_also_rolls_back(self):
        class ExplodingStore(AgentApplyStateStore):
            def mark_saved(self, target, saved_hash):
                raise OSError("disk full")

        before = self.config.read_text()
        service = OpenCodeAgentMutationService(
            self.kernel, self.root / "state" / "backups",
            apply_store=ExplodingStore(self.root / "state" / "apply.json"),
        )
        with self.assertRaises(MutationError) as caught:
            service.update_agent(
                "reviewer", "v1", {"description": "Changed"},
                expected_hash=service.source_hash(self.config),
            )
        self.assertEqual(caught.exception.status, 500)
        self.assertEqual(self.config.read_text(), before)

    def test_corrupt_state_store_blocks_apply_and_status(self):
        # Finding C surfacing: corrupt state never reads as "nothing pending".
        corrupt = self.root / "state" / "apply.json"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_text("{corrupt", encoding="utf-8")
        service = OpenCodeAgentApplyService(
            self.kernel,
            AgentApplyStateStore(corrupt),
            OpenCodeCapabilityDetector(command_registry=None),
        )
        with self.assertRaises(MutationError) as caught:
            service.status()
        self.assertEqual(caught.exception.status, 503)
        with self.assertRaises(MutationError) as caught:
            service.apply(confirm=True)
        self.assertEqual(caught.exception.status, 503)
        with self.assertRaises(MutationError) as caught:
            service.acknowledge_manual(confirm=True)
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("not valid JSON", str(caught.exception))
