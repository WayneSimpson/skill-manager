"""Read-only queries and guarded persistence for OpenCode config-declared agents."""
from __future__ import annotations

from dataclasses import asdict
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from time import time
from typing import Any

from skill_manager.errors import MutationError
from skill_manager.harness import HarnessKernelService
from skill_manager.jsonc import strip_jsonc
from skill_manager.opencode.agents import (
    OpenCodeAgent,
    OpenCodeAgentSource,
    _split_v2_model,
    discover_config_agents,
)
from skill_manager.opencode.jsonc_edit import insert_agent, patch_agent

_ABSENT_HASH = "absent"
_MAX_CATALOG_BYTES = 8 * 1024 * 1024
_AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_V1_VARIANT_KEYS = ("variant", "reasoningEffort")


def _source_payload(source: OpenCodeAgentSource | None) -> dict[str, object] | None:
    if source is None:
        return None
    return {
        "file": source.file,
        "path": str(source.path),
        "format": source.format,
        "isWriteTarget": source.is_write_target,
    }


def _agent_payload(agent: OpenCodeAgent) -> dict[str, object]:
    return {
        "name": agent.name,
        "schemaGeneration": agent.schema_generation,
        "description": agent.description,
        # Canonical instructions concept (V1 prompt / V2 system), with a legacy alias.
        "instructions": agent.instructions,
        "prompt": agent.prompt,
        "model": agent.model,
        "variant": agent.variant,
        "modelRaw": agent.model_raw,
        "mode": agent.mode,
        # Canonical permissions concept (V1 permission / V2 permissions), raw fidelity.
        "permissions": agent.permissions,
        "permission": agent.permission,
        "disabled": agent.disabled,
        "hidden": agent.hidden,
        "color": agent.color,
        "temperature": agent.temperature,
        "topP": agent.top_p,
        "steps": agent.steps,
        "tools": agent.tools,
        "additionalOptions": agent.additional_options or {},
        "source": _source_payload(agent.source),
        "editability": agent.editability,
        "readOnlyReasons": list(agent.read_only_reasons),
        # Truthful: only genuinely non-editable definitions are read-only.
        "readOnly": agent.editability == "read-only",
        "valid": agent.valid,
        "diagnostic": agent.diagnostic,
    }


class AmbiguousOpenCodeAgentError(ValueError):
    """The same agent name is defined in more than one schema generation."""

    def __init__(self, name: str, generations: list[str]):
        self.name = name
        self.generations = generations
        super().__init__(
            f"OpenCode agent {name} is defined with both "
            f"{' and '.join(generations)} syntax; disambiguate with schemaGeneration"
        )


class OpenCodeAgentQueryService:
    """Serves agent state from the shared resolver; performs no writes."""

    def __init__(self, kernel: HarnessKernelService):
        self._kernel = kernel

    def list_agents(self) -> dict[str, object]:
        discovery = discover_config_agents(self._kernel.context)
        return {
            "agents": [_agent_payload(agent) for agent in discovery.agents],
            "writeTarget": str(discovery.write_target),
            "sources": [
                {**asdict(source), "path": str(source.path)} for source in discovery.sources
            ],
            "diagnostics": list(discovery.diagnostics),
            "limitation": discovery.limitation,
        }

    def get_agent(self, name: str, *, schema_generation: str | None = None) -> dict[str, object]:
        if schema_generation is not None and schema_generation not in ("v1", "v2"):
            raise ValueError(
                f"invalid schemaGeneration: {schema_generation}; expected v1 or v2"
            )
        discovery = discover_config_agents(self._kernel.context)
        matches = [agent for agent in discovery.agents if agent.name == name]
        if not matches:
            raise ValueError(f"unknown OpenCode agent: {name}")
        if schema_generation is not None:
            matches = [agent for agent in matches
                       if agent.schema_generation == schema_generation]
            if not matches:
                raise ValueError(
                    f"unknown OpenCode agent: {name} with schemaGeneration {schema_generation}"
                )
        elif len(matches) > 1:
            # Same name in both generations: never silently select one.
            raise AmbiguousOpenCodeAgentError(
                name, [agent.schema_generation for agent in matches]
            )
        return _agent_payload(matches[0])


class AgentFields:
    """Validated editor fields for one agent mutation."""

    def __init__(self, fields: dict[str, Any] | None):
        fields = fields or {}
        self.name = _optional_string(fields.get("name"), "name")
        self.description = _optional_string(fields.get("description"), "description")
        self.instructions = _optional_string(fields.get("instructions"), "instructions")
        self.model = _optional_string(fields.get("model"), "model")
        self.variant = _optional_string(fields.get("variant"), "variant")
        self.mode = _optional_string(fields.get("mode"), "mode")
        self.rename_to = _optional_string(fields.get("renameTo"), "renameTo")
        self._touched = set(fields.keys())

    def touched(self, field: str) -> bool:
        return field in self._touched


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MutationError(f"Agent {label} must be a non-empty string when provided.", 422)
    return value


def _require_generation(generation: str | None) -> str:
    if generation not in ("v1", "v2"):
        raise MutationError(
            f"invalid schemaGeneration: {generation!r}; expected v1 or v2", 400
        )
    return generation


def _section_for(generation: str) -> str:
    return "agent" if generation == "v1" else "agents"


class OpenCodeAgentMutationService:
    """Guarded create/edit persistence; never applies or reloads OpenCode."""

    last_failure_detail: str | None = None

    def __init__(self, kernel: HarnessKernelService, backup_root: Path):
        self._kernel = kernel
        self._backup_root = backup_root

    # ---- context / metadata -------------------------------------------------

    def source_hash(self, path: Path) -> str:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return _ABSENT_HASH
        return hashlib.sha256(raw).hexdigest()

    def editor_context(self) -> dict[str, object]:
        discovery = discover_config_agents(self._kernel.context)
        generations = {agent.schema_generation for agent in discovery.agents}
        if len(generations) == 1:
            decision = {"targetGeneration": next(iter(generations)),
                        "requiresGenerationChoice": False}
        else:
            decision = {"targetGeneration": None,
                        "requiresGenerationChoice": True,
                        "reason": "supported syntax present: " + ", ".join(sorted(generations))
                                  if generations else
                                  "no agent or agents section exists yet"}
        return {
            "writeTarget": str(self._write_target()),
            "create": decision,
            "sourceHash": self.source_hash(self._write_target()),
            "readOnly": False,
        }

    def variant_options(self, model: str) -> list[str]:
        """Best-effort authoritative variant list from OpenCode's model catalog."""
        if not isinstance(model, str) or not model.strip():
            return []
        base = model.split("#", 1)[0]
        provider, _, model_id = base.partition("/")
        if not provider or not model_id:
            return []
        catalog_path = self._kernel.context.env.get("XDG_CACHE_HOME") or \
            str(Path.home() / ".cache")
        catalog = Path(catalog_path) / "opencode" / "models.json"
        try:
            if not catalog.is_file() or catalog.stat().st_size > _MAX_CATALOG_BYTES:
                return []
            data = json.loads(catalog.read_text(encoding="utf-8"))
            models = (data.get(provider) or {}).get("models") or {}
            entry = models.get(model_id)
            if not isinstance(entry, dict):
                return []
            for option in entry.get("reasoning_options") or []:
                if isinstance(option, dict) and option.get("type") == "effort" \
                        and isinstance(option.get("values"), list):
                    return [str(value) for value in option["values"] if value]
        except (OSError, ValueError, UnicodeError, AttributeError):
            return []
        return []

    # ---- preview ------------------------------------------------------------

    def preview_create(self, generation: str, fields: dict[str, Any]) -> dict[str, object]:
        generation = _require_generation(generation)
        parsed = AgentFields(fields)
        definition = self._build_definition(generation, {}, parsed, is_create=True)
        return {
            "mode": "create",
            "generation": generation,
            "targetFile": str(self._write_target()),
            "sourceHash": self.source_hash(self._write_target()),
            "old": None,
            "new": self._public_definition(generation, definition),
            "textDiff": self._diff(None, definition),
        }

    def preview_update(
        self, name: str, generation: str | None, fields: dict[str, Any]
    ) -> dict[str, object]:
        agent, raw_definition = self._resolve_for_edit(name, generation)
        parsed = AgentFields(fields)
        new_name = parsed.rename_to or agent.name
        definition = self._build_definition(
            agent.schema_generation, raw_definition, parsed, is_create=False)
        return {
            "mode": "update",
            "generation": agent.schema_generation,
            "targetFile": str(self._declaring_file(agent)),
            "sourceHash": self.source_hash(self._declaring_file(agent)),
            "old": self._public_definition(agent.schema_generation, raw_definition),
            "new": self._public_definition(agent.schema_generation, definition),
            "renameTo": new_name if new_name != agent.name else None,
            "textDiff": self._diff(raw_definition, definition),
        }

    # ---- persistence ----------------------------------------------------------

    def create_agent(
        self, generation: str, fields: dict[str, Any], *, expected_hash: str
    ) -> dict[str, object]:
        generation = _require_generation(generation)
        parsed = AgentFields(fields)
        if parsed.mode not in (None, "subagent"):
            raise MutationError(
                "New agents must be created as sub-agents; mode 'subagent' is applied "
                "explicitly and cannot be overridden at creation.", 422)
        if not parsed.name or not _AGENT_NAME.fullmatch(parsed.name):
            raise MutationError("A valid agent name is required to create a sub-agent.", 422)
        self._block_collision(parsed.name)

        target = self._write_target()
        raw = self._read_source(target)
        definition = self._build_definition(generation, {}, parsed, is_create=True)
        candidate = insert_agent(_section_for(generation), parsed.name, definition)(raw)
        result = self._persist(target, raw, candidate, expected_hash, parsed.name, definition)
        discovery = discover_config_agents(self._kernel.context)
        saved = next(a for a in discovery.agents
                     if a.name == parsed.name and a.schema_generation == generation)
        return {**result, "agent": _agent_payload(saved)}

    def update_agent(
        self, name: str, generation: str | None, fields: dict[str, Any], *, expected_hash: str
    ) -> dict[str, object]:
        agent, raw_definition = self._resolve_for_edit(name, generation)
        parsed = AgentFields(fields)
        new_name = parsed.rename_to or agent.name
        if new_name != agent.name:
            if not _AGENT_NAME.fullmatch(new_name):
                raise MutationError("The requested agent name is not valid.", 422)
            self._block_collision(new_name, excluding=(name,))
        definition = self._build_definition(
            agent.schema_generation, raw_definition, parsed, is_create=False)

        if new_name == agent.name and definition == raw_definition:
            return {"agent": _agent_payload(agent), "changed": False, "pendingApply": False,
                    "backup": None}

        target = self._declaring_file(agent)
        raw = self._read_source(target)
        section = _section_for(agent.schema_generation)
        candidate = patch_agent(section, name, definition, rename_to=new_name)(raw)
        result = self._persist(target, raw, candidate, expected_hash, new_name, definition)
        discovery = discover_config_agents(self._kernel.context)
        saved = next(a for a in discovery.agents
                     if a.name == new_name and a.schema_generation == agent.schema_generation)
        return {**result, "agent": _agent_payload(saved)}

    # ---- internals -------------------------------------------------------------

    def _write_target(self) -> Path:
        from skill_manager.opencode.resolver import opencode_write_config_path

        return opencode_write_config_path(self._kernel.context)

    def _declaring_file(self, agent: OpenCodeAgent) -> Path:
        if agent.source is None:
            raise MutationError(
                "The declaring configuration file for this agent could not be resolved.", 409)
        return agent.source.path

    def _resolve_for_edit(
        self, name: str, generation: str | None
    ) -> tuple[OpenCodeAgent, dict[str, Any]]:
        discovery = discover_config_agents(self._kernel.context)
        matches = [agent for agent in discovery.agents if agent.name == name]
        if not matches:
            raise MutationError(f"unknown OpenCode agent: {name}", 404)
        if generation is not None:
            _require_generation(generation)
            matches = [a for a in matches if a.schema_generation == generation]
            if not matches:
                raise MutationError(
                    f"unknown OpenCode agent: {name} with schemaGeneration {generation}", 404)
        elif len(matches) > 1:
            raise MutationError(
                f"OpenCode agent {name} is defined with both v1 and v2 syntax; "
                "select schemaGeneration to edit one definition.", 409)
        agent = matches[0]
        if not agent.valid:
            raise MutationError(
                "This agent definition is not an object and cannot be safely edited.", 409)
        raw = self._raw_definition(agent)
        if raw is None:
            raise MutationError(
                "This agent definition could not be read back from its source file.", 409)
        return agent, raw

    def _raw_definition(self, agent: OpenCodeAgent) -> dict[str, Any] | None:
        target = self._declaring_file(agent)
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        try:
            data = json.loads(strip_jsonc(text))
        except ValueError:
            return None
        section = data.get(_section_for(agent.schema_generation))
        candidate = section.get(agent.name) if isinstance(section, dict) else None
        return candidate if isinstance(candidate, dict) else None

    def _block_collision(self, name: str, excluding: tuple[str, ...] = ()) -> None:
        discovery = discover_config_agents(self._kernel.context)
        for agent in discovery.agents:
            if agent.name == name and agent.name not in excluding:
                raise MutationError(
                    f"An OpenCode agent named {name} already exists "
                    f"({_section_for(agent.schema_generation)} section).", 409)

    @staticmethod
    def _read_source(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "{}"

    def _build_definition(
        self, generation: str, raw: dict[str, Any], fields: AgentFields, *, is_create: bool
    ) -> dict[str, Any]:
        definition = dict(raw)  # Unknown keys and ordering are preserved verbatim.

        def set_known(key: str, value: Any) -> None:
            if key in definition or value is not None:
                definition[key] = value
            if value is None and key in definition:
                del definition[key]

        if fields.touched("description"):
            set_known("description", fields.description)
        if fields.touched("instructions"):
            set_known("system" if generation == "v2" else "prompt", fields.instructions)
        if fields.touched("mode"):
            definition["mode"] = fields.mode or "subagent"
        if is_create:
            definition["mode"] = "subagent"  # Always explicit; never a harness default.

        if fields.touched("model") or fields.touched("variant"):
            if generation == "v2":
                current = definition.get("model")
                if isinstance(current, dict):
                    if fields.touched("model") and not fields.model:
                        # Clearing the model removes the whole structured
                        # selection; variant handling must not resurrect a fragment.
                        del definition["model"]
                    else:
                        updated = dict(current)
                        if fields.touched("model") and fields.model:
                            updated["model"] = fields.model.split("/")[-1].split("#")[0]
                            provider = fields.model.split("/", 1)[0]
                            if provider and "/" in fields.model:
                                updated["providerID"] = provider
                        if fields.touched("variant"):
                            if fields.variant:
                                updated["variant"] = fields.variant
                            else:
                                updated.pop("variant", None)
                        definition["model"] = updated
                else:
                    base = fields.model if fields.touched("model") else (
                        (current or "").split("#", 1)[0] if isinstance(current, str) else None)
                    variant = fields.variant if fields.touched("variant") else (
                        (current or "").split("#", 1)[1] if isinstance(current, str)
                        and "#" in current else None)
                    if base is None and variant is None:
                        definition.pop("model", None)
                    elif base:
                        definition["model"] = f"{base}#{variant}" if variant else base
                    else:
                        definition["model"] = f"unknown#{variant}"
            else:
                if fields.touched("model"):
                    set_known("model", fields.model)
                if fields.touched("variant"):
                    existing = next((k for k in _V1_VARIANT_KEYS if k in definition), None)
                    if fields.variant:
                        definition[existing or "variant"] = fields.variant
                    elif existing:
                        del definition[existing]
        return definition

    @staticmethod
    def _public_definition(generation: str, definition: dict[str, Any]) -> dict[str, Any]:
        return {"schemaGeneration": generation, **definition}

    @staticmethod
    def _diff(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
        old_text = json.dumps(old, indent=2, ensure_ascii=False) if old is not None else ""
        new_text = json.dumps(new, indent=2, ensure_ascii=False)
        return list(difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile="current", tofile="proposed", lineterm=""))

    def _persist(
        self, target: Path, raw: str, candidate: str, expected_hash: str,
        agent_name: str, definition: dict[str, Any],
    ) -> dict[str, object]:
        # Validate the complete candidate before touching the source.
        parsed_candidate = self._validate_candidate(candidate)

        current_hash = self.source_hash(target)
        if current_hash != expected_hash:
            raise MutationError(
                "The configuration changed concurrently; re-open this agent and retry. "
                "Nothing was written.", 409)

        changed = candidate != raw
        backup = None
        if changed:
            backup = self._write_backup(target, raw, agent_name)
        backup_payload = {"file": backup.name, "path": str(backup)} if backup else None
        try:
            self._atomic_write(target, candidate)
        except OSError as error:
            self.last_failure_detail = f"atomic write failed: {error}"
            raise MutationError(
                "Saving the configuration failed before anything was replaced; "
                "the original file is unchanged.", 500) from error

        if not self._verify_readback(target, parsed_candidate):
            restored = self._restore_original(target, raw)
            if restored:
                raise MutationError(
                    "The saved configuration could not be verified after writing; "
                    "the original configuration was rolled back automatically.", 500)
            backup_note = f" A backup is available at {backup_payload['path']}." if backup_payload else ""
            self.last_failure_detail = "readback verification failed; restore failed" + backup_note
            raise MutationError(
                "The saved configuration could not be verified and automatic restore "
                "also failed." + backup_note, 500)

        return {
            "changed": changed,
            "pendingApply": changed,  # Saved config is never applied by this service.
            "backup": backup_payload,
        }

    @staticmethod
    def _validate_candidate(candidate: str) -> dict[str, Any]:
        try:
            parsed = json.loads(strip_jsonc(candidate))
            if not isinstance(parsed, dict):
                raise ValueError("root is not an object")
            return parsed
        except ValueError as error:
            raise MutationError(
                f"The proposed configuration failed validation and was not written: {error}",
                422) from error

    def _write_backup(self, target: Path, raw: str, agent_name: str) -> Path:
        self._backup_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._backup_root, 0o700)
        suffix = target.suffix or ".jsonc"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        path = self._backup_root / f"{agent_name}-{int(time())}-{digest}{suffix}"
        path.write_text(raw, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        existed = path.exists()
        original_mode = stat.S_IMODE(path.stat().st_mode) if existed else None
        temporary = path.with_name(f".{path.name}.skill-manager.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, original_mode or 0o600)
        os.replace(temporary, path)

    def _verify_readback(self, target: Path, expected: dict[str, Any]) -> bool:
        try:
            read_back = json.loads(strip_jsonc(target.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError):
            return False
        return read_back == expected

    def _restore_original(self, target: Path, raw: str) -> bool:
        try:
            self._atomic_write(target, raw)
        except OSError:
            return False
        return self._verify_readback_text(target, raw)

    @staticmethod
    def _verify_readback_text(target: Path, raw: str) -> bool:
        try:
            return target.read_text(encoding="utf-8") == raw
        except (OSError, UnicodeError):
            return False
