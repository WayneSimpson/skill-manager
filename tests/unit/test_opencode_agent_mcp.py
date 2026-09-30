"""Unit tests for OpenCode MCP server discovery and wildcard association."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.harness.resolution import resolve_context
from skill_manager.opencode.agent_mcp import (
    OpenCodeMcpService,
    canonical_mcp_wildcard,
    mcp_wildcard_matches,
    parse_config_mcp,
    parse_runtime_mcp,
)
from skill_manager.application.container import build_backend_container


class FakeMcpRuntime(BaseHTTPRequestHandler):
    payload = "{}"
    tool_ids_payload: bytes | str | None = None  # None → 404 (endpoint absent).

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/experimental/tool/ids":
            if self.tool_ids_payload is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = (self.tool_ids_payload if isinstance(self.tool_ids_payload, bytes)
                    else self.tool_ids_payload.encode())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = self.payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # Silence request logging.
        pass


class WildcardHelperTests(unittest.TestCase):
    def test_canonical_wildcard_uses_configured_name_verbatim(self):
        self.assertEqual(canonical_mcp_wildcard("n8n_nccio"), "n8n_nccio_*")
        self.assertEqual(canonical_mcp_wildcard("ms365-mail"), "ms365-mail_*")

    def test_matching_accepts_canonical_and_legacy_forms_only(self):
        self.assertTrue(mcp_wildcard_matches("clickup_*", "clickup"))
        self.assertTrue(mcp_wildcard_matches("playwright*", "playwright"))  # Legacy.
        self.assertFalse(mcp_wildcard_matches("clickup_create_task", "clickup"))
        self.assertFalse(mcp_wildcard_matches("clickup_*", "clickup2"))


class ParseRuntimeMcpTests(unittest.TestCase):
    def test_live_shape_parsed_with_status_and_error(self):
        # Exact live 1.18.32 payload shape (read-only observation).
        result = parse_runtime_mcp(json.dumps({
            "clickup": {"status": "connected"},
            "playwright": {"status": "failed", "error": "MCP error -32000: Connection closed"},
            "aimfox": {"status": "connected"},
            "n8n_nccio": {"status": "connected"},
        }))
        self.assertEqual(result["source"], "runtime")
        by_name = {s["name"]: s for s in result["servers"]}
        self.assertEqual(by_name["clickup"]["status"], "connected")
        self.assertEqual(by_name["clickup"]["wildcard"], "clickup_*")
        self.assertEqual(by_name["playwright"]["status"], "failed")
        self.assertIn("Connection closed", by_name["playwright"]["error"])
        self.assertEqual(by_name["n8n_nccio"]["wildcard"], "n8n_nccio_*")

    def test_unknown_and_future_statuses_pass_through(self):
        result = parse_runtime_mcp({"srv": {"status": "needs-auth"}})
        self.assertEqual(result["servers"][0]["status"], "needs-auth")

    def test_plain_string_entry_treated_as_status(self):
        result = parse_runtime_mcp({"srv": "connected"})
        self.assertEqual(result["servers"][0]["status"], "connected")

    def test_no_server_produces_empty_list(self):
        result = parse_runtime_mcp("{}")
        self.assertEqual(result["servers"], [])


class ParseConfigMcpTests(unittest.TestCase):
    def test_configured_names_derived_with_unknown_status(self):
        result = parse_config_mcp({"mcp": {"context7": {"type": "http"}}})
        self.assertEqual(result["source"], "config")
        self.assertEqual(result["servers"][0]["status"], "unknown")
        self.assertEqual(result["servers"][0]["wildcard"], "context7_*")

    def test_disabled_entry_reported_as_disabled(self):
        result = parse_config_mcp({"mcp": {"old": {"enabled": False}}})
        self.assertEqual(result["servers"][0]["status"], "disabled")

    def test_v2_nested_servers_map_supported_without_fake_servers_name(self):
        # Documented V2 config shape: mcp.servers.{name}
        result = parse_config_mcp({"mcp": {"servers": {
            "context7": {"type": "http"},
            "clickup": {"type": "http"},
        }}})
        names = [s["name"] for s in result["servers"]]
        self.assertEqual(names, ["clickup", "context7"])  # Sorted, no "servers".
        self.assertNotIn("servers", names)
        self.assertTrue(all(s["status"] == "unknown" for s in result["servers"]))

    def test_v2_nested_disabled_semantics_preserved(self):
        result = parse_config_mcp({"mcp": {"servers": {
            "old": {"enabled": False},
        }}})
        self.assertEqual(result["servers"][0]["status"], "disabled")

    def test_mixed_shapes_prefer_nested_v2_and_avoid_duplicates(self):
        result = parse_config_mcp({"mcp": {
            "direct-only": {"type": "http"},
            "shared": {"type": "http"},  # Also nested; nested must win per name.
            "servers": {
                "shared": {"enabled": False},  # Nested wins → disabled.
                "nested-only": {"type": "http"},
            },
        }})
        by_name = {s["name"]: s for s in result["servers"]}
        self.assertEqual(set(by_name), {"direct-only", "shared", "nested-only"})
        self.assertEqual(by_name["shared"]["status"], "disabled")  # Nested wins.
        self.assertNotIn("servers", by_name)

    def test_missing_or_invalid_mcp_section_yields_no_servers(self):
        self.assertEqual(parse_config_mcp(None)["servers"], [])
        self.assertEqual(parse_config_mcp({"mcp": "broken"})["servers"], [])
        self.assertEqual(parse_config_mcp({"mcp": {"servers": "broken"}})["servers"], [])


def base_env(root: Path) -> dict[str, str]:
    return {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "home" / ".config"),
        "XDG_DATA_HOME": str(root / "home" / ".local" / "share"),
        "XDG_STATE_HOME": str(root / "home" / ".local" / "state"),
        "XDG_CACHE_HOME": str(root / "home" / ".cache"),
    }


class McpServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _seed_config_mcp(self, names: list[str]) -> None:
        config = self.root / "home" / ".config" / "opencode"
        config.mkdir(parents=True, exist_ok=True)
        mcp = {name: {"type": "http", "url": "https://example.test"} for name in names}
        (config / "opencode.jsonc").write_text(json.dumps({"mcp": mcp}))

    def _serve(self, payload: dict) -> str:
        FakeMcpRuntime.payload = json.dumps(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcpRuntime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_runtime_listed_with_status(self):
        service = OpenCodeMcpService(
            resolve_context(base_env(self.root)),
            server_url=self._serve({
                "clickup": {"status": "connected"},
                "playwright": {"status": "failed", "error": "boom"},
            }),
        )
        result = service.servers()
        self.assertEqual(result["source"], "runtime")
        by_name = {s["name"]: s for s in result["servers"]}
        self.assertEqual(by_name["clickup"]["status"], "connected")
        self.assertEqual(by_name["playwright"]["status"], "failed")

    def test_runtime_unavailable_falls_back_to_config(self):
        self._seed_config_mcp(["context7", "twenty"])
        service = OpenCodeMcpService(
            resolve_context(base_env(self.root)),
            server_url="http://127.0.0.1:1",  # Unreachable.
        )
        result = service.servers()
        self.assertEqual(result["source"], "config")
        self.assertEqual(
            [s["name"] for s in result["servers"]], ["context7", "twenty"])

    def test_no_url_uses_config_without_probing(self):
        self._seed_config_mcp(["aimfox"])
        service = OpenCodeMcpService(resolve_context(base_env(self.root)))
        result = service.servers()
        self.assertEqual(result["source"], "config")
        self.assertEqual(result["servers"][0]["name"], "aimfox")

    def test_unavailable_when_no_runtime_and_no_config(self):
        service = OpenCodeMcpService(resolve_context(base_env(self.root)))
        result = service.servers()
        self.assertEqual(result["source"], "unavailable")
        self.assertIn("Advanced", result["detail"])

    def test_container_mcp_service_shares_runtime_url_derivation(self):
        # The container builds the MCP service with the same derived runtime
        # URL as the model catalogue (env override wins over snapshot).
        self._seed_config_mcp(["clickup"])
        container = build_backend_container(
            {**base_env(self.root),
             "SKILL_MANAGER_OPENCODE_SERVER_URL": self._serve({"clickup": {"status": "connected"}})},
            marketplace_catalog=None,
            mcp_marketplace_catalog=None,
        )
        result = container.opencode_mcp_servers.servers()
        self.assertEqual(result["source"], "runtime")
        self.assertEqual(result["servers"][0]["name"], "clickup")


class RuntimeToolIdsTests(unittest.TestCase):
    """`GET /experimental/tool/ids` is consulted read-only as metadata."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _serve(self):
        FakeMcpRuntime.payload = json.dumps({"clickup": {"status": "connected"}})
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcpRuntime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _service(self, url: str) -> OpenCodeMcpService:
        return OpenCodeMcpService(resolve_context(base_env(self.root)), server_url=url)

    def test_tool_ids_observed_live_shape_are_surfaced(self):
        # Exact live 1.18.32 shape: a JSON array of built-in tool IDs.
        FakeMcpRuntime.tool_ids_payload = json.dumps(
            ["invalid", "question", "bash", "read", "glob"])
        result = self._service(self._serve()).servers()
        self.assertEqual(result["runtimeToolIds"], ["bash", "glob", "invalid", "question", "read"])

    def test_missing_tool_ids_endpoint_degrades_gracefully(self):
        FakeMcpRuntime.tool_ids_payload = None  # 404
        result = self._service(self._serve()).servers()
        self.assertEqual(result["source"], "runtime")
        self.assertNotIn("runtimeToolIds", result)

    def test_invalid_tool_ids_shape_is_dropped(self):
        FakeMcpRuntime.tool_ids_payload = json.dumps({"not": "a list"})
        result = self._service(self._serve()).servers()
        self.assertNotIn("runtimeToolIds", result)

    def test_tool_ids_absent_without_runtime(self):
        FakeMcpRuntime.tool_ids_payload = '["bash"]'
        service = OpenCodeMcpService(resolve_context(base_env(self.root)))  # No URL.
        result = service.servers()
        self.assertEqual(result["source"], "unavailable")
        self.assertNotIn("runtimeToolIds", result)


from skill_manager.opencode.agent_permissions import (
    from_generation,
    to_generation,
)


class McpPermissionRoundTripTests(unittest.TestCase):
    """MCP wildcard rules must round-trip losslessly in both generations."""

    def test_v1_mcp_wildcards_round_trip_verbatim(self):
        raw = {
            "n8n_nccio_*": "allow",
            "playwright*": "ask",  # Legacy no-underscore form preserved.
            "orphan_tool_*": "deny",  # Not a configured server; still preserved.
            "read": "allow",
        }
        rules = to_generation("v1", raw)
        self.assertEqual(from_generation("v1", rules), raw)

    def test_v1_changing_only_one_wildcard_leaves_others_untouched(self):
        from dataclasses import replace
        raw = {"n8n_nccio_*": "allow", "playwright*": "ask", "read": "allow"}
        rules = to_generation("v1", raw)
        rules = [
            replace(rule, effect="deny") if rule.action == "n8n_nccio_*" else rule
            for rule in rules
        ]
        result = from_generation("v1", rules)
        self.assertEqual(result["n8n_nccio_*"], "deny")
        self.assertEqual(result["playwright*"], "ask")
        self.assertEqual(result["read"], "allow")

    def test_v2_mcp_rules_preserve_order_and_unknown_fields(self):
        raw = [
            {"action": "n8n_nccio_*", "effect": "allow", "priority": 5},
            {"action": "read", "effect": "deny"},
            {"action": "clickup_*", "effect": "ask"},
        ]
        rules = to_generation("v2", raw)
        result = from_generation("v2", rules)
        self.assertEqual(result, raw)  # Order and priority field preserved.

    def test_no_silent_v1_to_v2_migration(self):
        raw = {"n8n_nccio_*": "allow"}
        v1 = from_generation("v1", to_generation("v1", raw))
        self.assertEqual(v1, raw)  # Stays a dict, never a rules list.


if __name__ == "__main__":
    unittest.main()
