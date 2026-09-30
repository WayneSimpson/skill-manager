"""Neutral V1/V2 permission representation with lossless round-trip."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PermissionRule:
    """One permission decision: an action/tool plus optional resource pattern."""
    action: str                    # V1 key name or V2 action (schema-native)
    effect: str                    # allow | ask | deny
    resource: str | None = None   # Optional pattern/resource for scoped rules
    order: int = 0                 # V2 semantic order; V1 dict order on serialise
    custom: bool = False           # Unknown/MCP/wildcard action not in known set
    raw_value: Any = None          # Original raw V1 value for passthrough fidelity


# Actions with well-known V1↔V2 naming differences. Missing entries mean the
# name is shared between generations.
_V1_TO_V2_ACTION = {"bash": "shell", "task": "subagent"}
_V2_TO_V1_ACTION = {"shell": "bash", "subagent": "task"}

# Common permission actions for the structured editor's known-act set.
COMMON_ACTIONS_V1 = (
    "read", "edit", "bash", "glob", "grep", "list", "task",
    "webfetch", "websearch", "skill", "external_directory", "question", "todowrite",
)
COMMON_ACTIONS_V2 = (
    "read", "edit", "shell", "glob", "grep", "list", "subagent",
    "webfetch", "websearch", "skill", "external_directory", "question", "todowrite",
)

_VALID_EFFECTS = frozenset({"allow", "ask", "deny"})


def parse_v1_permission(raw: dict[str, Any] | None) -> list[PermissionRule]:
    """Parse a V1 `permission` object into neutral rules, preserving order/unknowns."""
    if raw is None or not isinstance(raw, dict):
        return []
    rules: list[PermissionRule] = []
    for order, (action, value) in enumerate(raw.items()):
        rules.extend(_parse_v1_entry(action, value, order))
    return rules


def _parse_v1_entry(action: str, value: Any, order: int) -> list[PermissionRule]:
    custom = action not in COMMON_ACTIONS_V1
    if isinstance(value, str) and value in _VALID_EFFECTS:
        return [PermissionRule(action=action, effect=value, order=order, custom=custom)]
    if isinstance(value, dict):
        # Pattern-scoped rule: V1 maps resource-pattern → effect.
        rules = []
        for pattern, effect in value.items():
            if isinstance(effect, str) and effect in _VALID_EFFECTS:
                rules.append(PermissionRule(
                    action=action, effect=effect, resource=pattern,
                    order=order, custom=custom,
                ))
            else:
                # Unknown nested shape: keep the raw value verbatim.
                rules.append(PermissionRule(
                    action=action, effect="allow", resource=pattern, order=order,
                    custom=True, raw_value=effect,
                ))
        return rules
    # Unknown scalar shape: passthrough with raw fidelity.
    return [PermissionRule(action=action, effect="allow", order=order,
                           custom=True, raw_value=value)]


def serialize_v1_permission(rules: list[PermissionRule]) -> dict[str, Any] | None:
    """Serialise neutral rules back into a V1 `permission` object."""
    if not rules:
        return None
    result: dict[str, Any] = {}
    for rule in rules:
        if rule.raw_value is not None:
            # Preserve the original raw value verbatim (unknown shape).
            _merge_v1(result, rule.action, rule.resource, rule.raw_value)
        elif rule.resource is None:
            _merge_v1(result, rule.action, None, rule.effect)
        else:
            _merge_v1(result, rule.action, rule.resource, rule.effect)
    return result


def _merge_v1(result: dict, action: str, resource: str | None, value: Any) -> None:
    if resource is None:
        result[action] = value
        return
    existing = result.get(action)
    if isinstance(existing, dict):
        existing[resource] = value
    else:
        result[action] = {resource: value}


def parse_v2_permissions(raw: list[Any] | None) -> list[PermissionRule]:
    """Parse a V2 ordered `permissions` list into neutral rules preserving order."""
    if raw is None or not isinstance(raw, list):
        return []
    rules: list[PermissionRule] = []
    for order, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "")
        effect = entry.get("effect", "")
        resource = entry.get("resource")
        custom = action not in COMMON_ACTIONS_V2
        if action and effect in _VALID_EFFECTS:
            extra_keys = set(entry.keys()) - {"action", "effect", "resource"}
            if extra_keys:
                # Extra future fields: keep the raw entry for round-trip fidelity.
                rules.append(PermissionRule(
                    action=action, effect=effect,
                    resource=resource if isinstance(resource, str) else None,
                    order=order, custom=custom, raw_value=entry,
                ))
            else:
                rules.append(PermissionRule(
                    action=action, effect=effect,
                    resource=resource if isinstance(resource, str) else None,
                    order=order, custom=custom,
                ))
        elif action:
            # Unknown shape: passthrough.
            rules.append(PermissionRule(
                action=action, effect=effect or "allow",
                resource=resource if isinstance(resource, str) else None,
                order=order, custom=True, raw_value=entry,
            ))
    return rules


def serialize_v2_permissions(rules: list[PermissionRule]) -> list[dict[str, Any]] | None:
    """Serialise neutral rules back into a V2 ordered `permissions` list."""
    if not rules:
        return None
    result: list[dict[str, Any]] = []
    for rule in sorted(rules, key=lambda r: r.order):
        if rule.raw_value is not None and isinstance(rule.raw_value, dict):
            # Preserve unknown/future keys from the raw entry, but overlay the
            # user's deliberate edits of known fields (action/effect/resource)
            # so editing a rule with extras does not silently revert the change.
            entry = dict(rule.raw_value)
            entry["action"] = rule.action
            entry["effect"] = rule.effect
            if rule.resource is not None:
                entry["resource"] = rule.resource
            else:
                entry.pop("resource", None)  # Explicitly cleared resource.
            result.append(entry)
            continue
        entry: dict[str, Any] = {"action": rule.action, "effect": rule.effect}
        if rule.resource is not None:
            entry["resource"] = rule.resource
        result.append(entry)
    return result


def to_generation(generation: str, raw: Any) -> list[PermissionRule]:
    """Parse raw permission data for the given schema generation."""
    if generation == "v2":
        return parse_v2_permissions(raw)
    return parse_v1_permission(raw)


def from_generation(generation: str, rules: list[PermissionRule]) -> Any:
    """Serialise neutral rules back for the given schema generation."""
    if generation == "v2":
        return serialize_v2_permissions(rules)
    return serialize_v1_permission(rules)


def known_action_for_generation(action: str, generation: str) -> str:
    """Map a display-level action to its schema-native name (no cross-gen leakage)."""
    if generation == "v2":
        return _V1_TO_V2_ACTION.get(action, action)
    return _V2_TO_V1_ACTION.get(action, action)


__all__ = [
    "COMMON_ACTIONS_V1",
    "COMMON_ACTIONS_V2",
    "PermissionRule",
    "from_generation",
    "known_action_for_generation",
    "parse_v1_permission",
    "parse_v2_permissions",
    "serialize_v1_permission",
    "serialize_v2_permissions",
    "to_generation",
]
