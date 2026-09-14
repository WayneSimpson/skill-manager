from __future__ import annotations

import json
import subprocess
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from skill_manager.application.mcp import FileBackedMcpAdapter
from skill_manager.application.mcp.config_choice import config_choices_payload
from skill_manager.application.mcp.identity import build_identity_plan
from skill_manager.application.mcp.store import McpServerSpec, McpServerStore, McpSource
from skill_manager.errors import MutationError
from skill_manager.harness import HarnessKernelService, HarnessSupportStore
from ruamel.yaml import YAML


def _spec(name: str = "exa") -> McpServerSpec:
    return McpServerSpec(
        name=name,
        display_name=name.title(),
        source=McpSource.marketplace(f"@user/{name}"),
        transport="stdio",
        command="npx",
        args=("-y", f"{name}-mcp-server"),
        env=(("KEY", "value"),),
    )

def _load_yaml(path: Path) -> dict[str, object]:
    payload = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _adapter(
    harness: str,
    *,
    home: Path,
    xdg_config_home: Path | None = None,
) -> FileBackedMcpAdapter:
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(xdg_config_home or (home / ".config")),
        "SKILL_MANAGER_HERMES_HOME": str(home / ".hermes"),
        "HERMES_HOME": str(home / ".hermes"),
        "PATH": "",
    }
    kernel = HarnessKernelService.from_environment(
        env,
        support_store=HarnessSupportStore(home / "settings.json"),
    )
    binding = next(
        binding for binding in kernel.bindings_for_family("mcp") if binding.definition.harness == harness
    )
    return FileBackedMcpAdapter(
        definition=binding.definition,
        profile=binding.profile,
        context=kernel.context,
    )


class FileBackedMcpAdapterTests(unittest.TestCase):
    def test_opencode_discovers_each_supported_config_and_uses_it_for_writes(self) -> None:
        for relative in ('.opencode/opencode.jsonc', '.config/opencode/opencode.json', '.config/opencode/opencode.jsonc'):
            with self.subTest(relative=relative), TemporaryDirectory() as tmp:
                home = Path(tmp)
                adapter = _adapter('opencode', home=home)
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'mcp': {
                    'local': {'type': 'local', 'command': ['node', 'server.js'], 'enabled': True},
                    'remote': {'type': 'remote', 'url': 'https://example.com/mcp', 'enabled': False},
                    'local-disabled': {'type': 'local', 'command': ['node'], 'enabled': False},
                    'remote-enabled': {'type': 'remote', 'url': 'https://example.com/mcp', 'enabled': True},
                }}))
                scan = adapter.scan(())
                self.assertTrue(scan.config_present)
                self.assertEqual(scan.config_path, path)
                entries = {e.name: e for e in scan.entries}
                self.assertEqual(entries['local'].parsed_spec.transport, 'stdio')
                self.assertEqual(entries['remote'].parsed_spec.transport, 'http')
                self.assertFalse(entries['remote'].raw_payload['enabled'])
                self.assertFalse(entries['local-disabled'].raw_payload['enabled'])
                self.assertEqual(entries['local-disabled'].parsed_spec.transport, 'stdio')
                self.assertTrue(entries['remote-enabled'].raw_payload['enabled'])
                self.assertEqual(entries['remote-enabled'].parsed_spec.transport, 'http')
                adapter.enable_server(_spec())
                self.assertIn('exa', json.loads(path.read_text())['mcp'])
                self.assertEqual([p for p in home.rglob('opencode.json*') if p.suffix in {'.json', '.jsonc'}], [path])

    def test_opencode_new_install_writes_modern_jsonc_and_keeps_jsonc_read_support(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter('opencode', home=home)
            modern = home / '.config/opencode/opencode.jsonc'
            adapter.enable_server(_spec())
            self.assertEqual(adapter.config_path, modern)
            self.assertFalse((home / '.opencode/opencode.jsonc').exists())
            modern.write_text('// fixture comment\n{"theme":"test", "mcp":{"exa":{"type":"remote", "url":"https://example.com",},},}\n')
            self.assertEqual(adapter.scan(()).entries[0].parsed_spec.transport, 'http')
            adapter.enable_server(_spec())
            self.assertEqual(json.loads(modern.read_text())['theme'], 'test')

    def test_opencode_unreadable_source_does_not_hide_valid_entries_or_allow_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home / '.opencode/opencode.jsonc'
            modern = home / '.config/opencode/opencode.jsonc'
            for path in (legacy, modern):
                path.parent.mkdir(parents=True)
                path.write_text('{"mcp":{"exa":{"type":"remote","url":"https://example.com"}}}')
            read_text = Path.read_text
            def read(path, *args, **kwargs):
                if path == modern:
                    raise PermissionError('fixture unreadable')
                return read_text(path, *args, **kwargs)
            adapter = _adapter('opencode', home=home)
            before = [p.read_bytes() for p in (legacy, modern)]
            with mock.patch.object(Path, 'read_text', read):
                scan = adapter.scan(())
                self.assertEqual([e.name for e in scan.entries], ['exa'])
                self.assertIn('unreadable', scan.scan_issue)
                with self.assertRaises(MutationError):
                    adapter.enable_server(_spec())
            self.assertEqual([p.read_bytes() for p in (legacy, modern)], before)

    def test_opencode_merges_partial_overrides_and_reports_entry_source(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home / '.opencode/opencode.jsonc'
            current = home / '.config/opencode/opencode.json'
            modern = current.with_suffix('.jsonc')
            for path, payload in (
                (legacy, {'mcp': {'exa': {'type': 'local', 'command': ['node', 'old.js'], 'environment': {'A': 'one'}, 'enabled': True}}}),
                (current, {'mcp': {'exa': {'command': ['node', 'new.js'], 'environment': {'B': 'two'}}}}),
                (modern, {'mcp': {'exa': {'enabled': False}}, 'theme': 'test'}),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload))
            adapter = _adapter('opencode', home=home)
            scan = adapter.scan((_spec(),))
            entry = scan.entries[0]
            self.assertEqual(entry.parsed_spec.args, ('new.js',))
            self.assertEqual(dict(entry.parsed_spec.env), {'A': 'one', 'B': 'two'})
            self.assertFalse(entry.raw_payload['enabled'])
            choices = config_choices_payload('exa', _spec(), (scan,))
            self.assertEqual(choices[1]['configPath'], str(modern))
            # A higher file with unrelated settings is not the server's source.
            modern.write_text('{"theme":"test"}')
            choices = config_choices_payload('exa', _spec(), (adapter.scan((_spec(),)),))
            self.assertEqual(choices[1]['configPath'], str(current))
            plan = build_identity_plan((adapter.scan(()),))
            self.assertEqual(plan.groups[0].sightings[0].config_path, str(current))

    def test_opencode_reselects_write_target_when_higher_config_appears(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home / ".opencode" / "opencode.jsonc"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}")
            adapter = _adapter("opencode", home=home)
            adapter.enable_server(_spec())
            self.assertEqual(adapter.config_path, legacy)
            modern = home / ".config" / "opencode" / "opencode.jsonc"
            modern.parent.mkdir(parents=True)
            modern.write_text('{"theme":"test"}')
            adapter.enable_server(_spec())
            self.assertEqual(adapter.config_path, modern)
            self.assertIn("exa", json.loads(modern.read_text())["mcp"])
            self.assertNotIn("mcp", json.loads(legacy.read_text()))

    def test_opencode_enable_consolidates_only_target_server_then_disable_removes_all(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            paths = [home / '.opencode/opencode.jsonc', home / '.config/opencode/opencode.json', home / '.config/opencode/opencode.jsonc']
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'theme': 'test', 'mcp': {
                    'exa': {'type': 'remote', 'url': 'https://old.example.com', 'enabled': False},
                    'other': {'type': 'local', 'command': ['other']},
                }}))
            adapter = _adapter('opencode', home=home)
            adapter.enable_server(_spec())
            for path in paths:
                payload = json.loads(path.read_text())
                self.assertEqual(payload['theme'], 'test')
                self.assertIn('other', payload['mcp'])
                self.assertEqual('exa' in payload['mcp'], path == paths[-1])
            self.assertEqual(next(e for e in adapter.scan((_spec(),)).entries if e.name == 'exa').state, 'managed')
            adapter.disable_server('exa')
            self.assertFalse(adapter.has_binding('exa'))

    def test_opencode_invalid_source_retains_valid_reads_but_blocks_all_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home / '.opencode/opencode.jsonc'
            modern = home / '.config/opencode/opencode.jsonc'
            legacy.parent.mkdir(parents=True)
            modern.parent.mkdir(parents=True)
            legacy.write_text('{"mcp":{"exa":{"type":"local","command":["node"]}}}')
            modern.write_text('{ invalid jsonc')
            before = [p.read_bytes() for p in (legacy, modern)]
            adapter = _adapter('opencode', home=home)
            scan = adapter.scan(())
            self.assertEqual([e.name for e in scan.entries], ['exa'])
            self.assertIsNotNone(scan.scan_issue)
            for mutate in (lambda: adapter.enable_server(_spec()), lambda: adapter.disable_server('exa')):
                with self.assertRaises(MutationError):
                    mutate()
                self.assertEqual([p.read_bytes() for p in (legacy, modern)], before)

    def test_classifies_managed_when_content_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(_spec("exa"))
            adapter = _adapter("cursor", home=home)

            adapter.enable_server(store.get_binding_spec("exa"))  # type: ignore[arg-type]
            scan = adapter.scan(store.list_binding_specs())

            states = {entry.name: entry.state for entry in scan.entries}
            self.assertEqual(states.get("exa"), "managed")

    def test_classifies_drifted_when_user_edits_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(_spec("exa"))
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                json.dumps(
                    {"mcpServers": {"exa": {"command": "npx", "args": ["different"]}}}
                ),
                encoding="utf-8",
            )

            scan = adapter.scan(store.list_binding_specs())
            states = {entry.name: entry.state for entry in scan.entries}
            self.assertEqual(states.get("exa"), "drifted")

        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(_spec("exa"))
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                json.dumps(
                    {"mcpServers": {"exa": {"headers": {"Authorization": "Bearer x"}}}}
                ),
                encoding="utf-8",
            )

            scan = adapter.scan(store.list_binding_specs())
            drifted = next(entry for entry in scan.entries if entry.name == "exa")
            self.assertEqual(drifted.state, "drifted")
            self.assertIsNotNone(drifted.parse_issue)

    def test_classifies_unmanaged_when_no_central_spec(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                json.dumps({"mcpServers": {"legacy-foo": {"command": "ls"}}}),
                encoding="utf-8",
            )

            scan = adapter.scan(store.list_binding_specs())
            unmanaged = [entry for entry in scan.entries if entry.state == "unmanaged"]
            self.assertEqual(len(unmanaged), 1)
            self.assertEqual(unmanaged[0].name, "legacy-foo")

    def test_codex_scan_excludes_desktop_owned_node_repl(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("codex", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                """
[mcp_servers.node_repl]
command = "C:\\\\Users\\\\tester\\\\AppData\\\\Local\\\\OpenAI\\\\Codex\\\\runtimes\\\\node_repl.exe"
args = []

[mcp_servers.node_repl.env]
CODEX_CLI_PATH = "C:\\\\Codex\\\\codex.exe"
NODE_REPL_NODE_PATH = "C:\\\\Codex\\\\node.exe"
SKY_CUA_NATIVE_PIPE_DIRECTORY = "\\\\\\\\.\\\\pipe\\\\codex-test"

[mcp_servers.user-server]
command = "npx"
args = ["-y", "user-mcp"]
""".lstrip(),
                encoding="utf-8",
            )

            scan = adapter.scan(())

            self.assertEqual([entry.name for entry in scan.entries], ["user-server"])

    def test_codex_scan_keeps_user_defined_node_repl_without_desktop_fingerprint(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("codex", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                """
[mcp_servers.node_repl]
command = "node_repl"
args = ["--user-config"]
""".lstrip(),
                encoding="utf-8",
            )

            scan = adapter.scan(())

            self.assertEqual([entry.name for entry in scan.entries], ["node_repl"])

    def test_codex_scan_keeps_managed_node_repl_even_with_desktop_fingerprint(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(
                McpServerSpec(
                    name="node_repl",
                    display_name="Node REPL",
                    source=McpSource.manual("node_repl"),
                    transport="stdio",
                    command="C:\\Codex\\node_repl.exe",
                    env=(
                        ("CODEX_CLI_PATH", "C:\\Codex\\codex.exe"),
                        ("NODE_REPL_NODE_PATH", "C:\\Codex\\node.exe"),
                        ("SKY_CUA_NATIVE_PIPE_DIRECTORY", "\\\\.\\pipe\\codex-test"),
                    ),
                )
            )
            adapter = _adapter("codex", home=home)
            adapter.enable_server(store.get_binding_spec("node_repl"))  # type: ignore[arg-type]

            scan = adapter.scan(store.list_binding_specs())

            self.assertEqual([entry.name for entry in scan.entries], ["node_repl"])
            self.assertEqual(scan.entries[0].state, "managed")

    def test_managed_spec_with_no_binding_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(_spec("exa"))
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

            scan = adapter.scan(store.list_binding_specs())
            states = {entry.name: entry.state for entry in scan.entries}
            self.assertEqual(states.get("exa"), "missing")

    def test_enable_preserves_non_mcp_keys_for_json(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                json.dumps(
                    {
                        "models": ["gpt-5"],
                        "mcpServers": {"existing": {"command": "ls"}},
                    }
                ),
                encoding="utf-8",
            )

            adapter.enable_server(_spec())
            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["models"], ["gpt-5"])
            self.assertIn("existing", payload["mcpServers"])
            self.assertIn("exa", payload["mcpServers"])

    def test_enable_uses_opencode_nested_subtree(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            xdg_config_home = home / ".config"
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("opencode", home=home, xdg_config_home=xdg_config_home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                json.dumps(
                    {
                        "models": ["x"],
                        "mcp": {"other": {"type": "local", "command": ["ls"]}},
                    }
                ),
                encoding="utf-8",
            )

            adapter.enable_server(_spec())
            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["models"], ["x"])
            self.assertIn("other", payload["mcp"])
            self.assertIn("exa", payload["mcp"])
            self.assertEqual(payload["mcp"]["exa"]["type"], "local")

    def test_enable_and_disable_round_trip_for_toml(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("codex", home=home)

            adapter.enable_server(_spec())
            payload = tomllib.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcp_servers"]["exa"]["command"], "npx")
            self.assertNotIn("transport", payload["mcp_servers"]["exa"])

            adapter.disable_server("exa")
            payload = tomllib.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("mcp_servers", {}), {})

    def test_enable_and_disable_round_trip_for_hermes_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("hermes", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                "model: test-model\nmcp_servers:\n  existing:\n    command: ls\n",
                encoding="utf-8",
            )

            adapter.enable_server(_spec())
            payload = _load_yaml(adapter.config_path)
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["mcp_servers"]["existing"]["command"], "ls")
            self.assertEqual(payload["mcp_servers"]["exa"]["command"], "npx")
            self.assertEqual(payload["mcp_servers"]["exa"]["env"], {"KEY": "value"})

            adapter.disable_server("exa")
            payload = _load_yaml(adapter.config_path)
            self.assertIn("existing", payload["mcp_servers"])
            self.assertNotIn("exa", payload["mcp_servers"])

    def test_hermes_yaml_round_trip_preserves_comments_and_existing_format(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("hermes", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text(
                """# top-level comment
model: "test-model"  # inline model comment

# keep this block comment
mcp_servers:
  # existing server comment
  existing:
    command: ls  # command comment
    args: ["-la"]  # flow-style args comment

# unrelated block comment
profiles:
  default: true  # profile comment
""",
                encoding="utf-8",
            )

            adapter.enable_server(_spec())
            enabled_text = adapter.config_path.read_text(encoding="utf-8")

            self.assertIn("# top-level comment", enabled_text)
            self.assertIn("# inline model comment", enabled_text)
            self.assertIn("# keep this block comment", enabled_text)
            self.assertIn("# existing server comment", enabled_text)
            self.assertIn("# command comment", enabled_text)
            self.assertIn("# flow-style args comment", enabled_text)
            self.assertIn("# unrelated block comment", enabled_text)
            self.assertIn("# profile comment", enabled_text)
            self.assertIn("exa:", enabled_text)
            self.assertIn('model: "test-model"', enabled_text)

            adapter.disable_server("exa")
            disabled_text = adapter.config_path.read_text(encoding="utf-8")

            self.assertNotIn("  exa:", disabled_text)
            self.assertIn("# top-level comment", disabled_text)
            self.assertIn("# existing server comment", disabled_text)
            self.assertIn("# command comment", disabled_text)
            self.assertIn("# unrelated block comment", disabled_text)
            self.assertIn("existing:", disabled_text)

    def test_hermes_yaml_http_uses_headers_and_sse_transport(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("hermes", home=home)

            adapter.enable_server(
                McpServerSpec(
                    name="remote",
                    display_name="Remote",
                    source=McpSource.marketplace("@remote/server"),
                    transport="sse",
                    url="https://mcp.example.com/sse",
                    headers=(("Authorization", "Bearer token"),),
                )
            )

            payload = _load_yaml(adapter.config_path)
            remote = payload["mcp_servers"]["remote"]
            self.assertEqual(remote["url"], "https://mcp.example.com/sse")
            self.assertEqual(remote["transport"], "sse")
            self.assertEqual(remote["headers"], {"Authorization": "Bearer token"})

    def test_cursor_writes_explicit_type_for_stdio_and_http(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("cursor", home=home)

            adapter.enable_server(_spec())
            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["exa"]["type"], "stdio")

            adapter.enable_server(
                McpServerSpec(
                    name="remote",
                    display_name="Remote",
                    source=McpSource.marketplace("@remote/server"),
                    transport="http",
                    url="https://mcp.example.com",
                )
            )
            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["remote"]["type"], "http")

    def test_claude_writes_explicit_type_for_http(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("claude", home=home)

            adapter.enable_server(
                McpServerSpec(
                    name="remote",
                    display_name="Remote",
                    source=McpSource.marketplace("@remote/server"),
                    transport="http",
                    url="https://mcp.example.com",
                )
            )
            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mcpServers"]["remote"]["type"], "http")

    def test_enable_reuses_opencode_existing_xdg_json_config(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            xdg_config_home = home / ".config"
            adapter = _adapter("opencode", home=home, xdg_config_home=xdg_config_home)
            official_path = xdg_config_home / "opencode" / "opencode.json"
            official_path.parent.mkdir(parents=True, exist_ok=True)
            official_path.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "exa": {
                                "type": "remote",
                                "url": "https://old.example.com",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            adapter.enable_server(_spec())

            canonical = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            official = json.loads(official_path.read_text(encoding="utf-8"))
            self.assertIn("exa", canonical["mcp"])
            self.assertEqual(adapter.config_path, official_path)
            self.assertEqual(canonical, official)
            self.assertFalse((home / ".opencode" / "opencode.jsonc").exists())

    def test_disable_removes_opencode_from_all_discovery_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            xdg_config_home = home / ".config"
            adapter = _adapter("opencode", home=home, xdg_config_home=xdg_config_home)
            adapter.enable_server(_spec())
            official_path = xdg_config_home / "opencode" / "opencode.json"
            official_path.parent.mkdir(parents=True, exist_ok=True)
            official_path.write_text(
                json.dumps({"mcp": {"exa": {"type": "local", "command": ["npx"]}}}),
                encoding="utf-8",
            )

            adapter.disable_server("exa")

            canonical = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            official = json.loads(official_path.read_text(encoding="utf-8"))
            self.assertNotIn("mcp", canonical)
            self.assertNotIn("mcp", official)

    def test_openclaw_without_mcp_command_is_not_writable(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("openclaw", home=home)

            status = adapter.status()
            self.assertFalse(status.mcp_writable)
            self.assertIn("OpenClaw", status.mcp_unavailable_reason or "")
            with self.assertRaises(MutationError):
                adapter.enable_server(_spec())

    def test_openclaw_slow_mcp_probe_remains_writable(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("openclaw", home=home)
            with (
                mock.patch(
                    "skill_manager.application.mcp.adapters.shutil.which",
                    return_value=r"C:\bin\openclaw.CMD",
                ),
                mock.patch(
                    "skill_manager.harness.availability.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(
                        [r"C:\bin\openclaw.CMD", "mcp", "--help"],
                        timeout=5,
                    ),
                ),
            ):
                status = adapter.status()

            self.assertTrue(status.installed)
            self.assertTrue(status.mcp_writable)
            self.assertIsNone(status.mcp_unavailable_reason)

    def test_has_binding_after_enable(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("cursor", home=home)

            self.assertFalse(adapter.has_binding("exa"))
            adapter.enable_server(_spec())
            self.assertTrue(adapter.has_binding("exa"))

    def test_claude_scans_unsupported_source_project_scoped_servers(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(
                McpServerSpec(
                    name="exa",
                    display_name="Exa",
                    source=McpSource.marketplace("exa"),
                    transport="http",
                    url="https://mcp.unsupported-source.example/exa/mcp",
                )
            )
            adapter = _adapter("claude", home=home)
            adapter.config_path.write_text(
                json.dumps(
                    {
                        "projects": {
                            str(home.resolve()): {
                                "mcpServers": {
                                    "exa": {"type": "http", "url": "https://mcp.unsupported-source.example/exa/mcp"}
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            scan = adapter.scan(store.list_binding_specs())
            states = {entry.name: entry.state for entry in scan.entries}
            self.assertEqual(states.get("exa"), "managed")
            self.assertTrue(adapter.has_binding("exa"))

    def test_claude_disable_removes_project_scoped_servers(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("claude", home=home)
            adapter.config_path.write_text(
                json.dumps(
                    {
                        "projects": {
                            str(home.resolve()): {
                                "mcpServers": {
                                    "exa": {"type": "http", "url": "https://mcp.unsupported-source.example/exa/mcp"}
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            adapter.disable_server("exa")

            payload = json.loads(adapter.config_path.read_text(encoding="utf-8"))
            project = payload["projects"][str(home.resolve())]
            self.assertNotIn("mcpServers", project)

    def test_invalid_json_raises_mutation_error(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text("{not json", encoding="utf-8")

            with self.assertRaises(MutationError):
                adapter.enable_server(_spec())

    def test_unterminated_jsonc_comment_raises_mutation_error(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            adapter = _adapter("opencode", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text('{} /* broken', encoding="utf-8")

            with self.assertRaises(MutationError) as captured:
                adapter.enable_server(_spec())

        self.assertEqual(captured.exception.status, 409)
        self.assertIn("invalid JSONC", str(captured.exception))

    def test_scan_reports_malformed_config_without_raising(self) -> None:
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            store = McpServerStore(home / "manifest.json")
            store.upsert_from_spec(_spec("exa"))
            adapter = _adapter("cursor", home=home)
            adapter.config_path.parent.mkdir(parents=True, exist_ok=True)
            adapter.config_path.write_text("{not json", encoding="utf-8")

            scan = adapter.scan(store.list_binding_specs())

            self.assertIn("not valid JSON", scan.scan_issue or "")
            states = {entry.name: entry.state for entry in scan.entries}
            self.assertEqual(states["exa"], "missing")


if __name__ == "__main__":
    unittest.main()
