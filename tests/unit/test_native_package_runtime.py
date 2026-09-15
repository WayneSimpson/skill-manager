from __future__ import annotations

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
    _parse_verified_version,
)
from skill_manager.application.skills.native_package_cli import NativeCLIError


class NativePackageRuntimeTests(unittest.TestCase):
    def test_version_parser_requires_an_exact_verified_version_line(self) -> None:
        self.assertEqual(_parse_verified_version("claude", "2.1.269 (Claude Code)"), "2.1.269")
        self.assertEqual(_parse_verified_version("codex", "codex-cli 0.154.0"), "0.154.0")
        self.assertIsNone(_parse_verified_version("claude", "Claude Code v2.1.269 extra"))
        self.assertIsNone(_parse_verified_version("codex", "codex-cli 0.154.000"))
        self.assertIsNone(_parse_verified_version("claude", "unknown 2.1.269"))

    def test_runner_probes_version_and_rejects_a_mismatched_policy(self) -> None:
        config = NativePackageRuntimeConfig(
            "claude", Path("/tmp/claude-config"), Path("/usr/bin/ls"), expected_version="2.1.270"
        )
        result = subprocess.CompletedProcess(["/usr/bin/ls", "--version"], 0, "2.1.269 (Claude Code)", "")

        with patch("skill_manager.application.skills.native_package_runtime.subprocess.run", return_value=result):
            with self.assertRaisesRegex(NativeCLIError, "does not match configured policy"):
                _BoundedNativeRunner(config)(["/usr/bin/ls", "plugin", "list", "--json"])

    def test_runner_uses_bounded_environment_and_preserves_harness_config_binding(self) -> None:
        config_root = Path("/tmp/claude-config")
        config = NativePackageRuntimeConfig("claude", config_root, Path("/usr/bin/ls"), expected_version="2.1.269")
        calls: list[dict] = []

        def run(command, **kwargs):
            calls.append({"command": command, **kwargs})
            self.assertFalse(kwargs["shell"])
            self.assertIs(subprocess.DEVNULL, kwargs["stdin"])
            self.assertNotEqual(kwargs["env"].get("HOME"), str(Path.home()))
            self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(config_root))
            self.assertTrue(Path(kwargs["cwd"]).is_dir())
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", kwargs["env"])
            output = "2.1.269 (Claude Code)" if command[-1] == "--version" else "[]"
            return subprocess.CompletedProcess(command, 0, output, "")

        with patch.dict("os.environ", {"AWS_SECRET_ACCESS_KEY": "must-not-leak"}, clear=False):
            with patch("skill_manager.application.skills.native_package_runtime.subprocess.run", side_effect=run):
                result = _BoundedNativeRunner(config)(["/usr/bin/ls", "plugin", "list", "--json"])

        self.assertEqual(result.stdout, "[]")
        self.assertEqual([item["command"] for item in calls], [
            ["/usr/bin/ls", "--version"],
            ["/usr/bin/ls", "plugin", "list", "--json"],
        ])

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
                    "SKILL_MANAGER_NATIVE_CLAUDE_EXPECTED_VERSION": "2.1.269",
                },
            )

            adapter = factory("claude")

        self.assertEqual(adapter.executable, "/usr/bin/ls")
        self.assertIsInstance(adapter.runner, _BoundedNativeRunner)
        self.assertEqual(adapter.root, root)


if __name__ == "__main__":
    unittest.main()
