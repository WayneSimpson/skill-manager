import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.harness.resolution import resolve_context
from skill_manager.opencode.agent_catalogue import (
    OpenCodeAgentCatalogueService,
    parse_cache_catalogue,
    parse_runtime_catalogue,
)

RUNTIME_FIXTURE = json.dumps({"all": [
    {
        "id": "anthropic", "name": "Anthropic", "source": "env",
        "models": {
            "claude-sonnet-4-5": {
                "id": "claude-sonnet-4-5", "providerID": "anthropic",
                "name": "Claude Sonnet 4.5", "status": "active",
                "capabilities": {"reasoning": True},
                "variants": {"low": {"reasoningEffort": "low"},
                             "high": {"reasoningEffort": "high"}},
            },
            "claude-haiku-4-5": {
                "id": "claude-haiku-4-5", "providerID": "anthropic",
                "name": "Claude Haiku 4.5", "status": "active",
                "capabilities": {"reasoning": False},
                "variants": {},
            },
        },
    },
    {
        "id": "openai", "name": "OpenAI", "source": "env",
        "models": {
            "gpt-5.4": {
                "id": "gpt-5.4", "providerID": "openai",
                "name": "GPT-5.4", "status": "active",
                "capabilities": {"reasoning": True},
                "variants": {"none": {}, "low": {}, "medium": {}, "high": {},
                             "xhigh": {}},
            },
        },
    },
]})

CACHE_FIXTURE = json.dumps({
    "deepinfra": {
        "models": {
            "Qwen/Qwen3.8-27B": {
                "id": "Qwen/Qwen3.8-27B", "status": "active",
                "capabilities": {"reasoning": True},
                "reasoning_options": [{"type": "effort", "values": ["low", "xhigh"]}],
                "variants": {"low": {}, "xhigh": {}},
            },
        },
    },
})


def context_for(root: Path):
    return resolve_context({
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "home/.config"),
        "XDG_DATA_HOME": str(root / "home/.local/share"),
        "XDG_STATE_HOME": str(root / "home/.local/state"),
        "XDG_CACHE_HOME": str(root / "home/.cache"),
    })


class RuntimeCatalogueParsingTests(unittest.TestCase):
    def test_parses_providers_models_and_model_specific_variants(self):
        result = parse_runtime_catalogue(RUNTIME_FIXTURE)
        self.assertEqual(result["source"], "runtime")
        self.assertEqual(len(result["providers"]), 2)
        self.assertEqual(result["totalModels"], 3)

        anthropic = result["providers"][0]
        self.assertEqual(anthropic["id"], "anthropic")
        self.assertEqual(anthropic["name"], "Anthropic")

        sonnet = next(m for m in anthropic["models"] if "sonnet" in m["id"])
        self.assertEqual(sonnet["id"], "anthropic/claude-sonnet-4-5")
        self.assertEqual(sonnet["name"], "Claude Sonnet 4.5")
        self.assertEqual(sonnet["variants"], ["high", "low"])
        self.assertTrue(sonnet["reasoning"])

        haiku = next(m for m in anthropic["models"] if "haiku" in m["id"])
        self.assertEqual(haiku["variants"], [])
        self.assertFalse(haiku["reasoning"])

        gpt = result["providers"][1]["models"][0]
        self.assertEqual(gpt["id"], "openai/gpt-5.4")
        self.assertEqual(gpt["variants"], ["high", "low", "medium", "none", "xhigh"])

    def test_ignores_invalid_entries(self):
        self.assertEqual(parse_runtime_catalogue('{"all": "not a list"}')["providers"], [])
        self.assertEqual(parse_runtime_catalogue("{}")["providers"], [])


class CacheCatalogueParsingTests(unittest.TestCase):
    def test_parses_local_models_json_shape(self):
        result = parse_cache_catalogue(CACHE_FIXTURE)
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["totalModels"], 1)
        model = result["providers"][0]["models"][0]
        self.assertEqual(model["id"], "deepinfra/Qwen/Qwen3.8-27B")
        self.assertEqual(model["variants"], ["low", "xhigh"])


class CatalogueServiceFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_runtime_unavailable_falls_back_to_cache(self):
        cache = self.root / "home/.cache/opencode/models.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(CACHE_FIXTURE)
        service = OpenCodeAgentCatalogueService(
            context_for(self.root), server_url="http://127.0.0.1:1",
        )
        result = service.catalogue()
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["totalModels"], 1)

    def test_both_unavailable_reports_safe_state(self):
        service = OpenCodeAgentCatalogueService(
            context_for(self.root), server_url="http://127.0.0.1:1",
        )
        result = service.catalogue()
        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["providers"], [])
        self.assertIn("custom", result["detail"])

    def test_no_server_url_uses_cache_only(self):
        cache = self.root / "home/.cache/opencode/models.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(CACHE_FIXTURE)
        service = OpenCodeAgentCatalogueService(context_for(self.root))
        result = service.catalogue()
        self.assertEqual(result["source"], "cache")

    def test_runtime_succeeds_when_available(self):
        cache = self.root / "home/.cache/opencode"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text(CACHE_FIXTURE)
        service = _RuntimeStubService(context_for(self.root), RUNTIME_FIXTURE)
        result = service.catalogue()
        self.assertEqual(result["source"], "runtime")
        self.assertEqual(result["totalModels"], 3)  # Runtime preferred over cache.


class _RuntimeStubService(OpenCodeAgentCatalogueService):
    """Test double that returns a fixed runtime payload."""

    def __init__(self, context, payload):
        super().__init__(context, server_url="http://stub")
        self._payload = payload

    def _try_runtime(self):
        return parse_runtime_catalogue(self._payload)


if __name__ == "__main__":
    unittest.main()


class ConnectedProviderTests(unittest.TestCase):
    def test_runtime_catalogue_carries_connected_flags_and_sorts(self):
        result = parse_runtime_catalogue(json.dumps({"all": [
            {"id": "disconnected-prov", "name": "D Prov", "models": {}},
            {"id": "anthropic", "name": "Anthropic", "models": {}},
        ], "connected": ["anthropic"]}))
        self.assertEqual(result["connectedProviderIds"], ["anthropic"])
        by_id = {p["id"]: p for p in result["providers"]}
        self.assertTrue(by_id["anthropic"]["connected"])
        self.assertFalse(by_id["disconnected-prov"]["connected"])
        # Connected providers sort first.
        self.assertEqual(result["providers"][0]["id"], "anthropic")

    def test_cache_catalogue_reports_connected_as_unknown(self):
        result = parse_cache_catalogue(json.dumps({
            "someprov": {"models": {}},
        }))
        self.assertIsNone(result["providers"][0]["connected"])
        self.assertEqual(result["connectedProviderIds"], [])
