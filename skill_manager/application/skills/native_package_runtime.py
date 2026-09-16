"""Build native package adapters from backend-owned runtime configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
from tempfile import TemporaryDirectory

from skill_manager.harness import ConfigSubtreeBindingProfile, FileTreeBindingProfile, HarnessKernelService

from .native_package_cli import (
    ClaudeNativePackageAdapter,
    CodexNativePackageAdapter,
    NativeCLIError,
    OpenCodeNativePackageAdapter,
    ReadOnlyNativePackageAdapter,
)
from .package_deployment_service import NativePackageAdapter
from .package_deployment import NativeTarget, read_opencode_registrations
from skill_manager.opencode.resolver import opencode_config_paths


PACKAGE_DEPLOYMENT_HARNESSES = ("claude", "codex", "cursor", "opencode")

@dataclass(frozen=True)
class NativePackageRuntimeConfig:
    harness: str
    root: Path
    executable: Path
    timeout_seconds: float = 30.0


class NativePackageAdapterFactory:
    """Create a fresh, server-configured adapter for each request."""

    def __init__(self, kernel: HarnessKernelService, env: dict[str, str]) -> None:
        self.kernel = kernel
        self.env = dict(env)

    def __call__(self, harness: str) -> NativePackageAdapter:
        if harness == 'opencode':
            configured_root = self.env.get(_key(harness, "ROOT"))
            if configured_root:
                try:
                    root = _explicit_root(configured_root)
                except ValueError:
                    return _OpenCodeReadOnlyAdapter(self._default_root(harness), opencode_config_paths(self.kernel.context))
                config_paths = _opencode_config_paths(root)
            else:
                root = self._default_root(harness)
                config_paths = (root / 'config.json', *opencode_config_paths(self.kernel.context))
            config = self._config(harness, root)
            if config is None:
                return _OpenCodeReadOnlyAdapter(root, config_paths)
            adapter = OpenCodeNativePackageAdapter(
                config.root,
                _BoundedNativeRunner(config),
                config_paths=config_paths,
                mechanism_available=True,
            )
            adapter.executable = str(config.executable)
            return adapter
        configured_root = self.env.get(_key(harness, "ROOT"))
        if configured_root:
            try:
                root = _explicit_root(configured_root)
            except ValueError:
                return ReadOnlyNativePackageAdapter(harness, self._default_root(harness))
        else:
            root = self._default_root(harness)
        config = self._config(harness, root)
        if config is None:
            return ReadOnlyNativePackageAdapter(harness, root)

        runner = _BoundedNativeRunner(config)
        if harness == "claude":
            adapter = ClaudeNativePackageAdapter(config.root, runner, mechanism_available=True)
        elif harness == "codex":
            adapter = CodexNativePackageAdapter(config.root, runner, mechanism_available=True)
        else:
            return ReadOnlyNativePackageAdapter(harness, root)
        adapter.executable = str(config.executable)
        return adapter

    def diagnostics(self, harness: str) -> tuple[str, ...]:
        """Explain why a harness is read-only without exposing configuration."""
        values: list[str] = []
        configured_root = self.env.get(_key(harness, "ROOT"))
        if configured_root:
            try:
                root = _explicit_root(configured_root)
            except ValueError:
                values.append("native-root-is-not-safe")
                root = None
        else:
            try:
                root = self._default_root(harness)
            except (OSError, RuntimeError, ValueError):
                root = None
                values.append("native-root-unavailable")
        if harness not in PACKAGE_DEPLOYMENT_HARNESSES:
            values.append("unsupported-harness")
        elif harness not in {"claude", "codex", "opencode"}:
            values.append("native-mutation-not-enabled-for-harness")
        elif root is not None:
            if not self.env.get(_key(harness, "EXECUTABLE")):
                values.append("native-executable-not-configured")
            else:
                try:
                    _explicit_executable(self.env[_key(harness, "EXECUTABLE")])
                except ValueError:
                    values.append("native-executable-is-not-a-safe-file")
            if self.env.get(_key(harness, "ALLOW_MUTATION"), "").casefold() != "true":
                values.append("native-mutation-policy-not-enabled")
        return tuple(dict.fromkeys(values))

    def _config(self, harness: str, root: Path) -> NativePackageRuntimeConfig | None:
        if harness not in {"claude", "codex", "opencode"}:
            return None
        if self.env.get(_key(harness, "ALLOW_MUTATION"), "").casefold() != "true":
            return None
        value = self.env.get(_key(harness, "EXECUTABLE"))
        if not value:
            return None
        try:
            executable = _explicit_executable(value)
        except ValueError:
            return None
        return NativePackageRuntimeConfig(
            harness=harness,
            root=root,
            executable=executable,
        )

    def _default_root(self, harness: str) -> Path:
        definition = self.kernel.definition(harness)
        if definition is None:
            raise ValueError(f"unknown harness: {harness}")
        if harness == "claude":
            binding = definition.binding_for("skills")
            if isinstance(binding, FileTreeBindingProfile):
                return _explicit_root(binding.resolve_managed_root(self.kernel.context).parent)
        if harness == "codex":
            binding = definition.binding_for("mcp")
            if isinstance(binding, ConfigSubtreeBindingProfile):
                return _explicit_root(binding.resolve_config_path(self.kernel.context).parent)
        binding = definition.binding_for("mcp")
        if isinstance(binding, ConfigSubtreeBindingProfile):
            return _explicit_root(binding.resolve_config_path(self.kernel.context).parent)
        binding = definition.binding_for("skills")
        if isinstance(binding, FileTreeBindingProfile):
            return _explicit_root(binding.resolve_managed_root(self.kernel.context).parent)
        raise ValueError(f"harness has no package runtime root: {harness}")


class _OpenCodeReadOnlyAdapter(ReadOnlyNativePackageAdapter):
    def __init__(self, root, paths):
        super().__init__('opencode', root)
        self.paths = paths

    def inspect(self, package_id, deployment=None):
        # All known declarations are evidence; this is not complete runtime inventory.
        return NativeTarget('opencode', self.root, read_opencode_registrations(self.paths),
                            mechanism_available=False)


class _BoundedNativeRunner:
    """Invoke one verified native binary with an explicit, bounded environment."""

    def __init__(self, config: NativePackageRuntimeConfig) -> None:
        self.config = config
        self._environment = {
            "PATH": os.pathsep.join((str(config.executable.parent), "/usr/bin", "/bin")),
            "LANG": "C.UTF-8",
        }

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        if not command or command[0] != str(self.config.executable):
            raise NativeCLIError("native CLI command must use the configured executable")
        return self._run(command)

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            _explicit_executable(str(self.config.executable))
        except ValueError as error:
            raise NativeCLIError("configured native executable is no longer safe") from error
        with TemporaryDirectory(prefix="skill-manager-native-run-") as directory:
            runtime_root = Path(directory)
            paths = {
                "HOME": runtime_root / "home",
                "USERPROFILE": runtime_root / "home",
                "XDG_CONFIG_HOME": runtime_root / "config",
                "XDG_DATA_HOME": runtime_root / "data",
                "XDG_CACHE_HOME": runtime_root / "cache",
                "XDG_STATE_HOME": runtime_root / "state",
                "TMPDIR": runtime_root / "tmp",
                "TMP": runtime_root / "tmp",
                "TEMP": runtime_root / "tmp",
            }
            for path in set(paths.values()) | {runtime_root / "workspace"}:
                path.mkdir(parents=True, exist_ok=True)
            environment = {key: str(value) for key, value in paths.items()}
            environment.update(self._environment)
            if self.config.harness == "claude":
                environment["CLAUDE_CONFIG_DIR"] = str(self.config.root)
            elif self.config.harness == "codex":
                environment["CODEX_HOME"] = str(self.config.root)
            elif self.config.harness == "opencode":
                # OpenCode's global plugin command follows this config root;
                # debug info verification proves the binding used by the CLI.
                environment["XDG_CONFIG_HOME"] = str(self.config.root.parent)
                environment["OPENCODE_CONFIG_DIR"] = str(self.config.root)
                environment['OPENCODE_DISABLE_MODELS_FETCH'] = 'true'
                environment['OPENCODE_DISABLE_AUTOUPDATE'] = 'true'
            return subprocess.run(
                command,
                cwd=str(runtime_root / "workspace"),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=self.config.timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )


def _key(harness: str, field: str) -> str:
    return f"SKILL_MANAGER_NATIVE_{harness.upper()}_{field}"


def _explicit_root(value: Path | str) -> Path:
    root = Path(value)
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("native adapter root must be an explicit bounded absolute path")
    if root.exists() and not root.is_dir():
        raise ValueError("native adapter root must be a directory")
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("native adapter root may not contain symlinks")
    return root


def _explicit_executable(value: str) -> Path:
    executable = Path(value)
    if not executable.is_absolute() or executable == Path(executable.anchor):
        raise ValueError("native executable must be an explicit absolute path")
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("native executable must be a regular non-linked file")
    if executable.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0:
        raise ValueError("native executable must be executable")
    if any(parent.is_symlink() for parent in (executable, *executable.parents)):
        raise ValueError("native executable path may not contain symlinks")
    try:
        with executable.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise ValueError("native executable must be a raw ELF binary")
    except OSError as error:
        raise ValueError("native executable could not be inspected") from error
    return executable


def _opencode_config_paths(root: Path) -> tuple[Path, ...]:
    return root / "config.json", root / "opencode.json", root / "opencode.jsonc"


__all__ = ["PACKAGE_DEPLOYMENT_HARNESSES", "NativePackageAdapterFactory", "NativePackageRuntimeConfig"]
