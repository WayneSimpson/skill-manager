"""Bounded CLI adapters for native whole-package deployments.

The adapters deliberately do not provide a process runner.  ``runner`` is a
required dependency and receives the complete argv sequence, including the
executable name.  It must execute that argv in an explicitly supplied,
isolated context and return either ``subprocess.CompletedProcess`` (with text
or byte stdout), a JSON stdout string/bytes value, or the already-decoded JSON
object.  The adapter never reads the process environment and never calls a
process API itself.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import hashlib
from pathlib import Path
import re
import subprocess
import tomllib
import os
import tempfile
import stat
from copy import deepcopy
from dataclasses import replace
from typing import Any, TypeAlias

from .package_deployment import (
    NativeRegistration,
    NativeTarget,
    PackageDeploymentPlan,
    _object_without_duplicate_keys,
    read_opencode_registrations,
)
from .managed_packages import package_fingerprint
from .package_resolution import PackageSource, _repository, _npm
from skill_manager.atomic_files import atomic_write_text
from skill_manager.jsonc import strip_jsonc


NativeCLIJSON: TypeAlias = Mapping[str, Any] | list[Any]
NativeCLIResult: TypeAlias = subprocess.CompletedProcess[str] | str | bytes | NativeCLIJSON
NativeCLIRunner: TypeAlias = Callable[[Sequence[str]], NativeCLIResult]

_MAX_JSON_BYTES = 4 * 1024 * 1024
_CLAUDE_SCOPES = frozenset({"user", "project", "local", "managed"})
_CODEX_INSTALL_POLICIES = frozenset({"NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"})
_CODEX_AUTH_POLICIES = frozenset({"ON_INSTALL", "ON_USE"})
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*\Z")
_PACKAGE_ID = re.compile(r"[0-9a-f]{64}\Z")


class NativeCLIError(RuntimeError):
    """A native CLI could not be safely invoked or its result was unusable."""


class ReadOnlyNativePackageAdapter:
    """Default for runtimes whose native mutation mechanism is not configured."""
    def __init__(self, harness, root):
        self.harness = harness
        self.root = _explicit_root(root)

    def inspect(self, package_id, deployment=None):
        registrations = ()
        if self.harness == 'opencode':
            registrations = read_opencode_registrations(tuple(self.root / name for name in ('opencode.json', 'opencode.jsonc')))
        return NativeTarget(self.harness, self.root, registrations, mechanism_available=False)

    def verify(self, plan, expected):
        return {'verified': False, 'enabled': None}


class _NativePackageCLIAdapter:
    harness: str
    executable: str

    def __init__(self, root: Path | str, runner: NativeCLIRunner, *, mechanism_available: bool = False) -> None:
        if not callable(runner):
            raise TypeError("an explicit native CLI runner is required")
        self.root = _explicit_root(root)
        self.runner = runner
        self.mechanism_available = mechanism_available is True
        self.last_diagnostics: tuple[str, ...] = ()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        """Diagnostics from the most recent inspection or verification."""
        return self.last_diagnostics

    def _set_diagnostics(self, *values: str) -> None:
        self.last_diagnostics = tuple(dict.fromkeys(values))

    def _run_json(self, argv: Sequence[str]) -> NativeCLIJSON:
        value = self._run(argv, expect_json=True)
        if not isinstance(value, (Mapping, list)):
            raise NativeCLIError("native CLI returned a JSON scalar")
        return value

    def _run(self, argv: Sequence[str], *, expect_json: bool = False) -> NativeCLIResult:
        command = [str(part) for part in argv]
        try:
            result = self.runner(command)
        except Exception as error:  # noqa: BLE001 - injected process boundary
            raise NativeCLIError(f"native CLI runner failed: {error}") from error

        if isinstance(result, subprocess.CompletedProcess):
            if result.returncode != 0:
                raise NativeCLIError(
                    f"native CLI command failed with exit code {result.returncode}"
                )
            output = result.stdout
            if isinstance(output, bytes):
                try:
                    output = output.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise NativeCLIError("native CLI stdout is not UTF-8") from error
            if output is None:
                output = ""
            if not isinstance(output, str):
                raise NativeCLIError("native CLI stdout has an unsupported type")
            if expect_json:
                if len(output.encode("utf-8")) > _MAX_JSON_BYTES:
                    raise NativeCLIError("native CLI JSON output is too large")
                try:
                    return json.loads(output)
                except json.JSONDecodeError as error:
                    raise NativeCLIError("native CLI returned malformed JSON") from error
            return output

        if isinstance(result, (Mapping, list)):
            return result
        if isinstance(result, bytes):
            try:
                result = result.decode("utf-8")
            except UnicodeDecodeError as error:
                raise NativeCLIError("native CLI stdout is not UTF-8") from error
        if isinstance(result, str):
            if expect_json:
                if len(result.encode("utf-8")) > _MAX_JSON_BYTES:
                    raise NativeCLIError("native CLI JSON output is too large")
                try:
                    return json.loads(result)
                except json.JSONDecodeError as error:
                    raise NativeCLIError("native CLI returned malformed JSON") from error
            return result
        raise NativeCLIError("native CLI runner returned an unsupported result")

    def _run_command(self, argv: Sequence[str]) -> None:
        self._run(argv)


class _ClaudeEntry:
    def __init__(self, value: Mapping[str, Any]) -> None:
        required = ("id", "version", "scope", "enabled")
        if any(key not in value for key in required):
            raise ValueError("Claude plugin list entry is missing a required field")
        if not isinstance(value["id"], str) or not value["id"] or "\x00" in value["id"]:
            raise ValueError("Claude plugin list entry has an invalid id")
        if not isinstance(value["version"], str) or not value["version"]:
            raise ValueError("Claude plugin list entry has an invalid version")
        if value["scope"] not in _CLAUDE_SCOPES:
            raise ValueError("Claude plugin list entry has an unknown scope")
        if not isinstance(value["enabled"], bool):
            raise ValueError("Claude plugin list entry has an invalid enabled state")
        self.id = value["id"]
        self.version = value["version"]
        self.scope = value["scope"]
        self.enabled = value["enabled"]
        self.path = _optional_entry_path(value)


class ClaudeNativePackageAdapter(_NativePackageCLIAdapter):
    """Operate Claude whole plugins discovered in the user skills directory."""

    harness = "claude"
    executable = "claude"

    def inspect(self, package_id: str, deployment: dict | None = None) -> NativeTarget:
        self._set_diagnostics()
        if not self.mechanism_available:
            return NativeTarget(self.harness, self.root, mechanism_available=False)
        expected = self._expected_target(package_id, deployment)
        try:
            entries = self._list_entries()
        except NativeCLIError as error:
            self._set_diagnostics("native-list-unavailable", str(error))
            return NativeTarget(
                self.harness,
                self.root,
                inventory_complete=False,
                mechanism_available=False,
            )

        registrations: list[NativeRegistration] = []
        complete = True
        for entry in entries:
            root = self._entry_root(entry, expected, deployment)
            if root is None:
                registrations.append(NativeRegistration(entry.id))
                complete = False
                if entry.path is not None:
                    self._set_diagnostics(*self.last_diagnostics, "native-registration-root-unavailable")
                else:
                    self._set_diagnostics(*self.last_diagnostics, "native-registration-path-unreported")
                continue
            deployment_id = self._matching_deployment_id(entry, root, deployment)
            registrations.append(
                NativeRegistration(
                    entry.id,
                    "native-root",
                    root=root,
                    deployment_id=deployment_id,
                )
            )
            if entry.scope != "user" or not _inside(root, self.root):
                complete = False

        if expected is not None and expected.is_dir():
            expected_ids = {entry.id for entry in entries}
            expected_id = deployment.get("nativeId") if deployment else None
            if expected_id is None:
                # A local target's manifest is authoritative for its expected identity,
                # but the adapter cannot infer that identity from a directory name.
                expected_id = self._manifest_native_id(expected)
            if expected_id and expected_id not in expected_ids:
                complete = False
                self._set_diagnostics(*self.last_diagnostics, "native-list-omits-owned-skills-dir")

        if not complete:
            self._set_diagnostics(*self.last_diagnostics, "native-inventory-incomplete")
        return NativeTarget(
            self.harness,
            self.root,
            tuple(registrations),
            inventory_complete=complete,
            mechanism_available=complete,
            occupied_identifiers=_unique(entry.id for entry in entries),
        )

    def verify(self, plan: PackageDeploymentPlan, expected: str) -> dict:
        if expected not in {"present", "absent", "enabled", "disabled"}:
            return {"verified": False, "enabled": None}
        try:
            native_id, target = self._plan_identity(plan)
            entries = self._list_entries()
            matches = [entry for entry in entries if entry.id == native_id]
            if expected == "absent":
                verified = not matches and _safe_missing_path(target, self.root)
                return {
                    "verified": verified,
                    "enabled": None,
                    "nativeId": native_id,
                    "path": str(target),
                }
            if len(matches) != 1:
                return {"verified": False, "enabled": None}
            entry = matches[0]
            if entry.scope != "user" or not self._entry_matches_path(entry, target):
                return {"verified": False, "enabled": entry.enabled}
            if expected == "enabled" and not entry.enabled:
                return {"verified": False, "enabled": entry.enabled}
            if expected == "disabled" and entry.enabled:
                return {"verified": False, "enabled": entry.enabled}
            return {
                "verified": True,
                "enabled": entry.enabled,
                "nativeId": native_id,
                "path": str(target),
            }
        except (NativeCLIError, OSError, RuntimeError, ValueError, TypeError) as error:
            self._set_diagnostics("native-verification-unavailable", str(error))
            return {"verified": False, "enabled": None}

    def set_enabled(self, plan: PackageDeploymentPlan, enabled: bool) -> dict:
        _require_action(plan, 'managed', self.mechanism_available)
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a boolean")
        before = self.verify(plan, "present")
        if not before["verified"]:
            raise NativeCLIError("Claude plugin identity and path were not verified")
        if before["enabled"] is enabled:
            return {"ok": True, "changed": False, **before}
        native_id, _target = self._plan_identity(plan)
        self._run_command([self.executable, "plugin", "enable" if enabled else "disable", native_id])
        after = self.verify(plan, "enabled" if enabled else "disabled")
        if not after["verified"]:
            raise NativeCLIError("Claude plugin enable/disable was not verified")
        return {"ok": True, "changed": True, **after}

    def _list_entries(self) -> list[_ClaudeEntry]:
        payload = self._run_json([self.executable, "plugin", "list", "--json"])
        if not isinstance(payload, list):
            raise NativeCLIError("Claude plugin list JSON is not an array")
        entries: list[_ClaudeEntry] = []
        seen: set[str] = set()
        for value in payload:
            if not isinstance(value, Mapping):
                raise NativeCLIError("Claude plugin list contains a non-object entry")
            try:
                entry = _ClaudeEntry(value)
            except (TypeError, ValueError) as error:
                raise NativeCLIError(str(error)) from error
            if entry.id in seen:
                raise NativeCLIError("Claude plugin list contains duplicate identities")
            seen.add(entry.id)
            entries.append(entry)
        return entries

    def _expected_target(self, package_id: str, deployment: dict | None) -> Path | None:
        if deployment is not None and deployment.get("harness") == self.harness:
            value = deployment.get("target")
            if isinstance(value, str):
                return Path(value)
        if _PACKAGE_ID.fullmatch(package_id):
            return self.root / "skills" / f"skill-manager-{package_id[:16]}"
        return None

    def _entry_root(
        self,
        entry: _ClaudeEntry,
        expected: Path | None,
        deployment: dict | None,
    ) -> Path | None:
        if entry.path is not None:
            return entry.path if _safe_actual_directory(entry.path) else None
        return None

    def _matching_deployment_id(
        self, entry: _ClaudeEntry, root: Path, deployment: dict | None
    ) -> str | None:
        if deployment is None or entry.id != deployment.get("nativeId"):
            return None
        deployment_target = deployment.get("target")
        deployment_id = deployment.get("deploymentId")
        if not isinstance(deployment_target, str) or not isinstance(deployment_id, str):
            return None
        target = Path(deployment_target)
        if not _same_path(root, target) or not _safe_actual_directory(target, self.root):
            return None
        return deployment_id

    def _plan_identity(self, plan: PackageDeploymentPlan) -> tuple[str, Path]:
        if plan.target_harness != self.harness or plan.strategy != "native-local":
            raise ValueError("plan is not a Claude native-local plan")
        native_id = plan.surface.get("nativeId")
        value = plan.surface.get("path")
        if not isinstance(native_id, str) or not isinstance(value, str):
            raise ValueError("Claude plan has no exact native identity and path")
        target = Path(value)
        if not (
            _safe_missing_path(target, self.root)
            or _safe_actual_directory(target, self.root)
        ):
            raise ValueError("Claude plan target is unsafe")
        return native_id, target

    def _entry_matches_path(self, entry: _ClaudeEntry, target: Path) -> bool:
        if not _safe_actual_directory(target, self.root):
            return False
        if entry.path is None or not _same_path(entry.path, target):
            return False
        return self._manifest_native_id(target) == entry.id

    @staticmethod
    def _manifest_native_id(root: Path) -> str | None:
        name = _manifest_name(root / ".claude-plugin/plugin.json")
        return f"{name}@skills-dir" if name else None


class _CodexEntry:
    def __init__(self, value: Mapping[str, Any], *, installed_section: bool) -> None:
        required = (
            "pluginId",
            "name",
            "marketplaceName",
            "installed",
            "enabled",
            "source",
            "installPolicy",
            "authPolicy",
        )
        if any(key not in value for key in required):
            raise ValueError("Codex plugin list entry is missing a required field")
        for key in ("pluginId", "name", "marketplaceName"):
            if not isinstance(value[key], str) or not value[key] or "\x00" in value[key]:
                raise ValueError(f"Codex plugin list entry has an invalid {key}")
        if value["pluginId"] != f"{value['name']}@{value['marketplaceName']}":
            raise ValueError("Codex plugin identity does not match its name and marketplace")
        if not isinstance(value["installed"], bool) or not isinstance(value["enabled"], bool):
            raise ValueError("Codex plugin list entry has an invalid state")
        if value["installed"] is not installed_section:
            raise ValueError("Codex plugin list section has an inconsistent installed state")
        if value["installPolicy"] not in _CODEX_INSTALL_POLICIES:
            raise ValueError("Codex plugin list entry has an unknown install policy")
        if value["authPolicy"] not in _CODEX_AUTH_POLICIES:
            raise ValueError("Codex plugin list entry has an unknown auth policy")
        if "version" in value and value["version"] is not None and not isinstance(value["version"], str):
            raise ValueError("Codex plugin list entry has an invalid version")
        if installed_section and not isinstance(value.get("version"), str):
            raise ValueError("Installed Codex plugin has no version")
        self.plugin_id = value["pluginId"]
        self.name = value["name"]
        self.marketplace = value["marketplaceName"]
        self.installed = value["installed"]
        self.enabled = value["enabled"]
        self.version = value.get("version")
        self.source = _codex_source(value["source"])
        self.installed_path = _optional_absolute_path(value, "installedPath")


class CodexNativePackageAdapter(_NativePackageCLIAdapter):
    """Operate Codex plugins through its local marketplace and plugin CLI."""

    harness = "codex"
    executable = "codex"

    def inspect(self, package_id: str, deployment: dict | None = None) -> NativeTarget:
        self._set_diagnostics()
        if not self.mechanism_available:
            return NativeTarget(self.harness, self.root, mechanism_available=False)
        try:
            installed, available = self._list_entries()
            markets = self._marketplaces()
        except NativeCLIError as error:
            self._set_diagnostics("native-list-unavailable", str(error))
            return NativeTarget(
                self.harness,
                self.root,
                inventory_complete=False,
                mechanism_available=False,
            )

        registrations: list[NativeRegistration] = []
        complete = True
        for entry in installed:
            root = self._installed_root(entry)
            source = _native_source(entry.source)
            if root is None:
                registrations.append(NativeRegistration(entry.plugin_id, 'native-source', source=source)
                                     if source else NativeRegistration(entry.plugin_id))
                complete = False
                self._set_diagnostics(*self.last_diagnostics, "codex-cache-root-unavailable")
                continue
            deployment_id = self._matching_deployment_id(entry, root, deployment, package_id)
            if deployment_id:
                root = Path(deployment['target'])
            elif source:
                registrations.append(NativeRegistration(entry.plugin_id, 'native-source', source=source))
                continue
            elif entry.source.get('source') == 'local':
                declared = Path(entry.source['path'])
                if _safe_actual_directory(declared):
                    root = declared
                else:
                    complete = False
            else:
                complete = False
                self._set_diagnostics(*self.last_diagnostics, 'native-source-unreconciled')
            registrations.append(
                NativeRegistration(
                    entry.plugin_id,
                    "native-root",
                    root=root,
                    deployment_id=deployment_id,
                )
            )

        if not complete:
            self._set_diagnostics(*self.last_diagnostics, "native-inventory-incomplete")
        identifiers = _unique(
            entry.plugin_id for entry in (*installed, *available)
        )
        marketplaces = tuple(item['name'] for item in markets)
        return NativeTarget(
            self.harness,
            self.root,
            tuple(registrations),
            inventory_complete=complete,
            mechanism_available=True,
            occupied_identifiers=identifiers + marketplaces,
        )

    def verify(self, plan: PackageDeploymentPlan, expected: str) -> dict:
        if expected not in {"present", "absent", "enabled", "disabled"}:
            return {"verified": False, "enabled": None}
        try:
            info = self._plan_info(plan, require_staged=expected != "absent")
            installed, _available = self._list_entries()
            matches = [entry for entry in installed if entry.plugin_id == info["native_id"]]
            if expected == "absent":
                verified = (
                    not matches
                    and self._native_absent(info)
                    and _safe_missing_path(info["marketplace_root"], self.root)
                    and not any(row['name'] == info['marketplace_id'] for row in self._marketplaces())
                )
                return {
                    "verified": verified,
                    "enabled": None,
                    "nativeId": info["native_id"],
                    "path": str(info["marketplace_root"]),
                }
            if len(matches) != 1:
                return {"verified": False, "enabled": None}
            entry = matches[0]
            if not self._codex_provenance_matches(entry, info):
                return {"verified": False, "enabled": entry.enabled}
            installed_root = self._installed_root(entry)
            if plan.fingerprint and package_fingerprint(installed_root) != plan.fingerprint:
                return {"verified": False, "enabled": entry.enabled}
            if expected == "enabled" and not entry.enabled:
                return {"verified": False, "enabled": entry.enabled}
            if expected == "disabled" and entry.enabled:
                return {"verified": False, "enabled": entry.enabled}
            installed_root = self._installed_root(entry)
            return {
                "verified": True,
                "enabled": entry.enabled,
                "nativeId": info["native_id"],
                "path": str(installed_root),
                "nativeFingerprint": package_fingerprint(installed_root.parent),
            }
        except (NativeCLIError, OSError, RuntimeError, ValueError, TypeError) as error:
            self._set_diagnostics("native-verification-unavailable", str(error))
            return {"verified": False, "enabled": None}

    def install(self, plan: PackageDeploymentPlan) -> dict:
        _require_action(plan, 'absent', self.mechanism_available)
        info = self._plan_info(plan, require_staged=True)
        installed, available = self._list_entries()
        if any(entry.plugin_id == info["native_id"] for entry in installed):
            raise NativeCLIError("Codex native plugin identity is already installed")
        if any(item['name'] == info['marketplace_id'] for item in self._marketplaces()):
            raise NativeCLIError("Codex marketplace namespace is already occupied")
        if not self._native_absent(info):
            raise NativeCLIError("Codex native cache target is already occupied")

        # These argv values come from the Task06 plan and are passed unchanged.
        created = self._run_json(info['marketplace_argv'])
        if (not isinstance(created, Mapping) or created.get('alreadyAdded') is not False or
                created.get('marketplaceName') != info['marketplace_id'] or
                created.get('installedRoot') != str(info['marketplace_root'])):
            raise NativeCLIError('Marketplace creation was not exclusive and verified')
        self._run_command(info["install_argv"])
        verification = self.verify(plan, "present")
        if not verification["verified"]:
            raise NativeCLIError("Codex native install was not verified")
        return {"ok": True, "changed": True, **verification}

    def reinstall(self, plan):
        _require_action(plan, 'managed', self.mechanism_available)
        info = self._plan_info(plan, require_staged=True)
        if not self._marketplace_matches(info):
            raise NativeCLIError('Owned marketplace source changed')
        self._run_command(info['install_argv'])

    def uninstall(self, plan: PackageDeploymentPlan) -> dict:
        _require_action(plan, 'managed', self.mechanism_available)
        info = self._plan_info(plan, require_staged=True)
        before = self.verify(plan, "present")
        if not before["verified"]:
            raise NativeCLIError("Codex native plugin ownership and provenance were not verified")

        # Derive only the destructive commands from the already-validated Task06 argv.
        self._run_command([info["executable"], "plugin", "remove", info["native_id"], "--json"])
        installed, _available = self._list_entries()
        if any(entry.plugin_id == info["native_id"] for entry in installed) or not self._native_absent(info):
            raise NativeCLIError("Codex native plugin removal was not verified")
        self._run_command(
            [info["executable"], "plugin", "marketplace", "remove", info["marketplace_id"]]
        )
        if any(item['name'] == info['marketplace_id'] for item in self._marketplaces()):
            raise NativeCLIError("Codex marketplace removal was not verified")
        return {"ok": True, "changed": True, "verified": True}

    def _marketplaces(self):
        payload = self._run_json([self.executable, 'plugin', 'marketplace', 'list', '--json'])
        if not isinstance(payload, Mapping) or not isinstance(payload.get('marketplaces'), list):
            raise NativeCLIError('Unverified marketplace inventory')
        rows = payload['marketplaces']
        if any(not isinstance(r, dict) or not isinstance(r.get('name'), str) or not isinstance(r.get('root'), str) for r in rows):
            raise NativeCLIError('Malformed marketplace inventory')
        if len({r['name'] for r in rows}) != len(rows):
            raise NativeCLIError('Ambiguous marketplace inventory')
        return rows

    def _marketplace_matches(self, info):
        rows = [r for r in self._marketplaces() if r['name'] == info['marketplace_id']]
        return len(rows) == 1 and rows[0]['root'] == str(info['marketplace_root']) and rows[0].get('marketplaceSource') == {
            'sourceType': 'local', 'source': str(info['marketplace_root'])}

    def set_enabled(self, plan: PackageDeploymentPlan, enabled: bool) -> None:
        _require_action(plan, 'managed', self.mechanism_available)
        if not self.verify(plan, 'present')['verified']:
            raise NativeCLIError('Owned native install could not be verified')
        path = self.root / 'config.toml'
        if any(p.is_symlink() for p in (path, *path.parents)) or path.stat().st_size > _MAX_JSON_BYTES:
            raise NativeCLIError('Unsafe native configuration')
        original = path.read_bytes().decode('utf-8')
        value = tomllib.loads(original)
        native_id = plan.surface['nativeId']
        if not isinstance(value.get('plugins', {}).get(native_id, {}).get('enabled'), bool):
            raise NativeCLIError('Native enabled setting is not explicit')
        # Edit only the boolean in the native CLI's owned table; preserve other bytes.
        pattern = re.compile(r'(?ms)^(\[plugins\."' + re.escape(native_id) + r'"\]\r?\n(?:(?!^\[).)*?^enabled\s*=\s*)(true|false)(?=\s*(?:#|$))')
        replacement, count = pattern.subn(lambda m: m[1] + ('true' if enabled else 'false'), original)
        expected = deepcopy(value)
        expected['plugins'][native_id]['enabled'] = enabled
        if count != 1 or tomllib.loads(replacement) != expected or path.read_bytes() != original.encode('utf-8'):
            raise NativeCLIError('Native configuration requires manual reconciliation')
        mode = stat.S_IMODE(path.stat().st_mode)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.native-config-')
        try:
            with os.fdopen(fd, 'w', newline='') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(replacement)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _list_entries(self) -> tuple[list[_CodexEntry], list[_CodexEntry]]:
        payload = self._run_json([self.executable, "plugin", "list", "--json"])
        if not isinstance(payload, Mapping):
            raise NativeCLIError("Codex plugin list JSON is not an object")
        if not isinstance(payload.get("installed"), list) or not isinstance(payload.get("available"), list):
            raise NativeCLIError("Codex plugin list JSON has an unverified schema")
        installed = self._parse_entries(payload["installed"], installed_section=True)
        available = self._parse_entries(payload["available"], installed_section=False)
        identities = [entry.plugin_id for entry in (*installed, *available)]
        if len(identities) != len(set(identities)):
            raise NativeCLIError("Codex plugin list contains duplicate identities")
        return installed, available

    @staticmethod
    def _parse_entries(values: list[Any], *, installed_section: bool) -> list[_CodexEntry]:
        result = []
        for value in values:
            if not isinstance(value, Mapping):
                raise NativeCLIError("Codex plugin list contains a non-object entry")
            try:
                result.append(_CodexEntry(value, installed_section=installed_section))
            except ValueError as error:
                raise NativeCLIError(str(error)) from error
        return result

    def _installed_root(self, entry: _CodexEntry) -> Path | None:
        if not isinstance(entry.version, str) or not _SEGMENT.fullmatch(entry.version):
            return None
        if not _SEGMENT.fullmatch(entry.name) or not _SEGMENT.fullmatch(entry.marketplace):
            return None
        root = self.root / "plugins" / "cache" / entry.marketplace / entry.name / entry.version
        if entry.installed_path is not None and not _same_path(entry.installed_path, root):
            return None
        if not _safe_actual_directory(root, self.root):
            return None
        return root if _manifest_name(root / "plugin.json", root / ".codex-plugin/plugin.json") == entry.name else None

    def _matching_deployment_id(
        self,
        entry: _CodexEntry,
        root: Path,
        deployment: dict | None,
        package_id: str,
    ) -> str | None:
        if deployment is None or deployment.get("harness") != self.harness:
            return None
        if deployment.get("nativeId") != entry.plugin_id or deployment.get("target") is None:
            return None
        target = Path(deployment["target"])
        deployment_id = deployment.get("deploymentId")
        if not isinstance(deployment_id, str):
            return None
        if not _safe_actual_directory(target, self.root):
            return None
        selected = deployment.get('placementPackageId') or deployment.get("selectedPackageId") or package_id
        if not isinstance(selected, str) or not _PACKAGE_ID.fullmatch(selected):
            return None
        expected_source = self.root / "skill-manager-marketplaces" / entry.marketplace / "plugins" / selected[:16]
        if not self._source_path_matches(entry, expected_source):
            return None
        if target != expected_source.parent.parent or package_fingerprint(root) != deployment['appliedFingerprint']:
            return None
        if deployment.get('nativeFingerprint') != package_fingerprint(root.parent):
            return None
        if not any(r['name'] == entry.marketplace and r['root'] == str(target) and
                   r.get('marketplaceSource') == {'sourceType': 'local', 'source': str(target)} for r in self._marketplaces()):
            return None
        return deployment_id

    def _plan_info(self, plan: PackageDeploymentPlan, *, require_staged: bool) -> dict[str, Any]:
        if plan.target_harness != self.harness or plan.strategy != "native-install":
            raise ValueError("plan is not a Codex native-install plan")
        surface = plan.surface
        native_id = surface.get("nativeId")
        marketplace_id = surface.get("marketplaceId")
        marketplace_path = surface.get("path")
        marketplace_document_path = surface.get("marketplacePath")
        whole_package_path = surface.get("wholePackagePath")
        marketplace_argv = surface.get("marketplaceArgv")
        install_argv = surface.get("installArgv")
        document = surface.get("marketplaceDocument")
        if not all(isinstance(value, str) for value in (
            native_id, marketplace_id, marketplace_path,
            marketplace_document_path, whole_package_path,
        )) or not isinstance(document, Mapping):
            raise ValueError("Codex plan is missing exact marketplace coordinates")
        if "@" not in native_id or native_id.rsplit("@", 1)[1] != marketplace_id:
            raise ValueError("Codex plan identity does not match its marketplace")
        native_name, native_marketplace = native_id.rsplit("@", 1)
        if not _SEGMENT.fullmatch(native_name) or not _SEGMENT.fullmatch(native_marketplace):
            raise ValueError("Codex plan has an unsafe native identity")
        selected = plan.surface.get('placementPackageId') or plan.selected_package_id or plan.managed_package_id
        if not isinstance(selected, str) or not _PACKAGE_ID.fullmatch(selected):
            raise ValueError("Codex plan has an invalid managed package identity")
        expected_marketplace = "skill-manager-" + selected[:16]
        expected_root = self.root / "skill-manager-marketplaces" / expected_marketplace
        expected_package = expected_root / "plugins" / selected[:16]
        expected_catalog = expected_root / ".agents/plugins/marketplace.json"
        if marketplace_id != expected_marketplace or not _same_path(Path(marketplace_path), expected_root):
            raise ValueError("Codex plan is outside its owned marketplace namespace")
        if not _same_path(Path(whole_package_path), expected_package) or not _same_path(
            Path(marketplace_document_path), expected_catalog
        ):
            raise ValueError("Codex plan has an unexpected marketplace path")
        expected_marketplace_argv = [self.executable, "plugin", "marketplace", "add", str(expected_root), '--json']
        expected_install_argv = [self.executable, "plugin", "add", native_id, "--json"]
        if not isinstance(marketplace_argv, Sequence) or isinstance(marketplace_argv, (str, bytes)):
            raise ValueError("Codex plan has no marketplace argv")
        if not isinstance(install_argv, Sequence) or isinstance(install_argv, (str, bytes)):
            raise ValueError("Codex plan has no install argv")
        if list(marketplace_argv) != expected_marketplace_argv or list(install_argv) != expected_install_argv:
            raise ValueError("Codex plan argv does not match the Task06 plan")
        if document.get("name") != marketplace_id:
            raise ValueError("Codex marketplace document has the wrong namespace")
        plugins = document.get("plugins")
        expected_plugin = {
            "name": native_name,
            "source": {"source": "local", "path": "./plugins/" + selected[:16]},
        }
        if not isinstance(plugins, list) or plugins != [expected_plugin]:
            raise ValueError("Codex marketplace document has an unexpected plugin source")

        info = {
            "native_id": native_id,
            "marketplace_id": marketplace_id,
            "marketplace_root": expected_root,
            "marketplace_path": expected_catalog,
            "whole_package_path": expected_package,
            "marketplace_document": dict(document),
            "marketplace_argv": list(marketplace_argv),
            "install_argv": list(install_argv),
            "executable": self.executable,
        }
        if require_staged:
            if not _safe_actual_directory(expected_root, self.root):
                raise ValueError("Codex marketplace root is not staged safely")
            if not _safe_actual_directory(expected_package, expected_root):
                raise ValueError("Codex whole package is not staged safely")
            actual_document = _read_json_file(expected_catalog)
            if actual_document != info["marketplace_document"]:
                raise ValueError("Codex marketplace document does not match the plan")
        return info

    def _codex_provenance_matches(self, entry: _CodexEntry, info: dict[str, Any]) -> bool:
        installed_root = self._installed_root(entry)
        if installed_root is None or entry.plugin_id != info["native_id"] or not entry.installed:
            return False
        if not self._source_path_matches(entry, info["whole_package_path"]):
            return False
        if not self._marketplace_matches(info):
            return False
        try:
            return _read_json_file(info["marketplace_path"]) == info["marketplace_document"]
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return False

    def _source_path_matches(self, entry: _CodexEntry, expected: Path) -> bool:
        if entry.source.get("source") != "local":
            return False
        raw = entry.source.get("path")
        if not isinstance(raw, str):
            return False
        actual = Path(raw)
        if not _same_path(actual, expected) or not _safe_actual_directory(actual):
            return False
        return _manifest_name(actual / "plugin.json", actual / ".codex-plugin/plugin.json") is not None

    @staticmethod
    def _native_absent(info: dict[str, Any]) -> bool:
        native_root = info["marketplace_root"].parent.parent
        cache_base = native_root / "plugins" / "cache" / info["marketplace_id"] / info["native_id"].split("@", 1)[0]
        return _safe_missing_path(cache_base)


class OpenCodeNativePackageAdapter(_NativePackageCLIAdapter):
    """Register one staged whole package through OpenCode's global plugin CLI."""

    harness = "opencode"
    executable = "opencode"

    def __init__(
        self,
        root: Path | str,
        runner: NativeCLIRunner,
        *,
        config_paths: Sequence[Path] = (),
        mechanism_available: bool = False,
    ) -> None:
        super().__init__(root, runner, mechanism_available=mechanism_available)
        self.config_paths = tuple(Path(path) for path in config_paths) or (
            self.root / "config.json",
            self.root / "opencode.json",
            self.root / "opencode.jsonc",
        )

    def inspect(self, package_id: str, deployment: dict | None = None) -> NativeTarget:
        self._set_diagnostics()
        try:
            registrations = read_opencode_registrations(self.config_paths)
            complete = self._inventory_is_complete(registrations)
            if self.mechanism_available:
                self._check_binding()
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            self._set_diagnostics("native-list-unavailable", str(error), "native-inventory-incomplete")
            return NativeTarget(
                self.harness,
                self.root,
                inventory_complete=False,
                mechanism_available=False,
            )

        if not complete:
            self._set_diagnostics("native-inventory-incomplete")
        reconciled = tuple(
            replace(
                registration,
                deployment_id=(
                    deployment.get("deploymentId")
                    if deployment is not None
                    and registration.native_id == deployment.get("nativeId")
                    and registration.root is not None
                    and _same_path(registration.root, Path(deployment.get("target", "")))
                    and registration.config_key == 'plugin'
                    and deployment.get('nativeFingerprint') == self._registration_fingerprint(registration)
                    else registration.deployment_id
                ),
            )
            for registration in registrations
        )
        return NativeTarget(
            self.harness,
            self.root,
            reconciled,
            inventory_complete=complete,
            mechanism_available=self.mechanism_available and complete,
            occupied_identifiers=_unique(registration.native_id for registration in registrations),
        )

    def verify(self, plan: PackageDeploymentPlan, expected: str) -> dict:
        if expected not in {"present", "absent"}:
            return {"verified": False, "enabled": None}
        try:
            info = self._plan_info(plan, require_staged=expected != "absent")
            registrations = read_opencode_registrations(self.config_paths)
            if not self._inventory_is_complete(registrations):
                return {"verified": False, "enabled": None}
            matches = [
                registration
                for registration in registrations
                if registration.native_id == info["native_id"]
            ]
            if expected == "present":
                if len(matches) != 1 or not self._owned_registration_matches(matches[0], info):
                    return {"verified": False, "enabled": None}
                debug = self._debug_info()
                if not _debug_lists_plugin(debug, info["native_id"]):
                    return {"verified": False, "enabled": None}
                return {
                    "verified": True,
                    "enabled": None,
                    "nativeId": info["native_id"],
                    "path": str(info["target"]),
                    "nativeFingerprint": self._registration_fingerprint(matches[0]),
                }

            if matches:
                return {"verified": False, "enabled": None}
            debug = self._debug_info()
            return {
                "verified": not _debug_lists_plugin(debug, info["native_id"]),
                "enabled": None,
                "nativeId": info["native_id"],
                "path": str(info["target"]),
            }
        except (NativeCLIError, OSError, RuntimeError, ValueError, TypeError) as error:
            self._set_diagnostics("native-verification-unavailable", str(error))
            return {"verified": False, "enabled": None}

    def install(self, plan: PackageDeploymentPlan) -> dict:
        _require_action(plan, "absent", self.mechanism_available)
        info = self._plan_info(plan, require_staged=True)
        registrations = read_opencode_registrations(self.config_paths)
        if not self._inventory_is_complete(registrations):
            raise NativeCLIError("OpenCode plugin inventory is ambiguous")
        if any(
            registration.native_id == info["native_id"]
            or (registration.root is not None and _same_path(registration.root, info["target"]))
            for registration in registrations
        ):
            raise NativeCLIError("OpenCode package path is already registered")
        self._run_command([self.executable, "plugin", info["package_spec"], "--global"])
        verification = self.verify(plan, "present")
        if not verification["verified"]:
            raise NativeCLIError("OpenCode native install was not verified")
        return {"ok": True, "changed": True, **verification}

    def reinstall(self, plan: PackageDeploymentPlan) -> dict:
        """An owned file-URL stays registered while its whole directory is replaced."""
        _require_action(plan, "managed", self.mechanism_available)
        verification = self.verify(plan, "present")
        if not verification["verified"]:
            raise NativeCLIError("OpenCode owned registration is not verified")
        return {"ok": True, "changed": False, **verification}

    def uninstall(self, plan: PackageDeploymentPlan) -> dict:
        _require_action(plan, "managed", self.mechanism_available)
        info = self._plan_info(plan, require_staged=True)
        before = self.verify(plan, "present")
        if not before["verified"]:
            raise NativeCLIError("OpenCode registration and source were not verified")
        self._remove_exact_registration(info["native_id"])
        verification = self.verify(plan, "absent")
        if not verification["verified"]:
            raise NativeCLIError("OpenCode native removal was not verified")
        return {"ok": True, "changed": True, **verification}

    def _debug_info(self) -> str:
        # Keep stderr: a listed config entry alone must not hide native loader errors.
        result = self._process([self.executable, "debug", "info", "--print-logs", "--log-level", "ERROR"])
        errors = result.stderr or ''
        if isinstance(errors, bytes):
            errors = errors.decode('utf-8')
        if errors.strip():
            raise NativeCLIError('OpenCode reported a native initialization error')
        output = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
        if not isinstance(output, str):
            raise NativeCLIError("OpenCode debug info returned an unsupported result")
        return output

    def _process(self, argv):
        try:
            result = self.runner(argv)
        except Exception as error:
            raise NativeCLIError('OpenCode native command is unavailable') from error
        if not isinstance(result, subprocess.CompletedProcess) or result.returncode != 0:
            raise NativeCLIError('OpenCode native command failed')
        return result

    def _plan_info(self, plan: PackageDeploymentPlan, *, require_staged: bool) -> dict[str, Any]:
        if plan.target_harness != self.harness or plan.strategy != "native-install":
            raise ValueError("plan is not an OpenCode native-install plan")
        surface = plan.surface
        value = surface.get("path")
        native_id = surface.get("nativeId")
        package_spec = surface.get("packageSpec")
        placement = surface.get("placementPackageId") or plan.selected_package_id or plan.managed_package_id
        if not all(isinstance(item, str) for item in (value, native_id, package_spec, placement)):
            raise ValueError("OpenCode plan has no exact package coordinates")
        if not _PACKAGE_ID.fullmatch(placement):
            raise ValueError("OpenCode plan has an invalid package identity")
        target = Path(value)
        expected = self.root / "skill-manager-packages" / placement[:16]
        if not _same_path(target, expected) or native_id != target.as_uri() or package_spec != native_id:
            raise ValueError("OpenCode plan does not use its exact owned file URL")
        if not _safe_actual_directory(self.root):
            raise ValueError("OpenCode configuration root is unsafe")
        if require_staged and not _safe_actual_directory(target, self.root):
            raise ValueError("OpenCode whole package is not staged safely")
        if not require_staged and not (
            _safe_actual_directory(target, self.root) or _safe_missing_path(target, self.root)
        ):
            raise ValueError("OpenCode whole package target is unsafe")
        return {
            "target": target,
            "native_id": native_id,
            "package_spec": package_spec,
            "fingerprint": plan.fingerprint,
        }

    def _owned_registration_matches(self, registration: NativeRegistration, info: dict[str, Any]) -> bool:
        return (
            registration.value_kind == "string"
            and registration.raw == info["native_id"]
            and registration.evidence == "native-root"
            and registration.root is not None
            and _same_path(registration.root, info["target"])
            and _safe_actual_directory(info["target"], self.root)
            and (not info["fingerprint"] or package_fingerprint(info["target"]) == info["fingerprint"])
        )

    def _check_binding(self):
        if not _safe_actual_directory(self.root):
            raise NativeCLIError('OpenCode configuration root must exist and be safe')
        paths = self._run([self.executable, 'debug', 'paths'])
        help_result = self._process([self.executable, 'plugin', '--help'])
        help_text = ''.join(value.decode('utf-8') if isinstance(value, bytes) else value or ''
                            for value in (help_result.stdout, help_result.stderr))
        found = re.findall(r'^config\s+(.+)$', paths, re.MULTILINE) if isinstance(paths, str) else []
        if len(found) != 1 or not _same_path(Path(found[0].strip()), self.root):
            raise NativeCLIError('OpenCode global config binding does not match the configured root')
        if not isinstance(help_text, str) or 'plugin <module>' not in help_text or '--global' not in help_text:
            raise NativeCLIError('OpenCode native plugin installation mechanism is unavailable')

    @staticmethod
    def _registration_fingerprint(registration):
        value = [str(registration.config_path), registration.config_key, registration.value_kind, registration.raw]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def _inventory_is_complete(self, registrations: Sequence[NativeRegistration]) -> bool:
        # Auto-loaded JS/TS entries have no proven package provenance in this adapter.
        for directory in {self.root, *(path.parent for path in self.config_paths)}:
            for name in ('plugin', 'plugins'):
                folder = directory / name
                if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                    return False
                if folder.is_dir() and any(path.suffix in ('.js', '.ts') for path in folder.iterdir()):
                    return False
        if any(r.config_key != 'plugin' or r.evidence == 'native-control' for r in registrations):
            return False
        if len({registration.native_id for registration in registrations}) != len(registrations):
            return False
        roots = [
            registration.root.resolve(strict=False)
            for registration in registrations
            if registration.evidence == "native-root" and registration.root is not None
        ]
        return len(roots) == len(set(roots))

    def _remove_exact_registration(self, native_id: str) -> None:
        matches = []
        contents = {}
        for path in self.config_paths:
            if not path.exists():
                if path.is_symlink():
                    raise NativeCLIError("OpenCode configuration path is unsafe")
                continue
            original = _read_config_text(path)
            contents[path] = original
            matches.extend((path, *match) for match in _find_opencode_string_entries(
                path, native_id, text=original
            ))
        if len(matches) != 1:
            raise NativeCLIError("OpenCode exact plugin registration is ambiguous or absent")
        path, start, end, previous_comma, following_comma = matches[0]
        if path.parent != self.root:
            raise NativeCLIError('Owned OpenCode registration moved outside the target configuration root')
        original = contents[path]
        spans = [(start, end)]
        if following_comma is not None:
            spans.append((following_comma, following_comma + 1))
        elif previous_comma is not None:
            spans.append((previous_comma, previous_comma + 1))
        updated = _remove_spans(original, spans)
        try:
            json.loads(strip_jsonc(updated), object_pairs_hook=_object_without_duplicate_keys)
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise NativeCLIError("OpenCode configuration cannot be safely rewritten") from error
        mode = stat.S_IMODE(path.stat().st_mode)
        if _read_config_text(path) != original:
            raise NativeCLIError('OpenCode configuration changed before removal')
        atomic_write_text(path, updated)
        try:
            path.chmod(mode)
        except OSError as error:
            raise NativeCLIError("OpenCode configuration permissions could not be preserved") from error


def _explicit_root(value: Path | str) -> Path:
    root = Path(value)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("native adapter root must be an explicit bounded absolute path")
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("native adapter root may not contain symlinks")
    return root


def _read_config_text(path: Path) -> str:
    try:
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.stat().st_size > _MAX_JSON_BYTES
                or any(parent.is_symlink() for parent in (path, *path.parents))):
            raise NativeCLIError("OpenCode configuration path is unsafe")
        with path.open('r', encoding='utf-8', newline='') as stream:
            return stream.read()
    except NativeCLIError:
        raise
    except (OSError, UnicodeError) as error:
        raise NativeCLIError("OpenCode configuration could not be read") from error


def _find_opencode_string_entries(path: Path, expected: str, *, text: str | None = None):
    if not path.exists():
        if path.is_symlink():
            raise NativeCLIError("OpenCode configuration path is unsafe")
        return ()
    if text is None:
        text = _read_config_text(path)
    try:
        arrays = _jsonc_named_arrays(text, {"plugin", "plugins"})
    except (NativeCLIError, ValueError, UnicodeError) as error:
        raise NativeCLIError("OpenCode configuration cannot be safely parsed") from error
    matches = []
    for items in arrays:
        for start, end, previous_comma, following_comma, value, kind in items:
            if kind == "string" and value == expected:
                matches.append((start, end, previous_comma, following_comma))
    return tuple(matches)


def _jsonc_named_arrays(text: str, names: set[str]):
    index = _jsonc_skip(text, 0)
    if index >= len(text) or text[index] != "{":
        raise ValueError("configuration must be an object")
    index += 1
    arrays = []
    while True:
        index = _jsonc_skip(text, index)
        if index >= len(text):
            raise ValueError("configuration object is unterminated")
        if text[index] == "}":
            return arrays
        key, index = _jsonc_string(text, index)
        index = _jsonc_skip(text, index)
        if index >= len(text) or text[index] != ":":
            raise ValueError("configuration key has no value")
        index = _jsonc_skip(text, index + 1)
        if key in names:
            if index >= len(text) or text[index] != "[":
                raise ValueError("native plugin declaration is not an array")
            items, index = _jsonc_array_items(text, index)
            arrays.append(items)
        else:
            index = _jsonc_value_end(text, index)
        index = _jsonc_skip(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            return arrays
        raise ValueError("configuration object has an invalid separator")


def _jsonc_array_items(text: str, index: int):
    index += 1
    items = []
    previous_comma = None
    while True:
        index = _jsonc_skip(text, index)
        if index >= len(text):
            raise ValueError("native plugin array is unterminated")
        if text[index] == "]":
            return items, index + 1
        start = index
        if text[index] == '"':
            value, end = _jsonc_string(text, index)
            kind = "string"
        else:
            end = _jsonc_value_end(text, index)
            value = None
            kind = "other"
        separator = _jsonc_skip(text, end)
        following_comma = separator if separator < len(text) and text[separator] == "," else None
        items.append((start, end, previous_comma, following_comma, value, kind))
        if following_comma is not None:
            previous_comma = following_comma
            index = following_comma + 1
            continue
        if separator < len(text) and text[separator] == "]":
            return items, separator + 1
        raise ValueError("native plugin array has an invalid separator")


def _jsonc_string(text: str, index: int):
    if index >= len(text) or text[index] != '"':
        raise ValueError("expected a JSON string")
    cursor = index + 1
    escaped = False
    while cursor < len(text):
        character = text[cursor]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            try:
                return json.loads(text[index:cursor + 1]), cursor + 1
            except json.JSONDecodeError as error:
                raise ValueError("invalid JSON string") from error
        cursor += 1
    raise ValueError("JSON string is unterminated")


def _jsonc_value_end(text: str, index: int):
    if index >= len(text):
        raise ValueError("missing JSON value")
    if text[index] == '"':
        _value, return_index = _jsonc_string(text, index)
        return return_index
    if text[index] not in "[{":
        cursor = index
        while cursor < len(text) and text[cursor] not in ",]}" and text[cursor] not in "\r\n":
            cursor += 1
        if cursor == index:
            raise ValueError("missing JSON value")
        return cursor
    stack = ["]" if text[index] == "[" else "}"]
    cursor = index + 1
    while stack:
        cursor = _jsonc_skip(text, cursor)
        if cursor >= len(text):
            raise ValueError("nested JSON value is unterminated")
        if text[cursor] == '"':
            _value, cursor = _jsonc_string(text, cursor)
            continue
        if text[cursor] in "[{":
            stack.append("]" if text[cursor] == "[" else "}")
            cursor += 1
            continue
        if text[cursor] in "]}":
            if text[cursor] != stack[-1]:
                raise ValueError("nested JSON value has mismatched delimiters")
            stack.pop()
            cursor += 1
            continue
        cursor += 1
    return cursor


def _jsonc_skip(text: str, index: int):
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("JSONC block comment is unterminated")
            index = end + 2
            continue
        break
    return index


def _remove_spans(text: str, spans: Sequence[tuple[int, int]]) -> str:
    result = text
    for start, end in sorted(spans, reverse=True):
        result = result[:start] + result[end:]
    return result


def _debug_lists_plugin(output: str, native_id: str) -> bool:
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    return any(line.strip() == "- " + native_id for line in clean.splitlines())


def _optional_entry_path(value: Mapping[str, Any]) -> Path | None:
    keys = ("path", "installPath", "installedPath", "pluginPath", "root")
    present = [key for key in keys if key in value]
    if len(present) > 1:
        paths = {_absolute_path(value[key]) for key in present}
        if len(paths) != 1:
            raise ValueError("native plugin entry has conflicting paths")
        return paths.pop()
    return _absolute_path(value[present[0]]) if present else None


def _optional_absolute_path(value: Mapping[str, Any], key: str) -> Path | None:
    if key not in value:
        return None
    return _absolute_path(value[key])


def _absolute_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("native plugin path must be a non-empty absolute string")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("native plugin path must be absolute")
    return path


def _safe_actual_directory(path: Path, boundary: Path | None = None) -> bool:
    try:
        if not path.is_absolute() or path == Path(path.anchor):
            return False
        if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_dir():
            return False
        resolved = path.resolve(strict=True)
        if boundary is not None and not resolved.is_relative_to(boundary.resolve(strict=False)):
            return False
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_missing_path(path: Path, boundary: Path | None = None) -> bool:
    try:
        if not path.is_absolute() or path == Path(path.anchor):
            return False
        if any(item.is_symlink() for item in (path, *path.parents)):
            return False
        if boundary is not None and not path.resolve(strict=False).is_relative_to(boundary.resolve(strict=False)):
            return False
        return not path.exists()
    except (OSError, RuntimeError, ValueError):
        return False


def _inside(path: Path, boundary: Path) -> bool:
    try:
        return path.resolve(strict=True).is_relative_to(boundary.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False


def _manifest_name(*paths: Path) -> str | None:
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                continue
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        name = value.get("name") if isinstance(value, Mapping) else None
        if isinstance(name, str) and name:
            return name
    return None


def _read_json_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError("native JSON file is unsafe")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("native JSON file has linked provenance")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("native JSON file is not an object")
    return value


def _codex_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("source"), str):
        raise ValueError("Codex plugin list entry has an invalid source")
    kind = value["source"]
    required = {
        "remote": ("id",),
        "local": ("path",),
        "git": ("url",),
        "git-subdir": ("url", "path"),
        "npm": ("package",),
    }.get(kind)
    if required is None or any(not isinstance(value.get(key), str) or not value[key] for key in required):
        raise ValueError("Codex plugin list entry has an invalid source shape")
    return dict(value)


def _unique(values: Sequence[str] | Any) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _require_action(plan, ownership, available):
    if not available or plan.blockers or plan.support != 'supported' or not plan.actions or plan.ownership != ownership:
        raise NativeCLIError('No fresh supported owned action')


def _native_source(value):
    """Normalize declared native coordinates through 04B's existing validators; never resolve."""
    try:
        if value['source'] in ('git', 'git-subdir'):
            source = _repository({'url': value['url'], 'directory': value.get('path')})
            revision = value.get('sha')
            return replace(source, revision=revision if isinstance(revision, str) and re.fullmatch('[a-f0-9]{40}', revision) else None)
        if value['source'] == 'npm' and value.get('registry') in (None, 'https://registry.npmjs.org', 'https://registry.npmjs.org/'):
            name = value['package']
            if not re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+', name):
                return None
            try:
                return _npm(name, value.get('version'))
            except ValueError:
                return PackageSource('npm', 'npm:' + name)
    except (ValueError, TypeError, KeyError):
        return None
    return None
