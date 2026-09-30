import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skill_manager.errors import MutationError
from skill_manager.harness import HarnessKernelService, HarnessSupportStore
import os

from skill_manager.opencode.apply_lifecycle import (
    AgentApplyStateError,
    AgentApplyStateStore,
    ManagedRuntimeRegistry,
    OpenCodeCapabilityDetector,
    parse_cli_commands,
)

V1_HELP = """
Commands:
  opencode completion          generate shell completion script
  opencode serve               starts a headless opencode server
  opencode debug               debugging and troubleshooting tools
  opencode agent               manage agents

Positionals:
  project  path to start opencode in
"""

RELOAD_HELP = """
Commands:
  opencode completion          generate shell completion script
  opencode reload              reload configuration
  opencode serve               starts a headless opcode server
"""


def env_for(root: Path) -> dict[str, str]:
    home = root / "home"
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }


def make_kernel(root: Path) -> HarnessKernelService:
    return HarnessKernelService.from_environment(
        env_for(root), support_store=HarnessSupportStore(root / "state" / "settings.json")
    )


class FakeController:
    def __init__(self):
        self.owned = []
        self.restart_calls = []
        self.restart_ok = True
        self.verify_result = True

    def owned_handles(self):
        return tuple(self.owned)

    def restart(self, handle):
        if handle not in self.owned:
            raise PermissionError("handle not owned by this controller")
        self.restart_calls.append(handle)
        return self.restart_ok

    def verify_active(self, handle, config_hash):
        return handle in self.owned and self.verify_result


class ApplyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_parses_authoritative_command_registry(self):
        self.assertEqual(
            parse_cli_commands(V1_HELP),
            frozenset({"completion", "serve", "debug", "agent"}),
        )
        self.assertIn("reload", parse_cli_commands(RELOAD_HELP))

    def test_optional_real_binary_registry_smoke(self):
        """Portable smoke: parses a real binary when explicitly provided.

        Discovery is via SKILL_MANAGER_TEST_OPENCODE_BINARY or PATH; the test
        never asserts a permanently fixed version capability and skips cleanly.
        """
        import os
        import shutil
        import subprocess

        binary = os.environ.get("SKILL_MANAGER_TEST_OPENCODE_BINARY") or shutil.which(
            "opencode"
        )
        if not binary:
            self.skipTest("no OpenCode binary discovered")
        result = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, timeout=30,
            env={"HOME": "/tmp/opencode/probe-home", "PATH": "/usr/bin:/bin"},
        )
        commands = parse_cli_commands(result.stdout + result.stderr)
        # Only structural sanity: the registry parsed at all.
        self.assertIsInstance(commands, frozenset)
        self.assertTrue(all(isinstance(name, str) for name in commands))

    def test_detection_without_probe_reports_manual_restart(self):
        detector = OpenCodeCapabilityDetector(command_registry=None)
        capability = detector.detect()
        self.assertEqual(capability.mechanism, "restart-manual")
        self.assertFalse(capability.reload_available)
        self.assertFalse(capability.managed_runtime)

    def test_detection_prefers_reload_when_registry_has_command(self):
        detector = OpenCodeCapabilityDetector(command_registry=lambda: parse_cli_commands(RELOAD_HELP))
        capability = detector.detect()
        self.assertEqual(capability.mechanism, "reload")
        self.assertTrue(capability.reload_available)

    def test_detection_reports_manual_when_no_reload_and_unmanaged(self):
        detector = OpenCodeCapabilityDetector(command_registry=lambda: parse_cli_commands(V1_HELP))
        capability = detector.detect()
        self.assertEqual(capability.mechanism, "restart-manual")

    def test_detection_reports_managed_restart_only_for_owned_handle(self):
        controller = FakeController()
        registry = ManagedRuntimeRegistry()
        detector = OpenCodeCapabilityDetector(
            command_registry=lambda: parse_cli_commands(V1_HELP), managed_registry=registry,
        )
        self.assertEqual(detector.detect().mechanism, "restart-manual")
        registry.register(controller)
        controller.owned.append("handle-1")
        self.assertEqual(detector.detect().mechanism, "restart-managed")

    def test_state_store_roundtrip_and_atomicity(self):
        store = AgentApplyStateStore(self.root / "state" / "apply.json")
        self.assertFalse(store.status("/tmp/config.jsonc")["pending"])
        store.mark_saved("/tmp/config.jsonc", "hash-a")
        self.assertTrue(store.status("/tmp/config.jsonc")["pending"])
        store.mark_applied("/tmp/config.jsonc", "hash-a")
        self.assertFalse(store.status("/tmp/config.jsonc")["pending"])
        store.mark_saved("/tmp/config.jsonc", "hash-b")
        status = store.status("/tmp/config.jsonc")
        self.assertTrue(status["pending"])
        self.assertEqual(status["savedHash"], "hash-b")
        self.assertEqual(status["appliedHash"], "hash-a")
        # Reloaded store observes the same state (refresh-safe).
        self.assertEqual(
            AgentApplyStateStore(self.root / "state" / "apply.json").status("/tmp/config.jsonc"),
            status,
        )
        mode = stat.S_IMODE((self.root / "state" / "apply.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_registry_refuses_handles_from_other_controllers(self):
        owner = FakeController()
        owner.owned.append("owned-handle")
        other = FakeController()
        other.owned.append("other-handle")
        registry = ManagedRuntimeRegistry()
        registry.register(owner)
        with self.assertRaises(PermissionError):
            registry.restart("other-handle")


if __name__ == "__main__":
    unittest.main()


class AgentApplyStateFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state" / "apply.json"
        self.store = AgentApplyStateStore(self.path)

    def test_missing_file_is_legitimate_empty_state(self):
        self.assertEqual(self.store.all_targets(), {})
        self.assertFalse(self.store.pending_targets())

    def test_invalid_json_state_never_silently_becomes_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(AgentApplyStateError):
            self.store.all_targets()
        with self.assertRaises(AgentApplyStateError):
            self.store.status("/tmp/config.jsonc")

    def test_unreadable_existing_state_raises(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}", encoding="utf-8")
        os.chmod(self.path, 0o000)
        self.addCleanup(lambda: os.chmod(self.path, 0o600))
        if os.access(self.path, os.R_OK):  # Running as root ignores modes.
            self.skipTest("filesystem modes not enforced for this user")
        with self.assertRaises(AgentApplyStateError):
            self.store.pending_targets()

    def test_wrong_version_or_schema_raises(self):
        for payload in ('{"version": 2, "targets": {}}', '{"version": 1}',
                        '{"version": 1, "targets": []}', '"just a string"'):
            with self.subTest(payload=payload):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(payload, encoding="utf-8")
                with self.assertRaises(AgentApplyStateError):
                    self.store.all_targets()

    def test_write_failure_before_replace_leaves_prior_state_intact(self):
        self.store.mark_saved("/tmp/config.jsonc", "hash-1")
        before = self.path.read_bytes()
        mode_before = stat.S_IMODE(self.path.stat().st_mode)

        with patch("os.fchmod", side_effect=OSError("chmod failed")):
            with self.assertRaises(OSError):
                self.store.mark_saved("/tmp/config.jsonc", "hash-2")

        self.assertEqual(self.path.read_bytes(), before)  # Prior state untouched.
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), mode_before)
        self.assertEqual(
            self.store.status("/tmp/config.jsonc")["savedHash"], "hash-1"
        )
        # No temporary fragments remain behind.
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["apply.json"])

    def test_written_state_is_private_mode(self):
        self.store.mark_saved("/tmp/config.jsonc", "hash-1")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_error_messages_never_leak_state_content(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"version": 1, "targets": {"SECRET": "leak"}}',
                             encoding="utf-8")
        self.path.chmod(0o000)
        self.addCleanup(lambda: self.path.chmod(0o600))
        if os.access(self.path, os.R_OK):
            self.skipTest("filesystem modes not enforced for this user")
        try:
            self.store.all_targets()
        except AgentApplyStateError as error:
            self.assertNotIn("SECRET", str(error))
            self.assertNotIn("leak", str(error))
