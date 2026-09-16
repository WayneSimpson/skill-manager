#!/usr/bin/env python3
"""Verify the real OpenCode whole-package lifecycle in an isolated sandbox.

This is deliberately a narrow Task09A probe.  It uses the production managed
package store, deployment service, planner, and OpenCode native adapter.  The
only hand-written config is the unrelated external fixture and the temporary
stale-conflict setup; installation and removal are performed by the service
and adapter through the real OpenCode CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.verify_native_package_sandbox as sandbox
from scripts.verify_native_package_sandbox import SandboxRunner, prepare, snapshot
from skill_manager.application.skills.managed_packages import ManagedPackageStore, package_fingerprint
from skill_manager.application.skills.native_package_cli import OpenCodeNativePackageAdapter
from skill_manager.application.skills.package_deployment import PackageDeploymentPlanner
from skill_manager.application.skills.package_deployment_service import (
    PackageDeploymentError,
    PackageDeploymentService,
)
from skill_manager.application.skills.package_resolution import (
    DistributionRelationship,
    PackageResolution,
    PackageSource,
)
from skill_manager.application.skills.source_package import SourcePackageDiscovery


NAME = "task09a-opencode-fixture"
SOURCE = "github:skill-manager-fixtures/task09a-opencode"
REVISION_V1 = "a" * 40
REVISION_V2 = "b" * 40
DEBUG_ARGS = ("debug", "info", "--print-logs", "--log-level", "DEBUG")
ENV_GUARDS = (
    "NPM_CONFIG_OFFLINE=true",
    "OPENCODE_DISABLE_MODELS_FETCH=true",
    "OPENCODE_DISABLE_AUTOUPDATE=true",
)


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def write_package(root: Path, version: str, revision: str) -> None:
    """Create an executable OpenCode package plus ordinary package content."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": NAME,
                "version": version,
                "type": "module",
                "exports": {"./server": "./index.js"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": NAME,
                "version": version,
                "description": "Task09A native OpenCode fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    resources = root / "resources"
    resources.mkdir()
    resource = f"fixture-resource-{version}-{revision[:8]}\n"
    (resources / "fixture-resource.txt").write_text(resource, encoding="utf-8")
    (root / "index.js").write_text(
        "export default {id: %s, server: async () => {"
        "const resource = await Bun.file(new URL(\"./resources/fixture-resource.txt\", import.meta.url)).text();"
        "await Bun.write(process.env.XDG_STATE_HOME + \"/task09a-loaded.json\", "
        "JSON.stringify({version: %s, resource})); return {};}};\n"
        % (json.dumps(NAME), json.dumps(version)),
        encoding="utf-8",
    )
    skills = root / "skills" / "fixture"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: task09a-fixture\ndescription: Task09A whole package fixture\n---\n"
        f"Return fixture-{version}.\n",
        encoding="utf-8",
    )


def write_external_fixture(root: Path) -> None:
    """Create an unrelated native package that must never be taken over."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "task09a-external-fixture",
                "version": "9.9.9",
                "type": "module",
                "exports": {"./server": "./index.js"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "index.js").write_text(
        "export default {id: \"task09a-external-fixture\", server: async () => {"
        "await Bun.write(process.env.XDG_STATE_HOME + \"/task09a-external-loaded\", \"external\");"
        "return {};}};\n",
        encoding="utf-8",
    )


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _file_bookend(roots: list[str]) -> dict[str, dict[str, dict[str, object]]]:
    """Read-only per-file attribution used only when the aggregate changes."""
    result: dict[str, dict[str, dict[str, object]]] = {}
    for raw_root in roots:
        root = Path(raw_root)
        if root.is_symlink():
            raise VerificationError(f"Snapshot root is a symlink: {root}")
        files: dict[str, dict[str, object]] = {}
        if root.exists():
            for parent, directories, names in os.walk(root):
                directories.sort()
                names.sort()
                for name in [*directories, *names]:
                    path = Path(parent) / name
                    info = path.lstat()
                    digest = hashlib.sha256()
                    if path.is_symlink():
                        digest.update(os.readlink(path).encode())
                    elif path.is_file():
                        with path.open("rb") as stream:
                            while chunk := stream.read(1024 * 1024):
                                digest.update(chunk)
                    files[str(path)] = {
                        "mode": info.st_mode,
                        "sha256": digest.hexdigest(),
                    }
        result[str(root)] = files
    return result


def _changed_files(before: dict, after: dict) -> list[str]:
    changed: list[str] = []
    for root in sorted(set(before) | set(after)):
        left, right = before.get(root, {}), after.get(root, {})
        for path in sorted(set(left) | set(right)):
            if left.get(path) != right.get(path):
                changed.append(path)
    return changed


def _restore_test_permissions(root: Path) -> None:
    """Restore only host-owned test directories; the runner handles UID-65534 files."""
    for path in (root, root / "config", root / "config" / "opencode"):
        if path.exists() and not path.is_symlink():
            path.chmod(0o777)


def _config_documents(root: Path) -> list[Path]:
    return [path for path in (root / "opencode.json", root / "opencode.jsonc") if path.exists()]


def _uri_occurrences(root: Path, uri: str) -> int:
    return sum(path.read_text(encoding="utf-8").count(uri) for path in _config_documents(root))


def _marker(root: Path) -> dict[str, str]:
    path = root / "state" / "task09a-loaded.json"
    require(path.is_file(), f"OpenCode server marker was not written: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(
        isinstance(value, dict)
        and isinstance(value.get("version"), str)
        and isinstance(value.get("resource"), str),
        "OpenCode server marker has an invalid shape",
    )
    return value


def verify_lifecycle(root: Path, runner: SandboxRunner, evidence: dict) -> None:
    config_root = root / "config" / "opencode"
    marker_path = root / "state" / "task09a-loaded.json"
    source_root = root / "source"
    source_v1 = source_root / "v1"
    source_v2 = source_root / "v2"
    write_package(source_v1, "1.0.0", REVISION_V1)
    write_package(source_v2, "2.0.0", REVISION_V2)

    packages = ManagedPackageStore(root / "managed")
    discovery = SourcePackageDiscovery()

    def adopt(source_root: Path, revision: str, version: str) -> dict:
        source = PackageSource("github", SOURCE, revision=revision, package_path=".")
        resolution = PackageResolution(
            "resolved",
            f"task09a-{version}",
            source=source,
            artifact_root=source_root,
            capabilities=discovery.inspect_root(source_root)["package"],
            distributions=(
                DistributionRelationship(
                    source,
                    "native OpenCode package distribution",
                    "Task09A pinned fixture declaration",
                    "opencode",
                ),
            ),
        )
        return packages.adopt(resolution, name=NAME, observations=[])

    record_v1 = adopt(source_v1, REVISION_V1, "1.0.0")
    record_v2 = adopt(source_v2, REVISION_V2, "2.0.0")
    central_manifest = packages.manifest.read_bytes()

    command_log: list[dict[str, object]] = []
    native_commands: list[list[str]] = []
    evidence["nativeCommands"] = command_log

    def native(argv: list[str] | tuple[str, ...], *, readonly_config: bool = False):
        command = [str(part) for part in argv]
        readonly_config = readonly_config or command[1:3] == ['debug', 'info']
        item: dict[str, object] = {"argv": command, "readonlyConfigRoot": readonly_config}
        native_commands.append(command)
        try:
            if readonly_config:
                _restore_test_permissions(root)
                config_root.chmod(0o555)
            result = runner(command)
            item.update(
                code=result.returncode,
                stdout=_text(result.stdout),
                stderr=_text(result.stderr),
            )
            return result
        except subprocess.TimeoutExpired as error:
            item.update(
                timeout=True,
                stdout=_text(error.stdout),
                stderr=_text(error.stderr),
            )
            raise
        finally:
            if readonly_config:
                config_root.chmod(0o777)
            runner.release()
            command_log.append(item)

    version_probe = native(["opencode", "--version"])
    evidence["version"] = _text(version_probe.stdout).strip()

    (config_root / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    before_debug = native(["opencode", *DEBUG_ARGS], readonly_config=True)
    require(before_debug.returncode == 0, "OpenCode preflight debug info failed")
    evidence["stages"] = {
        "preflight": {
            "debugInfo": _text(before_debug.stdout),
            "markerPresent": (root / "state" / "task09a-loaded.json").exists(),
        }
    }
    require(not (root / "state" / "task09a-loaded.json").exists(), "Preflight loaded the fixture unexpectedly")

    adapter = OpenCodeNativePackageAdapter(config_root, native, mechanism_available=True)
    require(not callable(getattr(adapter, "set_enabled", None)), "OpenCode adapter unexpectedly exposes set_enabled")
    planner = PackageDeploymentPlanner(packages)
    initial_target = adapter.inspect(record_v1["id"])
    initial_plan = planner.plan(record_v1["id"], initial_target)
    require(initial_plan.support == "supported", f"OpenCode plan is not supported: {initial_plan.blockers}")
    require(initial_plan.strategy == "native-install", "OpenCode plan did not select native-install")
    config_path = config_root / "opencode.json"
    expected_target = config_root / "skill-manager-packages" / record_v1["id"][:16]
    require(Path(initial_plan.surface["path"]) == expected_target, "Unexpected OpenCode package-copy path")

    adapter_calls = {"install": 0, "reinstall": 0, "uninstall": 0}
    for method_name in adapter_calls:
        original = getattr(adapter, method_name)

        def counted(plan, _original=original, _method_name=method_name):
            adapter_calls[_method_name] += 1
            return _original(plan)

        setattr(adapter, method_name, counted)

    external = config_root / "external-task09a"
    write_external_fixture(external)
    external_uri = external.as_uri()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"plugin": [external_uri]}) + "\n", encoding="utf-8")
    config_path.chmod(0o666)
    # One release stabilizes disposable-file permissions before the fixture bookend.
    version_probe = native(["opencode", "--version"])
    external_before = snapshot([str(external)])

    service = PackageDeploymentService(packages)
    _restore_test_permissions(root)
    deployed = service.deploy(record_v1["id"], adapter)
    deployment = deployed["deployment"]
    deployment_id = deployment["deploymentId"]
    target = Path(deployment["target"])
    target_uri = target.as_uri()
    require(target == expected_target, "Deployment target is not the planned whole-package copy")
    require(_uri_occurrences(config_root, target_uri) == 1, "Native registry does not contain one exact file URI")
    require(_uri_occurrences(config_root, external_uri) == 1, "External native registration was not preserved")
    managed_registry_paths = [
        path for path in _config_documents(config_root)
        if target_uri in path.read_text(encoding="utf-8")
    ]
    require(len(managed_registry_paths) == 1, "Managed URI is not attributable to one native registry document")
    registry_path = managed_registry_paths[0]
    install_commands = [
        command
        for command in native_commands
        if command[0:2] == ["opencode", "plugin"] and target_uri in command
    ]
    require(install_commands, "Service/adapter did not invoke the native OpenCode file-URI install")
    require(adapter_calls["install"] == 1, "Deployment service did not call the production adapter install")

    _restore_test_permissions(root)
    marker_path.unlink(missing_ok=True)
    debug_v1 = native(["opencode", *DEBUG_ARGS], readonly_config=True)
    require(debug_v1.returncode == 0, "OpenCode post-install debug info failed")
    marker_v1 = _marker(root)
    require(marker_v1["version"] == "1.0.0", "OpenCode did not load the v1 fixture module")
    require(marker_v1["resource"] == "fixture-resource-1.0.0-aaaaaaaa\n", "v1 resource was not read")
    require(target_uri in _text(debug_v1.stdout), "debug info did not report the installed exact URI")
    evidence["stages"].update(
        installed={
            "deploymentId": deployment_id,
            "target": str(target),
            "uri": target_uri,
            "registry": str(registry_path),
            "marker": marker_v1,
            "debugInfo": _text(debug_v1.stdout),
        }
    )

    target_before_repeat = package_fingerprint(target)
    registry_before_repeat = registry_path.read_bytes()
    repeated = service.deploy(record_v1["id"], adapter, deployment_id=deployment_id)
    require(repeated["changed"] is False, "Repeated deploy was not a verified no-op")
    require(Path(repeated["deployment"]["target"]) == target, "Repeated deploy changed the target path")
    require(package_fingerprint(target) == target_before_repeat, "Repeated deploy changed the whole copy")
    require(registry_path.read_bytes() == registry_before_repeat, "Repeated deploy changed the native registry")
    require(_uri_occurrences(config_root, target_uri) == 1, "Repeated deploy duplicated the exact URI")
    evidence["stages"]["repeat"] = {"changed": repeated["changed"], "uriCount": 1}

    _restore_test_permissions(root)
    marker_path.unlink()
    updated = service.update(deployment_id, record_v2["id"], adapter)
    updated_deployment = updated["deployment"]
    updated_target = Path(updated_deployment["target"])
    require(updated_target == target, "Update changed the stable whole-copy URI path")
    require(updated_target.as_uri() == target_uri, "Update changed the exact file URI")
    require(_uri_occurrences(config_root, target_uri) == 1, "Update did not retain one stable native URI")
    updated_registry_paths = [
        path for path in _config_documents(config_root)
        if target_uri in path.read_text(encoding="utf-8")
    ]
    require(len(updated_registry_paths) == 1, "Updated URI is not attributable to one native registry document")
    registry_path = updated_registry_paths[0]
    require((updated_target / "package.json").read_text(encoding="utf-8").find('"version": "2.0.0"') >= 0,
            "Update did not replace the whole package copy")
    require((updated_target / "resources" / "fixture-resource.txt").read_text(encoding="utf-8")
            == "fixture-resource-2.0.0-bbbbbbbb\n", "Update did not replace the resource")

    _restore_test_permissions(root)
    marker_path.unlink(missing_ok=True)
    debug_v2 = native(["opencode", *DEBUG_ARGS], readonly_config=True)
    require(debug_v2.returncode == 0, "OpenCode post-update debug info failed")
    marker_v2 = _marker(root)
    require(marker_v2["version"] == "2.0.0", "OpenCode did not load the v2 fixture after restart")
    require(marker_v2["resource"] == "fixture-resource-2.0.0-bbbbbbbb\n", "v2 resource was not read")
    require(target_uri in _text(debug_v2.stdout), "Updated debug info lost the stable exact URI")
    evidence["stages"]["updated"] = {
        "deploymentId": updated_deployment["deploymentId"],
        "target": str(updated_target),
        "uri": target_uri,
        "marker": marker_v2,
        "debugInfo": _text(debug_v2.stdout),
    }

    stale_registry = registry_path.read_bytes()
    stale_uri = (config_root / "stale-registration").as_uri()
    stale_text = stale_registry.decode("utf-8").replace(target_uri, stale_uri)
    require(target_uri not in stale_text and stale_uri in stale_text, "Could not create stale registry fixture")
    _restore_test_permissions(root)
    registry_path.write_text(stale_text, encoding="utf-8")
    native_count_before_stale = len(native_commands)
    try:
        try:
            service.deploy(record_v2["id"], adapter, deployment_id=deployment_id)
        except PackageDeploymentError:
            pass
        else:
            raise VerificationError("Stale native registry was accepted for a managed deployment")
        stale_commands = native_commands[native_count_before_stale:]
        require(
            not any(command[0:2] == ["opencode", "plugin"] and '--help' not in command for command in stale_commands),
            "Stale conflict reached a native mutation command",
        )
        require(package_fingerprint(target) == updated_deployment["targetFingerprint"],
                "Stale conflict changed the managed whole copy")
    finally:
        registry_path.write_bytes(stale_registry)
    evidence["stages"]["staleConflict"] = {"refused": True, "nativeMutationAttempted": False}

    _restore_test_permissions(root)
    marker_path.unlink()
    removed = service.remove(deployment_id, adapter)
    require(removed["changed"] is True, "Native removal was not performed")
    require(not target.exists(), "Managed OpenCode whole copy remained after removal")
    require(_uri_occurrences(config_root, target_uri) == 0, "Native registry retained the managed URI after removal")
    require(_uri_occurrences(config_root, external_uri) == 1, "Native removal damaged the external registration")
    remove_repeat = service.remove(deployment_id, adapter)
    require(remove_repeat["changed"] is False, "Repeated native removal was not a verified no-op")
    require(adapter_calls["uninstall"] == 1, "Removal service did not call the production adapter uninstall")

    # The adapter's pre-remove verification legitimately loads v2; clear that
    # marker so this fresh post-remove process proves absence rather than stale state.
    _restore_test_permissions(root)
    marker_path.unlink(missing_ok=True)
    debug_removed = native(["opencode", *DEBUG_ARGS], readonly_config=True)
    require(debug_removed.returncode == 0, "OpenCode post-removal debug info failed")
    require(target_uri not in _text(debug_removed.stdout), "Removed URI reappeared after native restart")
    require(not marker_path.exists(), "Removed fixture module loaded after native restart")
    require(external_before == snapshot([str(external)]), "External fixture files changed")
    require(packages.manifest.read_bytes() == central_manifest, "Central managed-package manifest changed")
    retained = [packages.get(record["id"]) for record in (record_v1, record_v2)]
    require(all(record["artifactState"] == "current" for record in retained),
            "Central managed package artifacts were not retained")
    require(service.list_deployments() == {}, "Deployment state retained an active deployment after removal")
    evidence["stages"]["removed"] = {
        "changed": removed["changed"],
        "repeatChanged": remove_repeat["changed"],
        "debugInfo": _text(debug_removed.stdout),
        "markerPresent": marker_path.exists(),
        "externalPreserved": True,
        "centralPackagesRetained": len(retained),
    }
    evidence["checks"] = {
        "usedProductionService": type(service).__name__,
        "usedProductionAdapter": type(adapter).__name__,
        "adapterCalls": adapter_calls,
        "setEnabled": False,
        "wholePackageCopy": str(target),
        "registry": str(registry_path),
        "exactFileURI": target_uri,
        "externalFixtureUnchanged": True,
        "staleConflictRefused": True,
        "centralPackagesRetained": True,
    }
    evidence["limitations"] = [
        "This proves a pinned local file-URI package lifecycle in the real OpenCode CLI; it does not claim registry/network bootstrap works.",
        "The version output is diagnostic evidence only; no hard version gate is applied.",
        "No model call, host configuration, credential, cache, or network access is used; native metadata probes use only the disposable config root.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--opencode", required=True)
    parser.add_argument("--snapshot-root", action="append", required=True)
    args = parser.parse_args()
    require(len(args.snapshot_root) == 3, "Exactly three --snapshot-root values are required")

    before = snapshot(args.snapshot_root)
    file_before = _file_bookend(args.snapshot_root)
    evidence: dict[str, object] = {
        "result": "FAIL",
        "harness": "opencode",
        "image": args.image,
        "binary": args.opencode,
        "sandboxPolicy": {
            "network": "none",
            "uid": "65534:65534",
            "rawELFReadOnlyBinary": True,
            "mounts": ["fresh disposable test root", "raw ELF OpenCode binary (read-only)"],
            "hostConfigCredentialCacheMounts": [],
            "nativeCommandTimeoutSeconds": 45,
            "permissionRestoreTimeoutSeconds": 20,
            "envGuards": list(ENV_GUARDS),
            "modelCalls": False,
        },
        "snapshotRoots": list(args.snapshot_root),
    }
    failure: BaseException | None = None
    try:
        with TemporaryDirectory(prefix="skill-manager-native-test-task09a-") as directory:
            root = Path(directory)
            prepare(root)
            runner = SandboxRunner(args.image, {"opencode": args.opencode}, root)
            evidence["sandboxRoot"] = str(root)
            real_run = sandbox.subprocess.run

            def guarded_run(command, **kwargs):
                command = list(command)
                if "/opt/opencode" in command:
                    index = command.index("/opt/opencode")
                    command[index:index] = list(ENV_GUARDS)
                return real_run(command, **kwargs)

            try:
                with patch("scripts.verify_native_package_sandbox.subprocess.run", side_effect=guarded_run):
                    try:
                        verify_lifecycle(root, runner, evidence)
                        evidence["result"] = "PASS"
                    except BaseException as error:  # preserve proof output on failure
                        failure = error
                        evidence["error"] = f"{type(error).__name__}: {error}"
            finally:
                _restore_test_permissions(root)
                runner.release()
    except BaseException as error:
        failure = failure or error
        evidence["result"] = "FAIL"
        evidence["error"] = f"{type(error).__name__}: {error}"
    finally:
        after = snapshot(args.snapshot_root)
        evidence["before"] = before
        evidence["after"] = after
        evidence["realOpenCodeUnchanged"] = before == after
        if before != after:
            file_after = _file_bookend(args.snapshot_root)
            evidence["realOpenCodeChangedFiles"] = _changed_files(file_before, file_after)
            evidence["realOpenCodeAttributionNote"] = (
                "Per-file attribution is captured only for an aggregate mismatch; inspect models.json entries if present."
            )
    if before != after:
        failure = failure or VerificationError("Real OpenCode snapshot roots changed")
        evidence["result"] = "FAIL"
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
