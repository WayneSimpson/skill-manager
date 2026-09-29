"""Read-only queries over OpenCode config-declared agents."""
from __future__ import annotations

from dataclasses import asdict

from skill_manager.harness import HarnessKernelService
from skill_manager.opencode.agents import OpenCodeAgent, OpenCodeAgentSource, discover_config_agents


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
        "readOnly": True,  # Task 11 exposes no mutation controls at all.
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
