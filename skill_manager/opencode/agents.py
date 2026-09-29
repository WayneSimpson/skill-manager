"""Read-only discovery of OpenCode agents declared in shared JSON/JSONC config."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from skill_manager.harness.resolution import ResolutionContext
from skill_manager.opencode.resolver import (
    OpenCodeConfigSource,
    STATIC_ONLY_LIMITATION,
    opencode_write_config_path,
    resolve_opencode_config,
)

MAX_AGENTS = 500

SchemaGeneration = Literal["v1", "v2"]
AgentEditability = Literal["config", "read-only"]

# V1 "agent" section keys with typed extraction; anything else is preserved raw.
_V1_TYPED_FIELDS = {
    "description": str,
    "prompt": str,
    "model": str,
    "mode": str,
    "temperature": float,
    "top_p": float,
    "steps": int,
    "disable": bool,
    "hidden": bool,
    "color": str,
}
_V1_RAW_FIELDS = ("permission", "tools")
# V1 keys that carry a model variant/reasoning choice without being in the docs'
# typed option set; surfaced first-class AND kept raw for lossless round-trips.
_V1_VARIANT_KEYS = ("variant", "reasoningEffort")
# V2 "agents" section keys per current V2 documentation. temperature/top_p/tools
# are explicitly legacy in V2, so they stay in additional options there.
_V2_TYPED_FIELDS = {
    "description": str,
    "system": str,
    "mode": str,
    "steps": int,
    "disabled": bool,
    "hidden": bool,
    "color": str,
}


@dataclass(frozen=True)
class OpenCodeAgentSource:
    file: str
    path: Path
    format: str
    is_write_target: bool


@dataclass(frozen=True)
class OpenCodeAgent:
    name: str
    schema_generation: SchemaGeneration
    description: str | None = None
    instructions: str | None = None
    model: str | None = None
    variant: str | None = None
    model_raw: Any = None
    mode: str | None = None
    permissions: dict[str, Any] | list[Any] | None = None
    disabled: bool | None = None
    hidden: bool | None = None
    color: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    steps: int | None = None
    tools: dict[str, Any] | None = None
    additional_options: dict[str, Any] = field(default_factory=dict)
    source: OpenCodeAgentSource | None = None
    editability: AgentEditability = "read-only"
    read_only_reasons: tuple[str, ...] = ()
    valid: bool = True
    diagnostic: str | None = None

    @property
    def prompt(self) -> str | None:
        """Legacy alias for the normalised instructions/system value."""
        return self.instructions

    @property
    def permission(self) -> dict[str, Any] | list[Any] | None:
        """Legacy alias for the normalised permissions value."""
        return self.permissions


@dataclass(frozen=True)
class OpenCodeAgentDiscovery:
    agents: tuple[OpenCodeAgent, ...]
    write_target: Path
    sources: tuple[OpenCodeConfigSource, ...]
    diagnostics: tuple[str, ...]
    limitation: str = (
        STATIC_ONLY_LIMITATION
        + " Markdown-defined agents and plugin-provided agents are not discovered."
    )


def _matches_type(value: Any, expected: type) -> bool:
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def _split_v2_model(value: Any) -> tuple[str | None, str | None, Any]:
    """Normalise V2 model selection without guessing undocumented shapes."""
    if isinstance(value, str):
        base, separator, variant = value.partition("#")
        return (base or None, variant or None, value) if separator or base else (None, None, value)
    if isinstance(value, dict) and isinstance(value.get("providerID"), str) \
            and isinstance(value.get("model"), str):
        provider_model = f'{value["providerID"]}/{value["model"]}'
        variant = value.get("variant")
        return provider_model, variant if isinstance(variant, str) else None, value
    return None, None, value  # Unconfirmed shape: preserve verbatim, normalise nothing.


def _build_agent(
    *,
    name: str,
    generation: SchemaGeneration,
    definition: dict[str, Any],
    source: OpenCodeAgentSource | None,
) -> OpenCodeAgent:
    known: dict[str, Any] = {}
    additional: dict[str, Any] = {}
    variant: str | None = None

    for key, value in definition.items():
        if generation == "v1":
            expected = _V1_TYPED_FIELDS.get(key)
            if expected is not None and _matches_type(value, expected):
                known["disabled" if key == "disable" else key] = value
                continue
            if key == "permission" and isinstance(value, dict):
                known["permissions"] = value
                continue
            if key == "tools" and isinstance(value, dict):
                known["tools"] = value
                continue
            if key in _V1_VARIANT_KEYS:
                if variant is None and isinstance(value, str):
                    variant = value
                additional[key] = value  # Raw value stays for lossless preservation.
                continue
        else:
            expected = _V2_TYPED_FIELDS.get(key)
            if expected is not None and _matches_type(value, expected):
                known["instructions" if key == "system" else key] = value
                continue
            if key == "permissions" and isinstance(value, list):
                known["permissions"] = value
                continue
        additional[key] = value

    model_base, model_variant, model_raw = None, None, None
    raw_model = definition.get("model")
    if generation == "v2":
        model_base, model_variant, model_raw = _split_v2_model(raw_model)
        additional.pop("model", None)
    elif isinstance(raw_model, str):
        model_base, model_raw = raw_model, raw_model
        additional.pop("model", None)

    reasons: list[str] = []
    if source is not None and not source.is_write_target:
        reasons.append("declared-outside-selected-config-source")

    return OpenCodeAgent(
        name=name,
        schema_generation=generation,
        description=known.get("description"),
        instructions=known.get("instructions") or known.get("prompt"),
        model=model_base,
        variant=model_variant or variant,
        model_raw=model_raw,
        mode=known.get("mode"),
        permissions=known.get("permissions"),
        disabled=known.get("disabled"),
        hidden=known.get("hidden"),
        color=known.get("color"),
        temperature=known.get("temperature"),
        top_p=known.get("top_p"),
        steps=known.get("steps"),
        tools=known.get("tools"),
        additional_options=additional,
        source=source,
        editability="config" if not reasons else "read-only",
        read_only_reasons=tuple(reasons),
    )


def _section_entries(config: dict[str, Any], section: str, diagnostics: list[str]) -> dict[str, Any]:
    value = config.get(section)
    if value is None:
        return {}
    if not isinstance(value, dict):
        diagnostics.append(f"OpenCode {section} section is not an object")
        return {}
    return value


def discover_config_agents(context: ResolutionContext) -> OpenCodeAgentDiscovery:
    """Read V1 `agent` and V2 `agents` config sections through the shared resolver."""
    resolution = resolve_opencode_config(context)
    write_target = opencode_write_config_path(context)
    diagnostics: list[str] = []

    v1_section = _section_entries(resolution.config, "agent", diagnostics)
    v2_section = _section_entries(resolution.config, "agents", diagnostics)
    ambiguous = sorted(set(v1_section) & set(v2_section))
    if ambiguous:
        # Authoritative precedence for coexisting sections is not documented:
        # preserve both definitions read-only instead of guessing a winner.
        diagnostics.append(
            "OpenCode agents defined in both agent and agents sections: "
            f"{', '.join(ambiguous)} (shown read-only)"
        )

    by_path = {source.path: source for source in resolution.sources}
    agents: list[OpenCodeAgent] = []
    for generation, section_name, section in (
        ("v1", "agent", v1_section),
        ("v2", "agents", v2_section),
    ):
        for name in section:
            declared_at = resolution.entry_sources.get((section_name, name))
            source_spec = by_path.get(declared_at)
            source = (
                OpenCodeAgentSource(
                    file=source_spec.name,
                    path=source_spec.path,
                    format=source_spec.format,
                    is_write_target=source_spec.path == write_target,
                )
                if source_spec is not None
                else None
            )
            definition = section.get(name)
            if not isinstance(definition, dict):
                agents.append(OpenCodeAgent(
                    name=name,
                    schema_generation=generation,
                    additional_options={},
                    source=source,
                    editability="read-only",
                    read_only_reasons=(
                        ("defined-in-both-v1-and-v2-sections", "definition-not-an-object")
                        if name in ambiguous else ("definition-not-an-object",)
                    ),
                    valid=False,
                    diagnostic="Agent definition is not an object",
                ))
            else:
                agent = _build_agent(name=name, generation=generation,
                                     definition=definition, source=source)
                if name in ambiguous:
                    agent = _with_reasons(agent, ("defined-in-both-v1-and-v2-sections",))
                agents.append(agent)
            if len(agents) >= MAX_AGENTS:
                diagnostics.append(f"OpenCode agent listing stopped at {MAX_AGENTS} entries")
                break

    return OpenCodeAgentDiscovery(
        agents=tuple(agents),
        write_target=write_target,
        sources=resolution.sources,
        diagnostics=(*resolution.diagnostics, *diagnostics),
    )


def _with_reasons(agent: OpenCodeAgent, reasons: tuple[str, ...]) -> OpenCodeAgent:
    from dataclasses import replace

    return replace(
        agent,
        editability="read-only",
        read_only_reasons=tuple(dict.fromkeys((*agent.read_only_reasons, *reasons))),
    )
