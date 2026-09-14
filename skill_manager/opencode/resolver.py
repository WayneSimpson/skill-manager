from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from skill_manager.harness.resolution import ResolutionContext
from skill_manager.jsonc import strip_jsonc


OpenCodeConfigFormat = Literal["json", "jsonc"]
OpenCodeConfigSourceStatus = Literal["loaded", "missing", "invalid", "unreadable"]

STATIC_ONLY_LIMITATION = (
    "Static compatibility only: reads known config files and does not query OpenCode, "
    "invoke its CLI, excludes plugin-added paths, or reproduce full upstream config resolution."
)


@dataclass(frozen=True)
class OpenCodeConfigSource:
    name: str
    path: Path
    format: OpenCodeConfigFormat
    precedence: int
    status: OpenCodeConfigSourceStatus
    diagnostic: str | None = None


@dataclass(frozen=True)
class OpenCodeConfigResolution:
    config: dict[str, object]
    sources: tuple[OpenCodeConfigSource, ...]
    diagnostics: tuple[str, ...]
    static_only: bool = True
    limitation: str = STATIC_ONLY_LIMITATION
    # Highest declaration of a named entry within each top-level section.
    entry_sources: dict[tuple[str, str], Path] = field(default_factory=dict)


def opencode_config_paths(context: ResolutionContext) -> tuple[Path, ...]:
    """Return static OpenCode config paths from lowest to highest precedence."""
    return (
        context.home / ".opencode" / "opencode.jsonc",
        context.xdg_config_home / "opencode" / "opencode.json",
        context.xdg_config_home / "opencode" / "opencode.jsonc",
    )


def resolve_opencode_config(context: ResolutionContext) -> OpenCodeConfigResolution:
    """Read and merge known OpenCode config files without performing any writes."""
    source_specs = (
        ("legacy", "jsonc"),
        ("xdg-json", "json"),
        ("xdg-jsonc", "jsonc"),
    )
    merged: dict[str, object] = {}
    sources: list[OpenCodeConfigSource] = []
    diagnostics: list[str] = []
    entry_sources: dict[tuple[str, str], Path] = {}

    for precedence, (path, (name, file_format)) in enumerate(
        zip(opencode_config_paths(context), source_specs)
    ):
        source, payload = _read_source(
            name=name,
            path=path,
            file_format=file_format,
            precedence=precedence,
        )
        sources.append(source)
        if source.diagnostic is not None:
            diagnostics.append(source.diagnostic)
        if payload is not None:
            for section, value in payload.items():
                if isinstance(value, dict):
                    for name in value:
                        entry_sources[(section, name)] = path
                else:
                    entry_sources = {key: origin for key, origin in entry_sources.items() if key[0] != section}
            merged = _merge_objects(merged, payload)

    return OpenCodeConfigResolution(
        config=merged,
        sources=tuple(sources),
        diagnostics=tuple(diagnostics),
        entry_sources=entry_sources,
    )


def opencode_write_config_path(context: ResolutionContext) -> Path:
    """Keep writes in the highest-precedence existing file, otherwise modern JSONC."""
    paths = opencode_config_paths(context)
    return next((path for path in reversed(paths) if path.is_file()), paths[-1])


def opencode_skill_paths(context: ResolutionContext) -> tuple[Path, ...]:
    """Return readable absolute global skill roots from the static config."""
    skills = resolve_opencode_config(context).config.get("skills")
    if not isinstance(skills, dict):
        return ()
    configured_paths = skills.get("paths")
    if not isinstance(configured_paths, list):
        return ()

    paths: list[Path] = []
    for value in configured_paths:
        if not isinstance(value, str) or not value:
            continue
        try:
            path = Path(value)
            if path.is_absolute() and path.is_dir():
                paths.append(path)
        except (OSError, RuntimeError, ValueError):
            continue
    return tuple(paths)


def _read_source(
    *,
    name: str,
    path: Path,
    file_format: OpenCodeConfigFormat,
    precedence: int,
) -> tuple[OpenCodeConfigSource, dict[str, object] | None]:
    try:
        if not path.is_file():
            return (
                OpenCodeConfigSource(
                    name=name,
                    path=path,
                    format=file_format,
                    precedence=precedence,
                    status="missing",
                ),
                None,
            )
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        diagnostic = f"OpenCode {name} config is unreadable"
        return (
            OpenCodeConfigSource(
                name=name,
                path=path,
                format=file_format,
                precedence=precedence,
                status="unreadable",
                diagnostic=diagnostic,
            ),
            None,
        )

    try:
        payload = json.loads(strip_jsonc(text) if file_format == "jsonc" else text)
    except json.JSONDecodeError as error:
        diagnostic = (
            f"OpenCode {name} config is invalid {file_format.upper()} "
            f"at line {error.lineno}, column {error.colno}"
        )
        return (
            OpenCodeConfigSource(
                name=name,
                path=path,
                format=file_format,
                precedence=precedence,
                status="invalid",
                diagnostic=diagnostic,
            ),
            None,
        )

    if not isinstance(payload, dict):
        diagnostic = f"OpenCode {name} config is invalid {file_format.upper()}: expected an object"
        return (
            OpenCodeConfigSource(
                name=name,
                path=path,
                format=file_format,
                precedence=precedence,
                status="invalid",
                diagnostic=diagnostic,
            ),
            None,
        )

    return (
        OpenCodeConfigSource(
            name=name,
            path=path,
            format=file_format,
            precedence=precedence,
            status="loaded",
        ),
        payload,
    )


def _merge_objects(
    lower: dict[str, object],
    higher: dict[str, object],
) -> dict[str, object]:
    merged = deepcopy(lower)
    for key, value in higher.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_objects(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged


__all__ = [
    "OpenCodeConfigResolution",
    "OpenCodeConfigSource",
    "STATIC_ONLY_LIMITATION",
    "opencode_config_paths",
    "opencode_skill_paths",
    "opencode_write_config_path",
    "resolve_opencode_config",
]
