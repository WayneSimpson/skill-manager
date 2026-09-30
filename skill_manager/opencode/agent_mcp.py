"""Read-only MCP server discovery for the agent editor permission controls.

Source of truth is the configured OpenCode runtime `GET /mcp` endpoint (server
name → status). When the runtime is unavailable, configured server names are
derived from the resolved OpenCode configuration `mcp` section. Individual MCP
tool enumeration is NOT attempted: the runtime does not reliably expose it in
this environment, so the editor works at server-wide wildcard granularity.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from skill_manager.harness.resolution import ResolutionContext
from skill_manager.opencode.resolver import resolve_opencode_config

_MCP_TIMEOUT_SECONDS = 5
_MAX_RUNTIME_BYTES = 1024 * 1024

# Statuses the runtime is known to report; anything else passes through
# verbatim so future statuses are surfaced truthfully rather than guessed.
_KNOWN_STATUSES = frozenset({
    "connected", "failed", "needs-auth", "auth", "disabled", "pending", "connecting",
})


def canonical_mcp_wildcard(server_name: str) -> str:
    """Canonical server-wide permission wildcard for an MCP server name.

    OpenCode's permission model addresses MCP/custom tools by wildcard tool
    name, e.g. server `n8n_nccio` → `n8n_nccio_*`.
    """
    return f"{server_name}_*"


def mcp_wildcard_matches(action: str, server_name: str) -> bool:
    """Whether a rule action is a server-wide wildcard for this server.

    Matches the canonical `{name}_*` form and the legacy no-underscore
    `{name}*` form (e.g. `playwright*`). Both are confidently associated with
    the server; exact per-tool patterns do NOT match and stay in Advanced.
    """
    return action == canonical_mcp_wildcard(server_name) or action == f"{server_name}*"


def parse_runtime_mcp(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    """Parse the runtime `/mcp` response `{name: {status, error?}}` into DTOs."""
    data = payload if isinstance(payload, dict) else json.loads(payload)
    servers: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for name, entry in data.items():
            if not isinstance(name, str) or not name:
                continue
            status = "unknown"
            error = None
            if isinstance(entry, dict):
                raw_status = entry.get("status")
                if isinstance(raw_status, str) and raw_status:
                    status = raw_status
                raw_error = entry.get("error")
                if isinstance(raw_error, str) and raw_error:
                    error = raw_error
            elif isinstance(entry, str) and entry:
                status = entry
            servers.append({
                "name": name,
                "status": status,
                "error": error,
                "wildcard": canonical_mcp_wildcard(name),
            })
    servers.sort(key=lambda s: s["name"].lower())
    return {"source": "runtime", "servers": servers}


def parse_config_mcp(config: dict[str, Any] | None) -> dict[str, Any]:
    """Derive configured MCP server names from a resolved OpenCode config.

    Generation-tolerant: supports the V1/direct map (`mcp.{name}`) and the
    documented V2 nested map (`mcp.servers.{name}`). When both shapes appear,
    the nested `servers` entries win per name (deterministic, no duplicates)
    and the literal `servers` container key is never treated as a server.
    """
    servers: list[dict[str, Any]] = []
    mcp = config.get("mcp") if isinstance(config, dict) else None
    if isinstance(mcp, dict):
        merged: dict[str, dict[str, Any] | None] = {}
        # Direct (V1-style) entries first; the nested container key is not a server.
        for name, entry in mcp.items():
            if isinstance(name, str) and name and name != "servers":
                merged[name] = entry if isinstance(entry, dict) else None
        # Nested V2 `servers` entries take precedence per name.
        nested = mcp.get("servers")
        if isinstance(nested, dict):
            for name, entry in nested.items():
                if isinstance(name, str) and name:
                    merged[name] = entry if isinstance(entry, dict) else None
        for name, entry in merged.items():
            disabled = isinstance(entry, dict) and entry.get("enabled") is False
            servers.append({
                "name": name,
                "status": "disabled" if disabled else "unknown",
                "error": None,
                "wildcard": canonical_mcp_wildcard(name),
            })
    servers.sort(key=lambda s: s["name"].lower())
    return {"source": "config", "servers": servers}


class OpenCodeMcpService:
    """Read-only MCP server discovery; runtime first, config fallback."""

    def __init__(self, context: ResolutionContext, *, server_url: str | None = None):
        self._context = context
        self._server_url = server_url

    def servers(self) -> dict[str, Any]:
        runtime = self._try_runtime()
        if runtime is not None:
            return runtime
        config = self._try_config()
        if config is not None:
            return config
        return {
            "source": "unavailable",
            "servers": [],
            "detail": (
                "No OpenCode runtime or configured MCP servers are available. "
                "Existing MCP permission rules remain editable under Advanced."
            ),
        }

    def _try_runtime(self) -> dict[str, Any] | None:
        if not self._server_url:
            return None
        try:
            request = Request(
                f"{self._server_url.rstrip('/')}/mcp",
                headers={"User-Agent": "skill-manager"},
            )
            with urlopen(request, timeout=_MCP_TIMEOUT_SECONDS) as response:
                payload = response.read(_MAX_RUNTIME_BYTES)
            result = parse_runtime_mcp(payload)
            return result if result["servers"] else None
        except (OSError, URLError, ValueError):
            return None

    def _try_config(self) -> dict[str, Any] | None:
        try:
            resolution = resolve_opencode_config(self._context)
        except (OSError, ValueError):
            return None
        result = parse_config_mcp(resolution.config)
        return result if result["servers"] else None


__all__ = [
    "OpenCodeMcpService",
    "canonical_mcp_wildcard",
    "mcp_wildcard_matches",
    "parse_config_mcp",
    "parse_runtime_mcp",
]
