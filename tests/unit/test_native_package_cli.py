from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import stat

from skill_manager.application.skills.package_deployment import PackageDeploymentPlan
from skill_manager.application.skills.native_package_cli import (
    ClaudeNativePackageAdapter,
    CodexNativePackageAdapter,
    NativeCLIError,
)


PACKAGE_ID = "a" * 64


def completed(argv: list[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")


def claude_plan(root: Path, *, native_id: str = "example@skills-dir") -> PackageDeploymentPlan:
    plan = PackageDeploymentPlan(PACKAGE_ID, "claude", {})
    plan.strategy = "native-local"
    plan.surface = {
        "path": str(root / "skills" / f"skill-manager-{PACKAGE_ID[:16]}"),
        "nativeId": native_id,
        "scope": "user",
    }
    return plan


def codex_plan(root: Path, *, native_id: str = "example@skill-manager-aaaaaaaaaaaaaaaa") -> PackageDeploymentPlan:
    marketplace = "skill-manager-" + PACKAGE_ID[:16]
    marketplace_root = root / "skill-manager-marketplaces" / marketplace
    plan = PackageDeploymentPlan(PACKAGE_ID, "codex", {})
    plan.strategy = "native-install"
    plan.support = 'supported'
    plan.actions = [{'action': 'native-install-whole-package', 'unit': 'whole-package'}]
    plan.surface = {
        "path": str(marketplace_root),
        "nativeId": native_id,
        "marketplaceId": marketplace,
        "marketplacePath": str(marketplace_root / ".agents/plugins/marketplace.json"),
        "wholePackagePath": str(marketplace_root / "plugins" / PACKAGE_ID[:16]),
        "marketplaceDocument": {
            "name": marketplace,
            "plugins": [{
                "name": native_id.split("@", 1)[0],
                "source": {"source": "local", "path": "./plugins/" + PACKAGE_ID[:16]},
            }],
        },
        "marketplaceArgv": ["codex", "plugin", "marketplace", "add", str(marketplace_root), '--json'],
        "installArgv": ["codex", "plugin", "add", native_id, "--json"],
    }
    return plan


def markets(plan, present=True):
    return {'marketplaces': [{'name': plan.surface['marketplaceId'], 'root': plan.surface['path'],
        'marketplaceSource': {'sourceType': 'local', 'source': plan.surface['path']}}] if present else []}


class NativePackageCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "native"

    def test_adapters_require_an_explicit_runner_and_do_not_use_subprocess(self) -> None:
        with self.assertRaises(TypeError):
            ClaudeNativePackageAdapter(self.root)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            CodexNativePackageAdapter(self.root)  # type: ignore[call-arg]

    def test_default_unverified_mechanism_does_not_invoke_runner(self):
        def forbidden(argv):
            raise AssertionError('No native call allowed')
        for cls in (ClaudeNativePackageAdapter, CodexNativePackageAdapter):
            target = cls(self.root, forbidden).inspect(PACKAGE_ID)
            self.assertFalse(target.mechanism_available)

    def test_codex_toggle_preserves_unrelated_bytes_and_file_mode(self):
        self.root.mkdir()
        plan = codex_plan(self.root)
        plan.ownership = 'managed'
        path = self.root / 'config.toml'
        original = ('# external settings\r\n[plugins."external@other"]\r\nenabled = true\r\n'
                    '[plugins."' + plan.surface['nativeId'] + '"]\r\nenabled = true # owned\r\n')
        path.write_bytes(original.encode())
        path.chmod(0o640)
        adapter = CodexNativePackageAdapter(self.root, lambda _: None, mechanism_available=True)
        with patch.object(adapter, 'verify', return_value={'verified': True, 'enabled': True}):
            adapter.set_enabled(plan, False)
        self.assertEqual(path.read_bytes(), original.replace('true # owned', 'false # owned').encode())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_declared_git_pin_becomes_exact_native_source_evidence(self):
        plan = codex_plan(self.root)
        cache = self.root / 'plugins/cache' / plan.surface['marketplaceId'] / 'example/1.0.0'
        cache.mkdir(parents=True)
        (cache / 'plugin.json').write_text('{"name":"example"}')
        entry = {'pluginId':plan.surface['nativeId'], 'name':'example',
            'marketplaceName':plan.surface['marketplaceId'], 'version':'1.0.0', 'installed':True,
            'enabled':True, 'installPolicy':'AVAILABLE', 'authPolicy':'ON_INSTALL',
            'source':{'source':'git', 'url':'https://github.com/example/plugin.git', 'sha':'a'*40, 'path':'packages/native'}}
        adapter = CodexNativePackageAdapter(self.root, lambda argv: completed(argv, json.dumps(
            markets(plan) if 'marketplace' in argv else {'installed':[entry], 'available':[]})), mechanism_available=True)
        registration = adapter.inspect(PACKAGE_ID).registrations[0]
        self.assertEqual(registration.evidence, 'native-source')
        self.assertEqual(registration.source.locator, 'github:example/plugin')
        self.assertEqual(registration.source.revision, 'a'*40)
        self.assertEqual(registration.source.package_path, 'packages/native')

    def test_claude_empty_verified_list_is_complete_and_uses_exact_argv(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str]):
            calls.append(argv)
            return completed(argv, "[]")

        target = ClaudeNativePackageAdapter(self.root, runner, mechanism_available=True).inspect(PACKAGE_ID)

        self.assertTrue(target.inventory_complete)
        self.assertTrue(target.mechanism_available)
        self.assertEqual(target.registrations, ())
        self.assertEqual(calls, [["claude", "plugin", "list", "--json"]])

    def test_claude_malformed_list_is_incomplete_and_manual(self) -> None:
        adapter = ClaudeNativePackageAdapter(
            self.root,
            lambda argv: completed(argv, '{"unexpected":"shape"}'),
            mechanism_available=True,
        )

        target = adapter.inspect(PACKAGE_ID)

        self.assertFalse(target.inventory_complete)
        self.assertFalse(target.mechanism_available)
        self.assertIn("native-list-unavailable", adapter.diagnostics)

    def test_claude_existing_owned_directory_without_list_identity_is_a_blocker(self) -> None:
        destination = self.root / "skills" / f"skill-manager-{PACKAGE_ID[:16]}"
        (destination / ".claude-plugin").mkdir(parents=True)
        (destination / ".claude-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")

        adapter = ClaudeNativePackageAdapter(self.root, lambda argv: completed(argv, "[]"), mechanism_available=True)
        target = adapter.inspect(PACKAGE_ID)

        self.assertFalse(target.inventory_complete)
        self.assertFalse(target.mechanism_available)
        self.assertIn("native-list-omits-owned-skills-dir", adapter.last_diagnostics)
        self.assertEqual(target.registrations, ())

    def test_claude_name_and_manifest_without_native_path_are_not_proof(self) -> None:
        destination = self.root / "skills" / f"skill-manager-{PACKAGE_ID[:16]}"
        (destination / ".claude-plugin").mkdir(parents=True)
        (destination / ".claude-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        adapter = ClaudeNativePackageAdapter(
            self.root,
            lambda argv: completed(argv, json.dumps([{
                "id": "example@skills-dir", "version": "1.0.0",
                "scope": "user", "enabled": True,
            }])),
            mechanism_available=True,
        )

        target = adapter.inspect(PACKAGE_ID)

        self.assertFalse(target.inventory_complete)
        self.assertFalse(target.mechanism_available)
        self.assertIsNone(target.registrations[0].root)

    def test_claude_verify_requires_identity_path_and_manifest_not_exit_code(self) -> None:
        plan = claude_plan(self.root)
        destination = Path(plan.surface["path"])
        (destination / ".claude-plugin").mkdir(parents=True)
        (destination / ".claude-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")

        def runner(argv: list[str]):
            return completed(argv, json.dumps([{
                "id": "example@skills-dir",
                "version": "1.0.0",
                "scope": "user",
                "enabled": True,
                "installPath": str(destination),
            }]))

        adapter = ClaudeNativePackageAdapter(self.root, runner, mechanism_available=True)
        self.assertEqual(adapter.verify(plan, "present"), {
            "verified": True,
            "enabled": True,
            "nativeId": "example@skills-dir",
            "path": str(destination),
        })

        mismatched = replace(plan, surface={**plan.surface, "nativeId": "other@skills-dir"})
        self.assertFalse(adapter.verify(mismatched, "present")["verified"])

    def test_claude_set_enabled_verifies_before_and_after_native_command(self) -> None:
        plan = claude_plan(self.root)
        destination = Path(plan.surface["path"])
        (destination / ".claude-plugin").mkdir(parents=True)
        (destination / ".claude-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        enabled = True
        calls: list[list[str]] = []

        def runner(argv: list[str]):
            nonlocal enabled
            calls.append(argv)
            if argv[:3] == ["claude", "plugin", "list"]:
                return completed(argv, json.dumps([{
                    "id": "example@skills-dir", "version": "1.0.0", "scope": "user", "enabled": enabled,
                    "installPath": str(destination),
                }]))
            enabled = argv[2] == "enable"
            return completed(argv)

        plan.support = 'supported'
        plan.ownership = 'managed'
        plan.actions = [{'action': 'reconcile-whole-package', 'unit': 'whole-package'}]
        result = ClaudeNativePackageAdapter(self.root, runner, mechanism_available=True).set_enabled(plan, False)

        self.assertTrue(result["verified"])
        self.assertFalse(result["enabled"])
        self.assertEqual(calls, [
            ["claude", "plugin", "list", "--json"],
            ["claude", "plugin", "disable", "example@skills-dir"],
            ["claude", "plugin", "list", "--json"],
        ])

    def test_codex_inspect_requires_live_cache_and_declared_source(self) -> None:
        plan = codex_plan(self.root)
        cache = self.root / "plugins/cache" / plan.surface["marketplaceId"] / "example" / "1.0.0"
        cache.mkdir(parents=True)
        (cache / "plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        declared = Path(plan.surface['wholePackagePath'])
        declared.mkdir(parents=True)
        (declared / 'plugin.json').write_text('{"name":"example"}')
        payload = {
            "installed": [{
                "pluginId": plan.surface["nativeId"],
                "name": "example",
                "marketplaceName": plan.surface["marketplaceId"],
                "version": "1.0.0",
                "installed": True,
                "enabled": True,
                "source": {"source": "local", "path": str(Path(plan.surface["wholePackagePath"]))},
                "installPolicy": "AVAILABLE",
                "authPolicy": "ON_INSTALL",
            }],
            "available": [],
        }

        adapter = CodexNativePackageAdapter(self.root, lambda argv: completed(argv, json.dumps(markets(plan) if 'marketplace' in argv else payload)), mechanism_available=True)
        target = adapter.inspect(PACKAGE_ID)

        self.assertTrue(target.inventory_complete)
        self.assertTrue(target.mechanism_available)
        self.assertEqual(target.occupied_identifiers, (plan.surface["nativeId"], plan.surface["marketplaceId"]))
        self.assertEqual(len(target.registrations), 1)
        self.assertEqual(target.registrations[0].root, declared)
        self.assertIsNone(target.registrations[0].deployment_id)

    def test_codex_verify_requires_marketplace_source_and_real_cache_provenance(self) -> None:
        plan = codex_plan(self.root)
        marketplace_root = Path(plan.surface["path"])
        package_root = Path(plan.surface["wholePackagePath"])
        (package_root / ".codex-plugin").mkdir(parents=True)
        (package_root / ".codex-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        catalog = Path(plan.surface["marketplacePath"])
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps(plan.surface["marketplaceDocument"]), encoding="utf-8")
        cache = self.root / "plugins/cache" / plan.surface["marketplaceId"] / "example" / "1.0.0"
        cache.mkdir(parents=True)
        (cache / "plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        payload = {
            "installed": [{
                "pluginId": plan.surface["nativeId"], "name": "example",
                "marketplaceName": plan.surface["marketplaceId"], "version": "1.0.0",
                "installed": True, "enabled": False,
                "source": {"source": "local", "path": str(package_root)},
                "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL",
            }],
            "available": [],
        }

        adapter = CodexNativePackageAdapter(self.root, lambda argv: completed(argv, json.dumps(markets(plan) if 'marketplace' in argv else payload)), mechanism_available=True)
        verified = adapter.verify(plan, "present")

        self.assertTrue(verified["verified"])
        self.assertFalse(verified["enabled"])
        self.assertEqual(verified["path"], str(cache))

        (cache / "plugin.json").write_text('{"name":"different"}', encoding="utf-8")
        self.assertFalse(adapter.verify(plan, "present")["verified"])

    def test_codex_install_uses_task06_argv_without_reconstructing_the_marketplace(self) -> None:
        plan = codex_plan(self.root)
        package_root = Path(plan.surface["wholePackagePath"])
        (package_root / ".codex-plugin").mkdir(parents=True)
        (package_root / ".codex-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        catalog = Path(plan.surface["marketplacePath"])
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps(plan.surface["marketplaceDocument"]), encoding="utf-8")
        calls: list[list[str]] = []
        installed = False
        marketplace_present = False

        def runner(argv: list[str]):
            nonlocal installed, marketplace_present
            calls.append(argv)
            if argv == ['codex', 'plugin', 'marketplace', 'list', '--json']:
                return completed(argv, json.dumps(markets(plan, marketplace_present)))
            if argv == plan.surface['marketplaceArgv']:
                marketplace_present = True
                return completed(argv, json.dumps({'alreadyAdded':False, 'marketplaceName':plan.surface['marketplaceId'], 'installedRoot':plan.surface['path']}))
            if argv == ["codex", "plugin", "list", "--json"]:
                entry = {
                    "pluginId": plan.surface["nativeId"], "name": "example",
                    "marketplaceName": plan.surface["marketplaceId"], "version": "1.0.0",
                    "installed": installed,
                    "enabled": True,
                    "source": {"source": "local", "path": str(package_root)},
                    "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL",
                }
                return completed(argv, json.dumps({"installed": [entry] if installed else [], "available": []}))
            if argv == plan.surface["installArgv"]:
                installed = True
                cache = self.root / "plugins/cache" / plan.surface["marketplaceId"] / "example" / "1.0.0"
                cache.mkdir(parents=True)
                (cache / "plugin.json").write_text('{"name":"example"}', encoding="utf-8")
            return completed(argv, "{}")

        result = CodexNativePackageAdapter(self.root, runner, mechanism_available=True).install(plan)

        self.assertTrue(result["verified"])
        self.assertEqual(calls[0], ["codex", "plugin", "list", "--json"])
        self.assertEqual([c for c in calls if 'list' not in c], [plan.surface["marketplaceArgv"], plan.surface["installArgv"]])

    def test_codex_rejects_path_segments_in_native_identity_before_mutation(self) -> None:
        plan = codex_plan(self.root, native_id="../escape@skill-manager-aaaaaaaaaaaaaaaa")
        package_root = Path(plan.surface["wholePackagePath"])
        (package_root / ".codex-plugin").mkdir(parents=True)
        (package_root / ".codex-plugin/plugin.json").write_text('{"name":"escape"}', encoding="utf-8")
        catalog = Path(plan.surface["marketplacePath"])
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps(plan.surface["marketplaceDocument"]), encoding="utf-8")
        calls: list[list[str]] = []

        def runner(argv: list[str]):
            calls.append(argv)
            return completed(argv, json.dumps({"installed": [], "available": []}))

        with self.assertRaises(ValueError):
            CodexNativePackageAdapter(self.root, runner, mechanism_available=True).install(plan)

        self.assertEqual(calls, [])

    def test_codex_uninstall_removes_only_owned_plugin_and_marketplace(self) -> None:
        plan = codex_plan(self.root)
        plan.ownership = 'managed'
        package_root = Path(plan.surface["wholePackagePath"])
        (package_root / ".codex-plugin").mkdir(parents=True)
        (package_root / ".codex-plugin/plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        catalog = Path(plan.surface["marketplacePath"])
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps(plan.surface["marketplaceDocument"]), encoding="utf-8")
        cache = self.root / "plugins/cache" / plan.surface["marketplaceId"] / "example" / "1.0.0"
        cache.mkdir(parents=True)
        (cache / "plugin.json").write_text('{"name":"example"}', encoding="utf-8")
        calls: list[list[str]] = []
        installed = True
        marketplace_present = True

        def runner(argv: list[str]):
            nonlocal installed, marketplace_present
            calls.append(argv)
            if argv == ['codex', 'plugin', 'marketplace', 'list', '--json']:
                return completed(argv, json.dumps(markets(plan, marketplace_present)))
            if argv == ["codex", "plugin", "list", "--json"]:
                entry = {
                    "pluginId": plan.surface["nativeId"], "name": "example",
                    "marketplaceName": plan.surface["marketplaceId"], "version": "1.0.0",
                    "installed": installed,
                    "enabled": True,
                    "source": {"source": "local", "path": str(package_root)},
                    "installPolicy": "AVAILABLE", "authPolicy": "ON_INSTALL",
                }
                return completed(argv, json.dumps({
                    "installed": [entry] if installed else [],
                    "available": [entry] if marketplace_present and not installed else [],
                }))
            if argv[1:3] == ["plugin", "remove"]:
                installed = False
                cache_root = cache.parent.parent
                if cache_root.exists():
                    shutil.rmtree(cache_root)
            elif argv[1:4] == ["plugin", "marketplace", "remove"]:
                marketplace_present = False
            return completed(argv, "{}")

        result = CodexNativePackageAdapter(self.root, runner, mechanism_available=True).uninstall(plan)

        self.assertTrue(result["verified"])
        self.assertEqual([c for c in calls if 'list' not in c], [
            ["codex", "plugin", "remove", plan.surface["nativeId"], "--json"],
            ["codex", "plugin", "marketplace", "remove", plan.surface["marketplaceId"]]])
        self.assertTrue(Path(plan.surface['path']).exists(), 'Native remove does not delete the owned local source')

    def test_codex_enable_disable_requires_owned_verified_plan(self) -> None:
        adapter = CodexNativePackageAdapter(self.root, lambda argv: completed(argv))
        with self.assertRaises(NativeCLIError):
            adapter.set_enabled(codex_plan(self.root), True)


if __name__ == "__main__":
    unittest.main()
