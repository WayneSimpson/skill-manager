from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterable


MAX_JSON_BYTES = 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1000
MAX_ANCESTORS = 8

AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
_STANDARD_MANIFEST = "plugin.json"
_VENDOR_MANIFESTS = (
    (".claude-plugin/plugin.json", "claude"),
    (".cursor-plugin/plugin.json", "cursor"),
    (".codex-plugin/plugin.json", "codex"),
)
_MISSING = object()
_AGENT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_CURSOR_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_DOMAIN_DIRECTORY = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z0-9.-]+$")


@dataclass
class _ParsedManifest:
    name: str | None
    version: str | None
    harness: str | None
    evidence: str
    valid: bool
    components: list[dict[str, object]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class _RootParse:
    root: Path
    manifests: list[dict[str, str]]
    parsed: list[_ParsedManifest]
    diagnostics: list[str]


class SourcePackageDiscovery:
    """Read-only, bounded discovery of a package that owns a skill."""

    def __init__(self, stop_paths: tuple[Path, ...] = (), *, boundary: Path | None = None) -> None:
        self._stop_paths = tuple(_resolve_boundary(path) for path in stop_paths)
        self._boundary = boundary.resolve() if boundary is not None else None
        self._root_cache: dict[Path, _RootParse] = {}

    def resolve(self, skill_path: Path | None) -> dict[str, object]:
        try:
            return self._resolve(skill_path)
        except (OSError, RuntimeError, ValueError):
            return _unresolved("Source package changed or could not be read safely.")

    def inspect_root(self, root: Path) -> dict[str, object]:
        """Inspect an explicitly established source root, without ancestor inference."""
        try:
            root = root.resolve(strict=True)
            if (not root.is_dir() or _under_any(root, self._stop_paths)
                    or (self._boundary is not None and not root.is_relative_to(self._boundary))):
                return _unresolved('Source root is outside the inspection boundary.')
            parsed = self._parse_root(root, tuple(_manifest_candidates(root)))
            package = self._build_package(parsed, None)
            return ({'status': 'resolved', 'package': package, 'reason': None} if package
                    else _unresolved('No validated package manifest at the source root.'))
        except (OSError, RuntimeError, ValueError):
            return _unresolved('Source root could not be read safely.')

    def _resolve(self, skill_path: Path | None) -> dict[str, object]:
        skill_dir, error = _source_skill_directory(skill_path, self._stop_paths)
        if skill_dir is None:
            return _unresolved(error or "Skill source is not readable")
        if self._boundary is not None and not skill_dir.is_relative_to(self._boundary):
            return _unresolved('Skill is outside the source boundary.')

        root, candidates, error = self._find_root(skill_dir)
        if root is None:
            return _unresolved(error or "No recognised source package manifest was found")
        if root not in self._root_cache:
            self._root_cache[root] = self._parse_root(root, candidates)

        parsed = self._root_cache[root]
        package = self._build_package(parsed, skill_dir)
        if package is None:
            reason = "No validated source package manifest includes this skill"
            if parsed.diagnostics:
                reason += ": " + "; ".join(_dedupe(parsed.diagnostics))
            return _unresolved(reason)
        return {"status": "resolved", "package": deepcopy(package), "reason": None}

    def _find_root(
        self, skill_dir: Path
    ) -> tuple[Path | None, tuple[tuple[Path, str], ...], str | None]:
        current = skill_dir
        home = _resolve_boundary(Path.home())
        for _ in range(MAX_ANCESTORS + 1):
            if self._boundary is not None and not current.is_relative_to(self._boundary):
                break
            if current == home or _under_any(current, self._stop_paths) or current == Path(current.anchor):
                break
            candidates = _manifest_candidates(current)
            if candidates:
                return current, tuple(candidates), None
            if _lexists(current / "package.json"):
                return None, (), "package.json does not establish a harness integration"
            if _lexists(current / ".git"):
                return None, (), "Git source boundary reached before a recognised package manifest"
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None, (), "No recognised source package manifest was found within the discovery boundary"

    def _parse_root(
        self, root: Path, candidates: tuple[tuple[Path, str], ...]
    ) -> _RootParse:
        manifests: list[dict[str, str]] = []
        parsed: list[_ParsedManifest] = []
        diagnostics: list[str] = []

        for path, harness in candidates:
            if harness == "standard":
                continue
            relative = _relative(root, path)
            payload, error = _read_json(path, root)
            if error:
                manifests.append(_manifest(relative, "unresolved"))
                diagnostics.append(f"{relative}: {error}")
                continue
            if not isinstance(payload, dict):
                manifests.append(_manifest(relative, "unresolved"))
                diagnostics.append(f"{relative}: manifest must be a JSON object")
                continue
            vendor = _parse_vendor(payload, harness, relative, root)
            parsed.append(vendor)
            diagnostics.extend(vendor.diagnostics)
            manifests.append(_manifest(relative, "declared_harness_manifest" if vendor.valid else "unresolved"))

        root_manifest = root / _STANDARD_MANIFEST
        if _lexists(root_manifest):
            relative = _STANDARD_MANIFEST
            payload, error = _read_json(root_manifest, root)
            if error:
                manifests.append(_manifest(relative, "unresolved"))
                diagnostics.append(f"{relative}: {error}")
            elif not isinstance(payload, dict):
                manifests.append(_manifest(relative, "unresolved"))
                diagnostics.append(f"{relative}: manifest must be a JSON object")
            else:
                standard = _parse_standard(payload, relative, root)
                parsed.append(standard)
                diagnostics.extend(standard.diagnostics)
                manifests.append(_manifest(relative, "declared_standard" if standard.valid else "unresolved"))

        standard = next((item for item in parsed if item.valid and item.evidence == "declared_standard"), None)
        if standard is not None:
            _add_standard_extension_directories(root, standard, manifests, diagnostics)
        if _lexists(root / "package.json"):
            diagnostics.append("Executable package metadata alone does not prove an OpenCode integration.")
        return _RootParse(root, manifests, parsed, diagnostics)

    @staticmethod
    def _build_package(parsed: _RootParse, skill_dir: Path | None) -> dict[str, object] | None:
        valid = [item for item in parsed.parsed if item.valid]
        if not valid:
            return None
        components = _dedupe_components(
            component
            for item in valid
            for component in item.components
        )
        if skill_dir is not None and not _skill_is_declared(parsed.root, skill_dir, components):
            return None
        selected = _select_identity(valid)
        root = str(parsed.root.resolve())
        package: dict[str, object] = {
            "id": hashlib.sha256(f"source-package\0{root}".encode()).hexdigest(),
            "root": root,
            "name": selected.name or parsed.root.name,
            "version": selected.version,
            "evidence": selected.evidence,
            "manifests": _dedupe_manifests(parsed.manifests),
            "components": sorted(components, key=_component_key),
            "diagnostics": _dedupe(parsed.diagnostics),
        }
        package["revision"] = _capability_revision(package)
        return package


def _parse_standard(payload: dict[str, object], manifest: str, root: Path) -> _ParsedManifest:
    diagnostics: list[str] = []
    invalid = False
    if payload.get("$schema") != AGENT_PLUGIN_SCHEMA:
        diagnostics.append(f"{manifest}: canonical Agent Plugins schema is required")
        invalid = True

    name = payload.get("name")
    if not isinstance(name, str) or not _AGENT_NAME.fullmatch(name) or "--" in name or ".." in name:
        diagnostics.append(f"{manifest}: invalid standard plugin name")
        invalid = True

    version = payload.get("version")
    if "version" in payload and not isinstance(version, str):
        diagnostics.append(f"{manifest}: version must be a string")
        invalid = True
    for key in ("description", "homepage", "repository", "license"):
        if key in payload and not isinstance(payload[key], str):
            diagnostics.append(f"{manifest}: {key} must be a string")
            invalid = True

    if "author" in payload:
        author = payload["author"]
        if not isinstance(author, dict) or any(
            key not in {"name", "email", "url"} or not isinstance(value, str)
            for key, value in author.items()
        ):
            diagnostics.append(f"{manifest}: author must contain only string name/email/url fields")
            invalid = True
    if "keywords" in payload and (
        not isinstance(payload["keywords"], list)
        or any(not isinstance(keyword, str) for keyword in payload["keywords"])
    ):
        diagnostics.append(f"{manifest}: keywords must be an array of strings")
        invalid = True

    allowed_fields = {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
    for key in sorted(str(key) for key in payload if key not in allowed_fields):
        diagnostics.append(f"{manifest}: unknown standard field ignored: {key}")

    extensions = payload.get("extensions")
    components: list[dict[str, object]] = []
    if isinstance(extensions, dict):
        for namespace in sorted(extensions):
            diagnostics.append(f"{manifest}: unknown extension namespace {namespace}")
            components.append(
                _component("extensions", None, f"{manifest}#extensions.{namespace}", "declared_standard", manifest, False)
            )
    elif "extensions" in payload:
        diagnostics.append(f"{manifest}: non-object extensions ignored")

    if not invalid:
        for child in _skill_children(root / "skills", root, diagnostics):
            components.append(_component("skills", None, _relative(root, child), "declared_standard", manifest, True))
        mcp = root / "mcp.json"
        if _lexists(mcp):
            mcp_payload, error = _read_json(mcp, root)
            if error:
                diagnostics.append(f"mcp.json: {error}")
            elif _valid_mcp(mcp_payload, diagnostics):
                diagnostics.append("mcp.json: MCP configuration is structural only; runtime validity and deployment are unvalidated")
                component = _component("mcp", None, "mcp.json", "declared_standard", manifest, False)
                component["entries"] = sorted(mcp_payload["mcpServers"])
                components.append(component)

    return _ParsedManifest(
        name=name if isinstance(name, str) else None,
        version=version if isinstance(version, str) else None,
        harness=None,
        evidence="declared_standard",
        valid=not invalid,
        components=components if not invalid else [],
        diagnostics=diagnostics,
    )


def _parse_vendor(payload: dict[str, object], harness: str, manifest: str, root: Path) -> _ParsedManifest:
    name = payload.get("name", "") if harness == "codex" else payload.get("name")
    diagnostics: list[str] = []
    invalid = False
    if harness == "claude":
        if not isinstance(name, str) or not name.strip():
            diagnostics.append(f"{manifest}: Claude plugin name is required")
            invalid = True
    elif harness == "cursor":
        if not isinstance(name, str) or not _CURSOR_NAME.fullmatch(name):
            diagnostics.append(f"{manifest}: Cursor plugin name must be lowercase kebab/dotted text")
            invalid = True
    elif not isinstance(name, str):
        diagnostics.append(f"{manifest}: Codex name must be a string")
        invalid = True
    if harness == "codex" and isinstance(name, str) and not name.strip():
        name = root.name

    version = payload.get("version")
    if "version" in payload and not isinstance(version, str):
        diagnostics.append(f"{manifest}: version must be a string")
        invalid = True

    components: list[dict[str, object]] = []
    if harness == "claude":
        components.extend(_parse_claude_components(payload, manifest, root, diagnostics))
    elif harness == "cursor":
        components.extend(_parse_cursor_components(payload, manifest, root, diagnostics))
    else:
        components.extend(_parse_codex_components(payload, manifest, root, diagnostics))

    supported = _supported_vendor_fields(harness)
    for key in sorted(str(key) for key in payload if key not in supported):
        diagnostics.append(f"{manifest}: unsupported {harness.capitalize()} field ignored: {key}")
    return _ParsedManifest(
        name=name if isinstance(name, str) and (harness != "claude" or name.strip()) else None,
        version=version if isinstance(version, str) else None,
        harness=harness,
        evidence="declared_harness_manifest",
        valid=not invalid,
        components=components,
        diagnostics=diagnostics,
    )


def _parse_claude_components(
    payload: dict[str, object], manifest: str, root: Path, diagnostics: list[str]
) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for field, kind, default in (("skills", "skills", "skills"), ("commands", "commands", "commands"), ("agents", "agents", "agents")):
        values, valid = _paths(payload, field, default, add_default=field == "skills")
        if not valid:
            diagnostics.append(f"{manifest}: {field} must be a string or array of strings")
            continue
        if field == "skills" and field not in payload and not _lexists(root / "skills"):
            values = []
            if _regular_readable(root / "SKILL.md", root):
                components.append(_component("skills", "claude", ".", "verified_convention", manifest, True))
        for value, evidence in values:
            if kind == "skills":
                components.extend(_skill_components(root, value, "claude", manifest, evidence, diagnostics))
            else:
                components.extend(_file_components(root, value, kind, "claude", manifest, evidence, diagnostics, (".md",)))

    for field, kind, default in (("hooks", "hooks", "hooks/hooks.json"), ("mcpServers", "mcp", ".mcp.json")):
        components.extend(_structural_components(_MISSING, root, kind, "claude", manifest, default, diagnostics))
        if field in payload:
            components.extend(_structural_components(payload[field], root, kind, "claude", manifest, None, diagnostics))
    return components


def _parse_cursor_components(
    payload: dict[str, object], manifest: str, root: Path, diagnostics: list[str]
) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    for field, kind, default, extensions in (
        ("skills", "skills", "skills", ()),
        ("rules", "rules", "rules", (".md", ".mdc", ".markdown")),
        ("agents", "agents", "agents", (".md", ".mdc", ".markdown")),
        ("commands", "commands", "commands", (".md", ".mdc", ".markdown", ".txt")),
    ):
        values, valid = _paths(payload, field, default, add_default=False)
        if not valid:
            diagnostics.append(f"{manifest}: {field} must be a string or array of strings")
            continue
        if field == "skills" and field not in payload and not _lexists(root / "skills"):
            values = []
            if _regular_readable(root / "SKILL.md", root):
                components.append(_component("skills", "cursor", ".", "verified_convention", manifest, True))
        for value, evidence in values:
            if kind == "skills":
                components.extend(_skill_components(root, value, "cursor", manifest, evidence, diagnostics))
            else:
                components.extend(_file_components(root, value, kind, "cursor", manifest, evidence, diagnostics, extensions))
    hooks = payload["hooks"] if "hooks" in payload else _MISSING
    mcp = payload["mcpServers"] if "mcpServers" in payload else _MISSING
    if hooks is not _MISSING and not isinstance(hooks, (str, dict)):
        diagnostics.append(f"{manifest}: hooks must be a path or object")
    else:
        components.extend(_structural_components(hooks, root, "hooks", "cursor", manifest, "hooks/hooks.json", diagnostics))
    components.extend(_structural_components(mcp, root, "mcp", "cursor", manifest, "mcp.json", diagnostics))
    return components


def _parse_codex_components(
    payload: dict[str, object], manifest: str, root: Path, diagnostics: list[str]
) -> list[dict[str, object]]:
    components: list[dict[str, object]] = []
    values, valid = _paths(payload, "skills", "./skills", add_default=False)
    if payload.get("skills") == []:
        values = [("./skills", "verified_convention")]
    if not valid:
        diagnostics.append(f"{manifest}: skills must be a string or array of strings")
    else:
        for value, evidence in values:
            components.extend(_skill_components(root, value, "codex", manifest, evidence, diagnostics, codex=True))

    apps = payload.get("apps", _MISSING)
    if apps is _MISSING and _lexists(root / ".app.json"):
        components.extend(_resource_components(root, "./.app.json", "apps", "codex", manifest, "verified_convention", diagnostics, codex=True))
    if apps is not _MISSING:
        if not isinstance(apps, str):
            diagnostics.append(f"{manifest}: apps must be a string path")
        else:
            components.extend(_resource_components(root, apps, "apps", "codex", manifest, "declared_harness_manifest", diagnostics, codex=True))

    hooks = payload.get("hooks", _MISSING)
    if hooks is not _MISSING and not _structural_shape(hooks, True):
        diagnostics.append(f"{manifest}: hooks must be a path, object, or array of paths/objects")
        hooks = _MISSING
    mcp = payload.get("mcpServers", _MISSING)
    if mcp is not _MISSING and not _structural_shape(mcp, False):
        diagnostics.append(f"{manifest}: mcpServers must be a string path or object")
        mcp = _MISSING
    components.extend(_structural_components(hooks, root, "hooks", "codex", manifest, "./hooks/hooks.json", diagnostics, codex=True))
    components.extend(_structural_components(mcp, root, "mcp", "codex", manifest, "./.mcp.json", diagnostics, codex=True))
    if isinstance(mcp, dict):
        components.extend(_structural_components(_MISSING, root, "mcp", "codex", manifest, "./.mcp.json", diagnostics, codex=True))
    for key in ("agents", "rules"):
        if key in payload:
            diagnostics.append(f"{manifest}: unsupported Codex field ignored: {key}")
    return components


def _paths(
    payload: dict[str, object], field: str, default: str | None, *, add_default: bool
) -> tuple[list[tuple[str, str]], bool]:
    if field not in payload:
        return ([(default, "verified_convention")] if default is not None else []), True
    value = payload[field]
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = list(value)
    else:
        return [], False
    result = [(item, "declared_harness_manifest") for item in values]
    if add_default and default is not None and default not in values:
        result.insert(0, (default, "verified_convention"))
    return result, True


def _skill_components(
    root: Path,
    raw_path: str,
    harness: str,
    manifest: str,
    evidence: str,
    diagnostics: list[str],
    *,
    codex: bool = False,
) -> list[dict[str, object]]:
    relative = _declared_path(raw_path, codex=codex)
    if relative is None:
        diagnostics.append(f"{manifest}: invalid {harness} skills path")
        return []
    target, error = _safe_path(root / relative, root)
    if target is None:
        if evidence == "verified_convention" and not _lexists(root / relative):
            return []
        diagnostics.append(f"{manifest}: skills path rejected: {error or 'unsafe path'}")
        return []
    if target.is_file():
        if target.name != "SKILL.md" or not _regular_readable(target, root):
            diagnostics.append(f"{manifest}: skills path does not identify a regular skill")
            return []
        return [_component("skills", harness, _relative(root, target.parent), evidence, manifest, True)]
    if not target.is_dir():
        diagnostics.append(f"{manifest}: declared skills path is missing")
        return []
    skill_file = target / "SKILL.md"
    if _lexists(skill_file):
        safe_skill, skill_error = _safe_path(skill_file, root)
        if safe_skill is None:
            diagnostics.append(f"{manifest}: SKILL.md rejected: {skill_error or 'unsafe path'}")
        elif _regular_readable(safe_skill, root):
            return [_component("skills", harness, relative, evidence, manifest, True)]
    return [
        _component("skills", harness, _relative(root, child), evidence, manifest, True)
        for child in _skill_children(target, root, diagnostics, recursive=codex)
    ]


def _file_components(
    root: Path,
    raw_path: str,
    kind: str,
    harness: str,
    manifest: str,
    evidence: str,
    diagnostics: list[str],
    extensions: tuple[str, ...],
) -> list[dict[str, object]]:
    relative = _declared_path(raw_path)
    if relative is None:
        diagnostics.append(f"{manifest}: invalid {harness} {kind} path")
        return []
    target, error = _safe_path(root / relative, root)
    if target is None:
        if evidence == "verified_convention" and not _lexists(root / relative):
            return []
        diagnostics.append(f"{manifest}: {kind} path rejected: {error or 'unsafe path'}")
        return []
    if target.is_file():
        if extensions and target.suffix.lower() not in extensions:
            diagnostics.append(f"{manifest}: unsupported {kind} extension ignored: {relative}")
            return []
        return [_component(kind, harness, relative, evidence, manifest, False)]
    if not target.is_dir():
        diagnostics.append(f"{manifest}: declared {kind} path is missing")
        return []
    result: list[dict[str, object]] = []
    for entry in _directory_entries(target, diagnostics):
        path = Path(entry.path)
        if entry.is_symlink() and _safe_path(path, root)[0] is None:
            diagnostics.append(f"{manifest}: {kind} component rejected: path escapes the package root")
            continue
        if not entry.is_file(follow_symlinks=True):
            continue
        entry_relative = _relative(root, path)
        if extensions and path.suffix.lower() not in extensions:
            diagnostics.append(f"{manifest}: unsupported {kind} extension ignored: {entry_relative}")
            continue
        if _regular_readable(path, root):
            result.append(_component(kind, harness, entry_relative, evidence, manifest, False))
    return result


def _resource_components(
    root: Path,
    raw_path: str,
    kind: str,
    harness: str,
    manifest: str,
    evidence: str,
    diagnostics: list[str],
    *,
    codex: bool = False,
) -> list[dict[str, object]]:
    relative = _declared_path(raw_path, codex=codex)
    if relative is None:
        diagnostics.append(f"{manifest}: invalid {harness} {kind} path")
        return []
    target, error = _safe_path(root / relative, root)
    if target is None:
        diagnostics.append(f"{manifest}: {kind} path rejected: {error or 'unsafe path'}")
        return []
    if not (target.is_file() or target.is_dir()):
        diagnostics.append(f"{manifest}: declared {kind} path is missing")
        return []
    return [_component(kind, harness, relative, evidence, manifest, False)]


def _structural_components(
    value: object,
    root: Path,
    kind: str,
    harness: str,
    manifest: str,
    default: str | None,
    diagnostics: list[str],
    *,
    codex: bool = False,
) -> list[dict[str, object]]:
    if value is _MISSING:
        values: list[object] = [default] if default is not None else []
        evidence = "verified_convention"
    else:
        values = value if isinstance(value, list) else [value]
        evidence = "declared_harness_manifest"
    result: list[dict[str, object]] = []
    for item in values:
        if isinstance(item, dict):
            if kind == "mcp":
                _append_once(diagnostics, f"{manifest}: MCP configuration is structural only; runtime validity and deployment are unvalidated")
            component = _component(kind, harness, f"{manifest}#{'mcpServers' if kind == 'mcp' else kind}", evidence, manifest, False)
            entries = item.get("hooks", {}) if kind == "hooks" else item.get("mcpServers", item)
            if isinstance(entries, dict):
                component["entries"] = sorted(entries)
            result.append(component)
            continue
        if not isinstance(item, str):
            diagnostics.append(f"{manifest}: {kind} declaration must contain only paths or objects")
            continue
        relative = _declared_path(item, codex=codex)
        if relative is None:
            diagnostics.append(f"{manifest}: invalid {kind} path")
            continue
        target, error = _safe_path(root / relative, root)
        if target is None:
            if evidence == "verified_convention" and not _lexists(root / relative):
                continue
            diagnostics.append(f"{manifest}: {kind} path rejected: {error or 'unsafe path'}")
            continue
        if not target.is_file():
            diagnostics.append(f"{manifest}: {kind} path is not a regular file")
            continue
        payload, error = _read_json(target, root)
        if error or not isinstance(payload, dict):
            diagnostics.append(f"{manifest}: {kind} config rejected: {error or 'expected a JSON object'}")
            continue
        entries = payload.get("hooks", {}) if kind == "hooks" else payload.get("mcpServers", payload)
        if not isinstance(entries, dict):
            diagnostics.append(f"{manifest}: invalid {kind} configuration object")
            continue
        component = _component(kind, harness, relative, evidence, manifest, False)
        component["entries"] = sorted(entries)
        result.append(component)
        if kind == "mcp":
            _append_once(diagnostics, f"{manifest}: MCP configuration is structural only; runtime validity and deployment are unvalidated")
    return result


def _structural_shape(value: object, allow_list: bool) -> bool:
    if isinstance(value, (str, dict)):
        return True
    return allow_list and isinstance(value, list) and all(isinstance(item, (str, dict)) for item in value)


def _valid_mcp(payload: object, diagnostics: list[str]) -> bool:
    if not isinstance(payload, dict):
        diagnostics.append("mcp.json: MCP manifest must be a JSON object")
        return False
    if any(key not in {"$schema", "mcpServers"} for key in payload):
        diagnostics.append("mcp.json: unsupported top-level MCP fields")
        return False
    if payload.get("$schema") != MCP_SCHEMA:
        diagnostics.append("mcp.json: canonical MCP schema is required")
        return False
    if not isinstance(payload.get("mcpServers"), dict):
        diagnostics.append("mcp.json: mcpServers must be an object")
        return False
    return True


def _source_skill_directory(skill_path: Path | None, stop_paths: tuple[Path, ...]) -> tuple[Path | None, str | None]:
    if skill_path is None:
        return None, "Skill path is required"
    if not isinstance(skill_path, Path) or not skill_path.is_absolute():
        return None, "Skill path must be absolute"
    try:
        supplied = (skill_path.parent if skill_path.name == "SKILL.md" else skill_path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "Skill path cannot be resolved safely"
    if _under_any(supplied, stop_paths):
        return None, "Skill path is inside an excluded discovery path"
    if supplied.is_file() and supplied.name == "SKILL.md":
        directory = supplied.parent
    elif supplied.is_dir():
        directory = supplied
    else:
        return None, "Skill source must be a directory or SKILL.md"
    if directory == _resolve_boundary(Path.home()):
        return None, "Skill source is inside an excluded discovery path"
    # Containment/readability is checked against the proven package root later.
    if not _readable_directory(directory) or not _is_regular(directory / "SKILL.md"):
        return None, "Skill source must contain a readable regular SKILL.md"
    return directory, None


def _manifest_candidates(root: Path) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    if _lexists(root / _STANDARD_MANIFEST):
        result.append((root / _STANDARD_MANIFEST, "standard"))
    for relative, harness in _VENDOR_MANIFESTS:
        if _lexists(root / relative):
            result.append((root / relative, harness))
    return result


def _read_json(path: Path, root: Path) -> tuple[object | None, str | None]:
    resolved, error = _safe_path(path, root)
    if resolved is None:
        return None, error or "path is outside the package root"
    try:
        metadata = resolved.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return None, "manifest is not a regular file"
        if metadata.st_size > MAX_JSON_BYTES:
            return None, "JSON read budget exceeded"
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(resolved, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None, "manifest is not a regular file"
            data = bytearray()
            while len(data) <= MAX_JSON_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_JSON_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_JSON_BYTES:
                return None, "JSON read budget exceeded"
        finally:
            os.close(descriptor)
        try:
            text = bytes(data).decode("utf-8")
            return json.loads(text), None
        except UnicodeDecodeError:
            return None, "invalid UTF-8 JSON"
        except json.JSONDecodeError:
            return None, "invalid JSON"
        except RecursionError:
            return None, "JSON nesting exceeds parser limits"
    except (OSError, RuntimeError, ValueError):
        return None, "manifest is unreadable"


def _skill_children(directory: Path, root: Path, diagnostics: list[str], *, recursive: bool = False) -> list[Path]:
    if not _lexists(directory):
        return []
    safe, error = _safe_path(directory, root)
    if safe is None or not safe.is_dir():
        diagnostics.append(f"skills directory rejected: {error or 'expected a directory'}")
        return []
    if recursive:
        return _recursive_skill_children(safe, root, diagnostics)
    result: list[Path] = []
    for entry in _directory_entries(safe, diagnostics):
        child = Path(entry.path)
        if entry.is_symlink() and _safe_path(child, root)[0] is None:
            diagnostics.append("skill component rejected: path escapes the package root")
            continue
        if not entry.is_dir(follow_symlinks=True):
            continue
        resolved, error = _safe_path(child, root)
        if resolved is None:
            diagnostics.append(f"skill component rejected: {error or 'unsafe path'}")
            continue
        skill = resolved / "SKILL.md"
        if _lexists(skill) and _safe_path(skill, root)[0] is None:
            diagnostics.append("skill component rejected: SKILL.md escapes the package root")
            continue
        if _regular_readable(skill, root):
            result.append(child)
    return sorted(result, key=lambda entry: entry.name)


def _recursive_skill_children(directory: Path, root: Path, diagnostics: list[str]) -> list[Path]:
    pending = [(directory, 0)]
    seen: set[Path] = set()
    result = []
    inspected = 0
    while pending:
        folder, depth = pending.pop()
        resolved, error = _safe_path(folder, root)
        if resolved is None:
            diagnostics.append(f"skills directory rejected: {error}")
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _regular_readable(folder / "SKILL.md", root):
            result.append(folder)
            continue
        if depth >= MAX_ANCESTORS:
            diagnostics.append("Nested skills directory depth limit reached")
            continue
        entries = _directory_entries(folder, diagnostics)
        inspected += len(entries)
        if inspected > MAX_DIRECTORY_ENTRIES:
            diagnostics.append("Recursive skills listing budget exceeded")
            break
        for entry in entries:
            if entry.is_dir():
                pending.append((Path(entry.path), depth + 1))
    return sorted(result)


def _directory_entries(directory: Path, diagnostics: list[str]) -> list[os.DirEntry[str]]:
    result: list[os.DirEntry[str]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                result.append(entry)
                if len(result) > MAX_DIRECTORY_ENTRIES:
                    diagnostics.append("directory listing budget exceeded")
                    return []
    except (OSError, RuntimeError, ValueError):
        return []
    return sorted(result, key=lambda entry: entry.name)


def _readable_directory(path: Path) -> bool:
    try:
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if not directory_flag:
            with os.scandir(path) as entries:
                next(entries, None)
            return True
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | directory_flag)
        try:
            return stat.S_ISDIR(os.fstat(descriptor).st_mode)
        finally:
            os.close(descriptor)
    except (OSError, RuntimeError, ValueError):
        return False


def _regular_readable(path: Path, root: Path) -> bool:
    resolved, _ = _safe_path(path, root)
    if resolved is None:
        return False
    try:
        if not _is_regular(resolved):
            return False
        with resolved.open("rb") as stream:
            stream.read(1)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_path(path: Path, root: Path) -> tuple[Path | None, str | None]:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, "path cannot be resolved safely"
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None, "path escapes the package root"
    return resolved, None


def _resolve_boundary(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return path.absolute()


def _under_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(_within(path, root) for root in roots)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexists(path: Path) -> bool:
    try:
        return os.path.lexists(path)
    except (OSError, ValueError):
        return False


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _declared_path(raw_path: str, *, codex: bool = False) -> str | None:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path or "\x00" in raw_path:
        return None
    if raw_path.startswith("/") or raw_path.startswith("//") or re.match(r"^[A-Za-z]:", raw_path):
        return None
    if codex and not raw_path.startswith("./"):
        return None
    parts = raw_path.split("/")
    if any(part == ".." for part in parts):
        return None
    normalized = "/".join(part for part in parts if part not in {"", "."})
    return normalized or "."


def _component(kind: str, harness: str | None, path: str, evidence: str, manifest: str, supported: bool) -> dict[str, object]:
    return {"kind": kind, "harness": harness, "path": path, "evidence": evidence, "manifest": manifest, "supported": supported}


def _manifest(path: str, evidence: str) -> dict[str, str]:
    return {"path": path, "format": "json", "evidence": evidence}


def _supported_vendor_fields(harness: str) -> set[str]:
    common = {"name", "version", "description", "author", "license", "homepage", "repository", "keywords", "$schema"}
    if harness == "claude":
        return common | {"skills", "commands", "agents", "hooks", "mcpServers"}
    if harness == "cursor":
        return common | {"skills", "rules", "agents", "commands", "hooks", "mcpServers"}
    return common | {"skills", "mcpServers", "hooks", "apps", "agents", "rules"}


def _add_standard_extension_directories(
    root: Path, standard: _ParsedManifest, manifests: list[dict[str, str]], diagnostics: list[str]
) -> None:
    manifest = next(item["path"] for item in manifests if item["evidence"] == "declared_standard")
    for entry in _directory_entries(root, diagnostics):
        if entry.is_dir(follow_symlinks=False) and _DOMAIN_DIRECTORY.fullmatch(entry.name):
            relative = _relative(root, Path(entry.path))
            diagnostics.append(f"{manifest}: unknown extension namespace directory: {relative}")
            standard.components.append(_component("extensions", None, relative, "unresolved", manifest, False))


def _skill_is_declared(root: Path, skill_dir: Path, components: list[dict[str, object]]) -> bool:
    try:
        target = skill_dir.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    for item in components:
        if item.get("kind") != "skills" or not isinstance(item.get("path"), str):
            continue
        path = item["path"]
        if "#" in path:
            continue
        try:
            if (root / path).resolve(strict=True) == target:
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _dedupe_components(components: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    indexes: dict[tuple[object, ...], int] = {}
    fields = ("kind", "harness", "path", "manifest", "supported")
    for item in components:
        key = tuple(item.get(field) for field in fields)
        if key not in indexes:
            indexes[key] = len(result)
            result.append(dict(item))
        elif result[indexes[key]].get("evidence") == "verified_convention" and item.get("evidence") == "declared_harness_manifest":
            result[indexes[key]] = dict(item)
    return result


def _dedupe_manifests(manifests: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    result: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in manifests:
        result[(item["path"], item["format"], item["evidence"])] = dict(item)
    return [result[key] for key in sorted(result)]


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _component_key(item: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(item.get("kind", "")), str(item.get("harness", "")), str(item.get("path", "")), str(item.get("manifest", "")))


def _select_identity(items: list[_ParsedManifest]) -> _ParsedManifest:
    priority = {None: 0, "claude": 1, "cursor": 2, "codex": 3}
    return sorted(items, key=lambda item: (0 if item.evidence == "declared_standard" else 1, priority.get(item.harness, 9)))[0]


def _capability_revision(package: dict[str, object]) -> str:
    descriptor = {
        "name": package["name"],
        "version": package["version"],
        "evidence": package["evidence"],
        "manifests": package["manifests"],
        "components": package["components"],
    }
    encoded = json.dumps(descriptor, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _unresolved(reason: str) -> dict[str, object]:
    return {"status": "unresolved", "package": None, "reason": reason}


__all__ = ["SourcePackageDiscovery"]
