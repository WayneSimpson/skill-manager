from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from skill_manager.application.skills.native_package_runtime import (
    NativePackageRuntimeConfig,
    NativePackageAdapterFactory,
    _BoundedNativeRunner,
    _explicit_executable,
)
from skill_manager.application.skills.native_package_cli import (
    NativeCLIError,
    OpenCodeNativePackageAdapter,
    ReadOnlyNativePackageAdapter,
)


class NativePackageRuntimeTests(unittest.TestCase):
    def test_codex_old_expected_version_is_not_a_gate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            factory = NativePackageAdapterFactory(Mock(), {
                'SKILL_MANAGER_NATIVE_CODEX_ROOT': str(root),
                'SKILL_MANAGER_NATIVE_CODEX_EXECUTABLE': '/usr/bin/ls',
                'SKILL_MANAGER_NATIVE_CODEX_ALLOW_MUTATION': 'true',
                'SKILL_MANAGER_NATIVE_CODEX_EXPECTED_VERSION': 'unrecognised-future-version',
            })
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                data = {'marketplaces': []} if 'marketplace' in argv else {'installed': [], 'available': []}
                return subprocess.CompletedProcess(argv, 0, json.dumps(data), '')
            with patch('skill_manager.application.skills.native_package_runtime.subprocess.run', side_effect=run):
                self.assertTrue(factory('codex').inspect('a'*64).mechanism_available)
            self.assertTrue(calls)
            self.assertFalse(any('--version' in argv for argv in calls))

    def test_runner_does_not_probe_or_enforce_native_version(self) -> None:
        config = NativePackageRuntimeConfig(
            "claude", Path("/tmp/claude-config"), Path("/usr/bin/ls")
        )
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with patch("skill_manager.application.skills.native_package_runtime.subprocess.run", side_effect=run):
            result = _BoundedNativeRunner(config)(["/usr/bin/ls", "plugin", "list", "--json"])

        self.assertEqual(result.stdout, "[]")
        self.assertEqual(calls, [["/usr/bin/ls", "plugin", "list", "--json"]])

    def test_runner_uses_bounded_environment_and_preserves_harness_config_binding(self) -> None:
        config_root = Path("/tmp/claude-config")
        config = NativePackageRuntimeConfig("claude", config_root, Path("/usr/bin/ls"))
        calls: list[dict] = []

        def run(command, **kwargs):
            calls.append({"command": command, **kwargs})
            self.assertFalse(kwargs["shell"])
            self.assertIs(subprocess.DEVNULL, kwargs["stdin"])
            self.assertNotEqual(kwargs["env"].get("HOME"), str(Path.home()))
            self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(config_root))
            self.assertTrue(Path(kwargs["cwd"]).is_dir())
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", kwargs["env"])
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with patch.dict("os.environ", {"AWS_SECRET_ACCESS_KEY": "must-not-leak"}, clear=False):
            with patch("skill_manager.application.skills.native_package_runtime.subprocess.run", side_effect=run):
                result = _BoundedNativeRunner(config)(["/usr/bin/ls", "plugin", "list", "--json"])

        self.assertEqual(result.stdout, "[]")
        self.assertEqual([item["command"] for item in calls], [["/usr/bin/ls", "plugin", "list", "--json"]])

    def test_runner_rejects_commands_that_do_not_start_with_configured_binary(self) -> None:
        config = NativePackageRuntimeConfig("codex", Path("/tmp/codex-config"), Path("/usr/bin/ls"))

        with self.assertRaisesRegex(NativeCLIError, "configured executable"):
            _BoundedNativeRunner(config)(["codex", "plugin", "list", "--json"])

    def test_explicit_executable_rejects_scripts_even_when_executable(self) -> None:
        with TemporaryDirectory() as directory:
            script = Path(directory) / "native"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)

            with self.assertRaisesRegex(ValueError, "raw ELF"):
                _explicit_executable(str(script))

    def test_factory_binds_only_a_verified_binary_and_explicit_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "claude"
            root.mkdir()
            factory = NativePackageAdapterFactory(
                Mock(),
                {
                    "SKILL_MANAGER_NATIVE_CLAUDE_ROOT": str(root),
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXECUTABLE": "/usr/bin/ls",
                    "SKILL_MANAGER_NATIVE_CLAUDE_ALLOW_MUTATION": "true",
                },
            )

            adapter = factory("claude")

        self.assertEqual(adapter.executable, "/usr/bin/ls")
        self.assertIsInstance(adapter.runner, _BoundedNativeRunner)
        self.assertEqual(adapter.root, root)

    def test_old_expected_version_and_unknown_native_version_do_not_block_plugin_list(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "claude"
            installed = root / "skills" / "external"
            installed.mkdir(parents=True)
            factory = NativePackageAdapterFactory(
                Mock(),
                {
                    "SKILL_MANAGER_NATIVE_CLAUDE_ROOT": str(root),
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXECUTABLE": "/usr/bin/ls",
                    "SKILL_MANAGER_NATIVE_CLAUDE_ALLOW_MUTATION": "true",
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXPECTED_VERSION": "0.0.0",
                },
            )
            calls: list[list[str]] = []

            def run(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, json.dumps([{
                    "id": "external@skills-dir",
                    "version": "unknown",
                    "scope": "user",
                    "enabled": True,
                    "installPath": str(installed),
                }]), "")

            with patch("skill_manager.application.skills.native_package_runtime.subprocess.run", side_effect=run):
                target = factory("claude").inspect("a" * 64)

        self.assertTrue(target.inventory_complete)
        self.assertTrue(target.mechanism_available)
        self.assertEqual(calls, [["/usr/bin/ls", "plugin", "list", "--json"]])

    def test_factory_binds_configured_opencode_adapter_without_version_gate(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "opencode"
            root.mkdir()
            factory = NativePackageAdapterFactory(
                Mock(),
                {
                    "SKILL_MANAGER_NATIVE_OPENCODE_ROOT": str(root),
                    "SKILL_MANAGER_NATIVE_OPENCODE_EXECUTABLE": "/usr/bin/ls",
                    "SKILL_MANAGER_NATIVE_OPENCODE_ALLOW_MUTATION": "true",
                },
            )

            adapter = factory("opencode")

        self.assertIsInstance(adapter, OpenCodeNativePackageAdapter)
        self.assertEqual(adapter.root, root)
        self.assertEqual(adapter.executable, "/usr/bin/ls")
        self.assertIsInstance(adapter.runner, _BoundedNativeRunner)

    def test_malformed_native_inventory_blocks_actual_adapter(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "claude"
            root.mkdir()
            factory = NativePackageAdapterFactory(
                Mock(),
                {
                    "SKILL_MANAGER_NATIVE_CLAUDE_ROOT": str(root),
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXECUTABLE": "/usr/bin/ls",
                    "SKILL_MANAGER_NATIVE_CLAUDE_ALLOW_MUTATION": "true",
                },
            )
            with patch(
                "skill_manager.application.skills.native_package_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, '{"unexpected":"shape"}', ""),
            ):
                adapter = factory("claude")
                target = adapter.inspect("a" * 64)

        self.assertFalse(target.inventory_complete)
        self.assertFalse(target.mechanism_available)
        self.assertIn("native-list-unavailable", adapter.diagnostics)

    def test_unavailable_native_mechanism_remains_read_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "claude"
            root.mkdir()
            factory = NativePackageAdapterFactory(
                Mock(),
                {
                    "SKILL_MANAGER_NATIVE_CLAUDE_ROOT": str(root),
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXECUTABLE": "/usr/bin/ls",
                },
            )
            adapter = factory("claude")

        self.assertIsInstance(adapter, ReadOnlyNativePackageAdapter)
        target = adapter.inspect("a" * 64)
        self.assertFalse(target.inventory_complete)
        self.assertFalse(target.mechanism_available)


if __name__ == "__main__":
    unittest.main()
