"""Explicit apply lifecycle: capability detection, managed-runtime ownership, pending state."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Literal, Protocol, runtime_checkable

ApplyMechanism = Literal["reload", "restart-managed", "restart-manual", "unavailable"]

# Exact top-level command names that would constitute a supported config reload.
# Membership is checked against the authoritative CLI command registry, never
# against bare exit codes (unknown subcommands fall through to the default
# command and exit zero, which would otherwise fake availability).
RELOAD_COMMANDS: tuple[str, ...] = ("reload", "config-reload")

_COMMAND_LINE = re.compile(r"^\s{2,}opencode\s+(?P<name>[a-z][a-z0-9-]*)\b")


def parse_cli_commands(help_text: str) -> frozenset[str]:
    """Parse the authoritative top-level command list from `opencode --help`."""
    names = []
    in_commands = False
    for line in help_text.splitlines():
        if line.strip().startswith("Commands:"):
            in_commands = True
            continue
        if in_commands:
            if line and not line.startswith(" "):
                break  # Registry section ended (Positionals:, Options:, ...).
            match = _COMMAND_LINE.match(line)
            if match:
                names.append(match.group("name"))
    return frozenset(names)


@dataclass(frozen=True)
class ApplyCapability:
    mechanism: ApplyMechanism
    reload_available: bool
    managed_runtime: bool
    detail: str


@runtime_checkable
class ManagedOpenCodeController(Protocol):
    """Restarts only processes its own implementation created and owns."""

    def owned_handles(self) -> tuple[Any, ...]: ...

    def restart(self, handle: Any) -> bool: ...

    def verify_active(self, handle: Any, config_hash: str) -> bool: ...


class ManagedRuntimeRegistry:
    """Ownership boundary for automated restarts; empty means unmanaged."""

    def __init__(self) -> None:
        self._controllers: list[ManagedOpenCodeController] = []

    def register(self, controller: ManagedOpenCodeController) -> None:
        self._controllers.append(controller)

    def handles(self) -> tuple[Any, ...]:
        return tuple(
            handle for controller in self._controllers for handle in controller.owned_handles()
        )

    def restart(self, handle: Any) -> bool:
        for controller in self._controllers:
            if handle in controller.owned_handles():
                return controller.restart(handle)
        raise PermissionError("process handle is not owned by any registered controller")

    def verify_active(self, handle: Any, config_hash: str) -> bool:
        for controller in self._controllers:
            if handle in controller.owned_handles():
                return controller.verify_active(handle, config_hash)
        return False


class OpenCodeCapabilityDetector:
    """Detects the safest apply mechanism from authoritative runtime capability."""

    def __init__(
        self,
        *,
        command_registry: Callable[[], frozenset[str]] | None = None,
        managed_registry: ManagedRuntimeRegistry | None = None,
    ):
        self._command_registry = command_registry
        self._managed_registry = managed_registry or ManagedRuntimeRegistry()

    def detect(self) -> ApplyCapability:
        if self._command_registry is None:
            return ApplyCapability(
                mechanism="restart-manual",
                reload_available=False,
                managed_runtime=False,
                detail=(
                    "Runtime reload capability not probed: OpenCode loads config-time files at "
                    "startup and does not hot-reload them; restart is required."
                ),
            )
        registry = self._command_registry()
        reload_command = next((name for name in RELOAD_COMMANDS if name in registry), None)
        if reload_command is not None:
            return ApplyCapability(
                mechanism="reload",
                reload_available=True,
                managed_runtime=False,
                detail=f"Runtime exposes the '{reload_command}' command.",
            )
        if self._managed_registry.handles():
            return ApplyCapability(
                mechanism="restart-managed",
                reload_available=False,
                managed_runtime=True,
                detail=(
                    "No reload command exists; a Skill Manager-owned managed runtime can be "
                    "restarted with explicit confirmation."
                ),
            )
        return ApplyCapability(
            mechanism="restart-manual",
            reload_available=False,
            managed_runtime=False,
            detail=(
                "No reload command exists and no Skill Manager-owned runtime is registered; "
                "restart OpenCode manually to apply saved changes."
            ),
        )


class AgentApplyStateStore:
    """Durable saved-vs-applied state so pending-apply survives refresh."""

    def __init__(self, path: Path):
        self._path = path

    def status(self, target: str) -> dict[str, object]:
        return _entry_status(self._load(), target)

    def all_targets(self) -> dict[str, dict[str, object]]:
        """Per-target state from one loaded snapshot (single read, no races)."""
        data = self._load()
        return {target: _entry_status(data, target) for target in data.get("targets", {})}

    def pending_targets(self) -> dict[str, str]:
        """Map of every pending target path to its stored saved hash."""
        return {
            target: status["savedHash"]
            for target, status in self.all_targets().items()
            if status["pending"]
        }

    def mark_saved(self, target: str, saved_hash: str) -> None:
        data = self._load()
        entry = data.setdefault("targets", {}).setdefault(target, {})
        entry["savedHash"] = saved_hash
        self._write(data)

    def mark_applied(self, target: str, applied_hash: str) -> None:
        data = self._load()
        entry = data.setdefault("targets", {}).setdefault(target, {})
        entry["appliedHash"] = applied_hash
        self._write(data)

    def _load(self) -> dict[str, Any]:
        # FileNotFoundError is the legitimate empty initial state. Anything else
        # (unreadable, invalid JSON, wrong root/version/schema) is a real
        # state-store failure that must never masquerade as "nothing pending".
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": 1, "targets": {}}
        except OSError as error:
            raise AgentApplyStateError(
                "The saved agent apply-state file exists but could not be read; "
                "pending state is unavailable until it is inspected."
            ) from error
        try:
            data = json.loads(text)
        except ValueError as error:
            raise AgentApplyStateError(
                "The saved agent apply-state file is not valid JSON; pending state "
                "is unavailable until it is inspected."
            ) from error
        if not isinstance(data, dict) or data.get("version") != 1 \
                or not isinstance(data.get("targets"), dict):
            raise AgentApplyStateError(
                "The saved agent apply-state file has an unsupported format; "
                "pending state is unavailable until it is inspected."
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Private mode is applied to the temporary file BEFORE replacement, so
        # any pre-replace failure (including chmod) leaves the prior state intact.
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        temporary_path = Path(temporary)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


def _entry_status(data: dict[str, Any], target: str) -> dict[str, object]:
    entry = data.get("targets", {}).get(target, {})
    saved = entry.get("savedHash")
    applied = entry.get("appliedHash")
    return {
        "pending": saved is not None and saved != applied,
        "savedHash": saved,
        "appliedHash": applied,
    }


class AgentApplyStateError(RuntimeError):
    """The durable apply-state store is unusable; never pretend nothing is pending."""


__all__ = [
    "AgentApplyStateError",
    "AgentApplyStateStore",
    "ApplyCapability",
    "ApplyMechanism",
    "ManagedOpenCodeController",
    "ManagedRuntimeRegistry",
    "OpenCodeCapabilityDetector",
    "RELOAD_COMMANDS",
    "parse_cli_commands",
]
