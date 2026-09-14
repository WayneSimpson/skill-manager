from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler
from unittest.mock import Mock, patch

from skill_manager.application.skills.runtime import (
    OpenCodeRuntimeSkillsClient,
    RuntimeSkillClientError,
    RuntimeSkillRecord,
    RuntimeSkillSnapshotStore,
    RuntimeSkillsService,
    _parse_runtime_skill_response,
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getheader(self, name: str):
        return str(len(self.body)) if name.casefold() == "content-length" else None

    def read(self, _size: int = -1) -> bytes:
        return self.body


class _FakeOpener:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, *, timeout: float):
        self.request = request
        self.timeout = timeout
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class RuntimeSnapshotPersistenceTests(unittest.TestCase):
    def test_restore_replace_error_and_clear_without_network_or_credentials(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "state" / "runtime.json"
            client = Mock()
            records = (RuntimeSkillRecord("Saved", "", Path("/plugin/skill/SKILL.md"), "body"),)
            client.fetch.return_value = records
            service = RuntimeSkillsService(store=RuntimeSkillSnapshotStore(path), client=client)
            ready = service.refresh(server_url="http://localhost:4096", directory=Path("/project"),
                                    username="private-user", password="private-password")
            self.assertFalse(ready["stale"])
            self.assertIsNotNone(ready["lastRefreshed"])
            saved = path.read_bytes()
            self.assertNotIn(b"private-user", saved)
            self.assertNotIn(b"private-password", saved)
            self.assertEqual(set(json.loads(saved)), {"version", "records", "directory", "serverUrl", "lastRefreshed"})

            client.reset_mock()
            restored = RuntimeSkillsService(store=RuntimeSkillSnapshotStore(path), client=client)
            self.assertEqual(restored.records(), records)
            self.assertTrue(restored.status()["stale"])
            self.assertEqual(restored.status()["lastRefreshed"], ready["lastRefreshed"])
            client.fetch.assert_not_called()

            client.fetch.side_effect = RuntimeSkillClientError("runtime skill server returned HTTP 401")
            with self.assertRaises(RuntimeSkillClientError):
                restored.refresh(server_url="http://localhost:4097", directory=Path("/other"), username=None, password=None)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(restored.records(), records)
            self.assertEqual(restored.status()["status"], "error")
            self.assertEqual(restored.status()["serverUrl"], ready["serverUrl"])
            self.assertEqual(restored.status()["lastRefreshed"], ready["lastRefreshed"])
            self.assertTrue(restored.status()["stale"])

            client.fetch.side_effect = None
            client.fetch.return_value = ()
            replaced = restored.refresh(server_url="http://localhost:4097", directory=Path("/other"), username=None, password=None)
            self.assertFalse(replaced["stale"])
            self.assertIsNone(replaced["error"])
            self.assertEqual(RuntimeSkillSnapshotStore(path).records(), ())
            self.assertEqual(RuntimeSkillSnapshotStore(path).status()["directory"], "/other")
            restored.disconnect()
            self.assertFalse(path.exists())
            self.assertEqual(RuntimeSkillSnapshotStore(path).status()["status"], "disconnected")

    def test_corrupt_snapshot_does_not_prevent_startup(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"
            for contents in ("{broken", '{"version": 1, "serverUrl": null}'):
                path.write_text(contents)
                store = RuntimeSkillSnapshotStore(path)
                self.assertEqual(store.records(), ())
                self.assertEqual(store.status()["status"], "error")
                self.assertTrue(store.status()["stale"])

    def test_save_failure_retains_last_successful_snapshot(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "runtime.json"
            store = RuntimeSkillSnapshotStore(path)
            store.replace(records=(), server_url="http://localhost:4096", directory="/original")
            before = path.read_bytes()
            service = RuntimeSkillsService(store=store, client=Mock())
            service.client.fetch.return_value = (RuntimeSkillRecord("New", "", None, "body"),)
            with patch("skill_manager.application.skills.runtime.atomic_write_text", side_effect=OSError):
                with self.assertRaises(RuntimeSkillClientError):
                    service.refresh(server_url="http://localhost:4097", directory=Path("/other"), username=None, password=None)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.records(), ())
            self.assertEqual(store.status()["directory"], "/original")
            self.assertTrue(store.status()["stale"])


class RuntimeSkillsClientTests(unittest.TestCase):
    def test_uses_agent_registry_with_plugin_skill_not_v2_registry(self) -> None:
        calls = []

        class DifferentRegistries:
            def open(self, request, *, timeout):
                calls.append(request.full_url)
                if "/api/skill?" in request.full_url:
                    return _FakeResponse(b'{"location":{"directory":"/project"},"data":[]}')
                return _FakeResponse(json.dumps([{
                    "name": "n8n-agents-official", "description": "Plugin-provided skill",
                    "location": "/plugins/example/skills/n8n-agents-official/SKILL.md",
                    "content": "# Plugin skill",
                }]).encode())

        client = OpenCodeRuntimeSkillsClient(opener=DifferentRegistries())
        records = client.fetch(server_url="http://127.0.0.1:4096", directory=Path("/project"), username=None, password=None)
        self.assertEqual([r.name for r in records], ["n8n-agents-official"])
        self.assertEqual(calls, ["http://127.0.0.1:4096/skill?directory=%2Fproject"])

    def test_default_opener_disables_environment_proxies_without_network_access(self) -> None:
        proxy_env = {
            "HTTP_PROXY": "http://198.51.100.10:8080",
            "HTTPS_PROXY": "http://198.51.100.10:8080",
            "http_proxy": "http://198.51.100.10:8080",
            "https_proxy": "http://198.51.100.10:8080",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        with patch.dict(os.environ, proxy_env):
            client = OpenCodeRuntimeSkillsClient()

        proxy_handlers = [handler for handler in client._opener.handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(proxy_handlers, [])

    def test_fetches_release_api_with_basic_auth_and_ignores_invalid_entries(self) -> None:
        body = json.dumps(
            [
                    {
                        "name": "Release Skill",
                        "description": "A runtime skill",
                        "location": "/cache/plugin/skills/example/SKILL.md",
                        "content": "# Release Skill\n",
                    },
                    {
                        "name": "Builtin Customize",
                        "description": "Built-in skill",
                        "location": "<built-in>",
                        "content": "# Customize\n",
                        "slash": "/customize",
                    },
                    {"name": "missing content and location"},
                    {"name": "invalid description", "description": []},
                    "not an entry",
            ]
        ).encode()
        opener = _FakeOpener(_FakeResponse(body))
        client = OpenCodeRuntimeSkillsClient(opener=opener)

        records = client.fetch(
            server_url="http://127.0.0.1:4096",
            directory=Path("/project"),
            username="user",
            password="password",
        )

        assert opener.request is not None
        self.assertEqual("http://127.0.0.1:4096/skill?directory=%2Fproject", opener.request.full_url)
        expected_auth = "Basic " + base64.b64encode(b"user:password").decode()
        self.assertEqual(opener.request.get_header("Authorization"), expected_auth)
        self.assertEqual(opener.timeout, 5.0)
        self.assertEqual(
            [record.name for record in records],
            ["Release Skill", "Builtin Customize", "missing content and location"],
        )
        self.assertEqual(records[0].package_path, Path("/cache/plugin/skills/example"))
        self.assertEqual(records[1].package_path, None)
        self.assertEqual(records[1].location, None)
        self.assertEqual(records[1].content, "# Customize\n")
        self.assertEqual(records[1].slash, "/customize")

    def test_retains_file_locations_outside_the_requested_context_directory(self) -> None:
        payload = [
                {
                    "name": "Plugin Cache Skill",
                    "description": "",
                    "location": "/cache/plugin/skills/example/SKILL.md",
                },
                {
                    "name": "Builtin Customize",
                    "description": "",
                    "location": "/builtin/customize-opencode.md",
                    "content": "# Content only",
                },
                {"name": "No runtime document"},
        ]

        records = _parse_runtime_skill_response(payload)

        self.assertEqual(
            [record.name for record in records],
            ["Plugin Cache Skill", "Builtin Customize", "No runtime document"],
        )

    def test_rejects_v2_wrapper_instead_of_treating_it_as_agent_inventory(self) -> None:
        with self.assertRaisesRegex(RuntimeSkillClientError, "invalid response"):
            _parse_runtime_skill_response(
                {"location": {"directory": "/cache"}, "data": []},
            )

    def test_rejects_non_loopback_server_without_opening_network(self) -> None:
        opener = _FakeOpener(_FakeResponse(b"{}"))
        client = OpenCodeRuntimeSkillsClient(opener=opener)

        with self.assertRaises(RuntimeSkillClientError):
            client.fetch(
                server_url="https://example.com",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

        self.assertIsNone(opener.request)

    def test_rejects_url_credentials_without_retaining_the_password(self) -> None:
        store = RuntimeSkillSnapshotStore()
        service = RuntimeSkillsService(store=store, client=_FakeOpener(_FakeResponse(b"{}")))

        with self.assertRaises(RuntimeSkillClientError):
            service.refresh(
                server_url="http://user:secret@127.0.0.1:4096",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

        self.assertNotIn("secret", str(service.status()))

    def test_blocks_redirects(self) -> None:
        from skill_manager.application.skills.runtime import _NoRedirectHandler

        with self.assertRaisesRegex(RuntimeSkillClientError, "redirects"):
            _NoRedirectHandler().redirect_request(None, None, 302, "redirect", {}, "http://127.0.0.1")

    def test_reports_http_source_failure_without_returning_response_body(self) -> None:
        opener = _FakeOpener(
            HTTPError(
                "http://127.0.0.1:4096/skill",
                503,
                "unavailable",
                {},
                None,
            )
        )
        client = OpenCodeRuntimeSkillsClient(opener=opener)

        with self.assertRaisesRegex(RuntimeSkillClientError, "HTTP 503"):
            client.fetch(
                server_url="http://127.0.0.1:4096",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

    def test_reports_timeout_and_rejects_oversized_response(self) -> None:
        timeout_client = OpenCodeRuntimeSkillsClient(opener=_FakeOpener(TimeoutError()))
        with self.assertRaisesRegex(RuntimeSkillClientError, "timed out"):
            timeout_client.fetch(
                server_url="http://127.0.0.1:4096",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

        oversized_client = OpenCodeRuntimeSkillsClient(
            opener=_FakeOpener(_FakeResponse(b"x" * 11)),
            max_response_bytes=10,
        )
        with self.assertRaisesRegex(RuntimeSkillClientError, "too large"):
            oversized_client.fetch(
                server_url="http://127.0.0.1:4096",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

    def test_snapshot_service_preserves_previous_records_on_fetch_failure(self) -> None:
        record = RuntimeSkillRecord(
            name="Existing Runtime Skill",
            description="",
            location=None,
            content="# Existing Runtime Skill",
        )

        class _FailingClient:
            def fetch(self, **_kwargs):
                raise RuntimeSkillClientError("runtime server timed out")

        store = RuntimeSkillSnapshotStore()
        store.replace(
            records=(record,),
            server_url="http://127.0.0.1:4096",
            directory="/tmp/plugins",
        )
        service = RuntimeSkillsService(store=store, client=_FailingClient())

        with self.assertRaises(RuntimeSkillClientError):
            service.refresh(
                server_url="http://127.0.0.1:4096",
                directory=Path("/tmp/plugins"),
                username=None,
                password=None,
            )

        self.assertEqual([item.name for item in service.records()], ["Existing Runtime Skill"])
        self.assertEqual(service.status()["status"], "error")
        self.assertEqual(service.status()["skillCount"], 1)

    def test_fetch_failure_preserves_provenance_for_retained_records(self) -> None:
        record = RuntimeSkillRecord("Existing Runtime Skill", "", None, "# Existing")

        class _FailingClient:
            def fetch(self, **_kwargs):
                raise RuntimeSkillClientError("runtime server timed out")

        store = RuntimeSkillSnapshotStore()
        store.replace(
            records=(record,),
            server_url="http://127.0.0.1:4096",
            directory="/project",
        )
        service = RuntimeSkillsService(store=store, client=_FailingClient())

        with self.assertRaises(RuntimeSkillClientError):
            service.refresh(
                server_url="http://127.0.0.1:4097",
                directory=Path("/other-project"),
                username=None,
                password=None,
            )

        status = service.status()
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["serverUrl"], "http://127.0.0.1:4096")
        self.assertEqual(status["directory"], "/project")


if __name__ == "__main__":
    unittest.main()
