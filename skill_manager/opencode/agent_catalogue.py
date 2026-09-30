"""Authoritative OpenCode agent-editor model catalogue with safe fallback."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from skill_manager.harness.resolution import ResolutionContext

_CATALOGUE_TIMEOUT_SECONDS = 5
_MAX_RUNTIME_BYTES = 32 * 1024 * 1024  # 226 providers can be large.
_MAX_CACHE_BYTES = 8 * 1024 * 1024


def _models_from_provider(provider: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract model DTOs from one runtime/cache provider entry."""
    models = []
    for model_id, model in (provider.get("models") or {}).items():
        if not isinstance(model, dict):
            continue
        variants = sorted(
            name for name, value in (model.get("variants") or {}).items()
            if isinstance(value, dict) and name
        )
        models.append({
            "id": f'{provider.get("id", "")}/{model_id}',
            "providerId": provider.get("id", ""),
            "providerName": provider.get("name") or provider.get("id", ""),
            "name": model.get("name") or model_id,
            "status": model.get("status") or "active",
            "reasoning": bool(model.get("capabilities", {}).get("reasoning")),
            "variants": variants,
        })
    return models


def parse_runtime_catalogue(payload: bytes | str) -> dict[str, Any]:
    """Parse the OpenCode runtime `/provider` response into catalogue DTOs."""
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    providers_list = data.get("all", []) if isinstance(data, dict) else []
    connected = data.get("connected", []) if isinstance(data, dict) else []
    connected_ids = {
        entry for entry in connected if isinstance(entry, str)
    } if isinstance(connected, list) else set()
    providers = []
    for entry in providers_list:
        if not isinstance(entry, dict):
            continue
        models = _models_from_provider(entry)
        providers.append({
            "id": entry.get("id", ""),
            "name": entry.get("name") or entry.get("id", ""),
            "source": entry.get("source", ""),
            "connected": entry.get("id", "") in connected_ids,
            "models": models,
        })
    providers.sort(key=lambda p: (not p["connected"], p["name"].lower()))
    return {
        "source": "runtime",
        "providers": providers,
        "connectedProviderIds": sorted(connected_ids),
        "totalModels": sum(len(p["models"]) for p in providers),
    }


def parse_cache_catalogue(payload: bytes | str) -> dict[str, Any]:
    """Parse the local `~/.cache/opencode/models.json` fallback catalogue."""
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    providers = []
    for provider_id, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        # The cache uses the same models shape; synthesise the provider wrapper.
        providers.append({
            "id": provider_id,
            "name": entry.get("name") or provider_id,
            "source": entry.get("source", "cache"),
            "models": _models_from_provider({**entry, "id": provider_id}),
        })
    return {
        "source": "cache",
        "providers": [
            {**p, "connected": None} for p in providers  # Unknown from cache.
        ],
        "connectedProviderIds": [],
        "totalModels": sum(len(p["models"]) for p in providers),
    }


class OpenCodeAgentCatalogueService:
    """Read-only model/variant catalogue; runtime first, local cache fallback."""

    def __init__(self, context: ResolutionContext, *, server_url: str | None = None):
        self._context = context
        self._server_url = server_url

    def catalogue(self) -> dict[str, Any]:
        runtime = self._try_runtime()
        if runtime is not None:
            return runtime
        cache = self._try_cache()
        if cache is not None:
            return cache
        return {
            "source": "unavailable",
            "providers": [],
            "totalModels": 0,
            "detail": (
                "No OpenCode runtime or local model catalogue is available. "
                "Model values can still be entered or preserved as custom entries."
            ),
        }

    def _try_runtime(self) -> dict[str, Any] | None:
        if not self._server_url:
            return None
        try:
            request = Request(
                f"{self._server_url.rstrip('/')}/provider",
                headers={"User-Agent": "skill-manager"},
            )
            with urlopen(request, timeout=_CATALOGUE_TIMEOUT_SECONDS) as response:
                payload = response.read(_MAX_RUNTIME_BYTES)
            return parse_runtime_catalogue(payload)
        except (OSError, URLError, ValueError, KeyError):
            return None

    def _try_cache(self) -> dict[str, Any] | None:
        cache_home = self._context.env.get("XDG_CACHE_HOME") or \
            str(Path.home() / ".cache")
        cache_path = Path(cache_home) / "opencode" / "models.json"
        try:
            if not cache_path.is_file() or cache_path.stat().st_size > _MAX_CACHE_BYTES:
                return None
            return parse_cache_catalogue(cache_path.read_bytes())
        except (OSError, ValueError):
            return None


__all__ = [
    "OpenCodeAgentCatalogueService",
    "parse_cache_catalogue",
    "parse_runtime_catalogue",
]
