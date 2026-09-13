from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler
from unittest.mock import patch

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


class RuntimeSkillsClientTests(unittest.TestCase):
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
            {
                "location": {"directory": "/project"},
                "data": [
                    {
                        "name": "Release Skill",
                        "description": "A runtime skill",
                        "location": "/cache/plugin/skills/example/SKILL.md",
                        "content": "# Release Skill\n",
                    },
                    {
                        "name": "Builtin Customize",
                        "description": "Built-in skill",
                        "location": "/builtin/customize-opencode.md",
                        "content": "# Customize\n",
                        "slash": "/customize",
                    },
                    {"name": "missing content and location"},
                    {"name": "invalid description", "description": []},
                    "not an entry",
                ],
            }
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
        self.assertIn("/api/skill?", opener.request.full_url)
        self.assertIn("location%5Bdirectory%5D=%2Fproject", opener.request.full_url)
        expected_auth = "Basic " + base64.b64encode(b"user:password").decode()
        self.assertEqual(opener.request.get_header("Authorization"), expected_auth)
        self.assertEqual(opener.timeout, 5.0)
        self.assertEqual(
            [record.name for record in records],
            ["Release Skill", "Builtin Customize", "missing content and location"],
        )
        self.assertEqual(records[0].package_path, Path("/cache/plugin/skills/example"))
        self.assertEqual(records[1].package_path, None)
        self.assertEqual(records[1].slash, "/customize")

    def test_retains_file_locations_outside_the_requested_context_directory(self) -> None:
        payload = {
            "location": {"directory": "/project"},
            "data": [
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
            ],
        }

        records = _parse_runtime_skill_response(payload, allowed_directory=Path("/project"))

        self.assertEqual(
            [record.name for record in records],
            ["Plugin Cache Skill", "Builtin Customize", "No runtime document"],
        )

    def test_rejects_response_with_a_different_context_directory(self) -> None:
        with self.assertRaisesRegex(RuntimeSkillClientError, "context directory"):
            _parse_runtime_skill_response(
                {"location": {"directory": "/cache"}, "data": []},
                allowed_directory=Path("/project"),
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
                "http://127.0.0.1:4096/api/skill",
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
