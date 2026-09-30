from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from skill_manager.application.skills.marketplace import MarketplaceCatalog
from skill_manager.application.skills.marketplace.client import SkillsShClient
from skill_manager.application.skills.marketplace.models import RepoDisplayMetadata
from skill_manager.application.skills.marketplace.skillssh import fetch_all_time_leaderboard, fetch_detail_page, search_skills
from skill_manager.application.skills.source_fetch import SourceFetchService
from skill_manager.errors import MARKETPLACE_UNAVAILABLE_MESSAGE, MutationError
from skill_manager.sources import ResolvedGitHubSkill, github_owner_avatar_url
from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package
from tests.support.marketplace_fixture import create_fixture_marketplace_service
from tests.support.marketplace_https_fixture import MarketplaceFixtureServer


class _FixtureResolver:
    def __init__(self, *, broken_repos: set[str] | None = None) -> None:
        self._broken_repos = broken_repos or set()

    def repo_metadata(self, repo: str) -> RepoDisplayMetadata:
        return RepoDisplayMetadata(
            stars=512 if repo == "mode-io/skills" else 84,
            image_url=github_owner_avatar_url(repo),
            default_branch="main",
        )

    def repo_metadata_for_repos(self, repos: list[str]) -> dict[str, RepoDisplayMetadata]:
        return {repo: self.repo_metadata(repo) for repo in repos}

    def close(self) -> None:
        return None

    def github_folder_url(self, repo: str, skill_id: str, *, default_branch: str | None = None) -> str | None:
        if repo in self._broken_repos:
            raise ValueError("folder resolution unavailable")
        branch = default_branch or "main"
        return f"https://github.com/{repo}/tree/{branch}/skills/{skill_id}"


class _FixtureGitHubSource:
    def __init__(self, roots: dict[str, Path], failures: set[str] | None = None) -> None:
        self._roots = roots
        self._failures = failures or set()

    def fetch(self, locator: str, work_dir: Path) -> Path:
        return self.resolve(locator, work_dir).package_path

    def resolve(self, locator: str, work_dir: Path) -> ResolvedGitHubSkill:
        if locator in self._failures:
            raise MutationError("unable to fetch source", status=400)
        root = self._roots.get(locator)
        if root is None:
            raise MutationError("unknown source", status=400)
        owner, repo_name, _skill = locator.split("/", 2)
        return ResolvedGitHubSkill(
            repo=f"{owner}/{repo_name}",
            ref="main",
            relative_path=f"skills/{root.name}",
            package_path=root,
            clone_dir=work_dir / f"{owner}--{repo_name}",
        )


def _fixture_catalog(env: dict[str, str], *, broken_repos: set[str] | None = None) -> MarketplaceCatalog:
    client = SkillsShClient.from_environment(env)
    return MarketplaceCatalog(
        leaderboard_fetcher=lambda: fetch_all_time_leaderboard(client=client),
        search_fetcher=lambda query, limit: search_skills(query, limit=limit, client=client),
        detail_fetcher=lambda detail_url: fetch_detail_page(detail_url, client=client),
        github_resolver=_FixtureResolver(broken_repos=broken_repos),
        warm_on_init=False,
    )


class SkillsMarketplaceApiTests(unittest.TestCase):
    def test_marketplace_popular_uses_https_fixture_when_trusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(marketplace=_fixture_catalog(fixture.env())) as harness:
                payload = harness.get_json("/api/marketplace/popular")

        first = payload["items"][0]
        self.assertEqual(first["name"], "Mode Switch")
        self.assertEqual(first["description"], "Switch between supported skill execution modes.")
        self.assertEqual(first["repoUrl"], "https://github.com/mode-io/skills")
        self.assertEqual(first["skillsDetailUrl"], f"{fixture.base_url}/mode-io/skills/mode-switch")
        self.assertTrue(all(item["repoLabel"] != "unsupported-source.example" for item in payload["items"]))

    def test_marketplace_search_uses_https_fixture_when_trusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(marketplace=_fixture_catalog(fixture.env())) as harness:
                payload = harness.get_json("/api/marketplace/search?q=trace")

        self.assertEqual([item["name"] for item in payload["items"]], ["Trace Scout"])
        self.assertEqual(payload["items"][0]["description"], "Review traces and highlight suspicious flows.")

    def test_marketplace_search_degrades_when_fixture_search_result_has_no_detail_page(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(marketplace=_fixture_catalog(fixture.env())) as harness:
                payload = harness.get_json("/api/marketplace/search?q=ui-ux")

        self.assertEqual(payload["items"][0]["name"], "ui-ux-pro-max")
        self.assertEqual(payload["items"][0]["repoLabel"], "broken-org/ui-ux-pro-max-skill")
        self.assertEqual(payload["items"][0]["description"], MarketplaceCatalog.DETAIL_MISSING_FALLBACK)
        self.assertTrue(all(item["repoLabel"] != "unsupported-source.example" for item in payload["items"]))

    def test_marketplace_popular_returns_503_when_fixture_is_untrusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(env_overrides={"SKILL_MANAGER_MARKETPLACE_BASE_URL": fixture.base_url}) as harness:
                payload = harness.get_json("/api/marketplace/popular", expected_status=503)

        self.assertEqual(payload["error"], MARKETPLACE_UNAVAILABLE_MESSAGE)

    def test_marketplace_search_returns_503_when_fixture_is_untrusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(env_overrides={"SKILL_MANAGER_MARKETPLACE_BASE_URL": fixture.base_url}) as harness:
                payload = harness.get_json("/api/marketplace/search?q=trace", expected_status=503)

        self.assertEqual(payload["error"], MARKETPLACE_UNAVAILABLE_MESSAGE)

    def test_marketplace_detail_uses_https_fixture_and_handles_folder_resolution_failure(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(
                marketplace=_fixture_catalog(fixture.env(), broken_repos={"vercel-labs/skills"}),
            ) as harness:
                payload = harness.get_json("/api/marketplace/items/skillssh%3Avercel-labs%2Fskills%3Atrace-scout")

        self.assertEqual(payload["name"], "Trace Scout")
        self.assertEqual(payload["description"], "Review traces and highlight suspicious flows.")
        self.assertEqual(payload["sourceLinks"]["repoUrl"], "https://github.com/vercel-labs/skills")
        self.assertEqual(payload["sourceLinks"]["folderUrl"], None)
        self.assertEqual(payload["sourceLinks"]["skillsDetailUrl"], f"{fixture.base_url}/vercel-labs/skills/trace-scout")

    def test_marketplace_detail_degrades_when_summary_preview_is_missing(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(marketplace=_fixture_catalog(fixture.env())) as harness:
                payload = harness.get_json("/api/marketplace/items/skillssh%3Abroken-org%2Fui-ux-pro-max-skill%3Aui-ux-pro-max")

        self.assertEqual(payload["name"], "ui-ux-pro-max")
        self.assertEqual(payload["description"], MarketplaceCatalog.DETAIL_MISSING_FALLBACK)
        self.assertEqual(payload["sourceLinks"]["repoUrl"], "https://github.com/broken-org/ui-ux-pro-max-skill")

    def test_marketplace_detail_returns_503_when_fixture_is_untrusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(env_overrides={"SKILL_MANAGER_MARKETPLACE_BASE_URL": fixture.base_url}) as harness:
                payload = harness.get_json("/api/marketplace/items/skillssh%3Amode-io%2Fskills%3Amode-switch", expected_status=503)

        self.assertEqual(payload["error"], MARKETPLACE_UNAVAILABLE_MESSAGE)

    def test_marketplace_detail_returns_404_for_unknown_item(self) -> None:
        with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
            payload = harness.get_json("/api/marketplace/items/skillssh%3Aunknown%2Frepo%3Anope", expected_status=404)

        self.assertIn("unknown marketplace item", payload["error"])

    def test_marketplace_detail_returns_404_for_filtered_unsupported_source(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(marketplace=_fixture_catalog(fixture.env())) as harness:
                payload = harness.get_json("/api/marketplace/items/skillssh%3Aunsupported-source.example%3Aui-ux-pro-max", expected_status=404)

        self.assertIn("unknown marketplace item", payload["error"])

    def test_marketplace_document_uses_source_fetcher_and_gracefully_handles_failures(self) -> None:
        with MarketplaceFixtureServer() as fixture, TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good_root = seed_skill_package(root / "sources", "mode-switch", "Mode Switch", body="Use when marketplace tests run.")
            source_fetcher = SourceFetchService(
                github=_FixtureGitHubSource(
                    {
                        "mode-io/skills/mode-switch": good_root,
                    },
                    failures={"vercel-labs/skills/trace-scout"},
                ),
            )
            with AppTestHarness(
                marketplace=_fixture_catalog(fixture.env()),
                source_fetcher=source_fetcher,
            ) as harness:
                ready_payload = harness.get_json("/api/marketplace/items/skillssh%3Amode-io%2Fskills%3Amode-switch/document")
                unavailable_payload = harness.get_json("/api/marketplace/items/skillssh%3Avercel-labs%2Fskills%3Atrace-scout/document")

        self.assertEqual(ready_payload["status"], "ready")
        self.assertIn("marketplace tests run", ready_payload["documentMarkdown"])
        self.assertEqual(unavailable_payload, {"status": "unavailable", "documentMarkdown": None})

    def test_marketplace_document_returns_503_when_fixture_is_untrusted(self) -> None:
        with MarketplaceFixtureServer() as fixture:
            with AppTestHarness(env_overrides={"SKILL_MANAGER_MARKETPLACE_BASE_URL": fixture.base_url}) as harness:
                payload = harness.get_json(
                    "/api/marketplace/items/skillssh%3Amode-io%2Fskills%3Amode-switch/document",
                    expected_status=503,
                )

        self.assertEqual(payload["error"], MARKETPLACE_UNAVAILABLE_MESSAGE)

    def test_marketplace_detail_returns_preview_payload_without_internal_source_fields(self) -> None:
        with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
            payload = harness.get_json("/api/marketplace/items/skillssh%3Amode-io%2Fskills%3Amode-switch")

        self.assertEqual(payload["name"], "Mode Switch")
        self.assertEqual(payload["sourceLinks"]["repoLabel"], "mode-io/skills")
        self.assertEqual(payload["sourceLinks"]["repoUrl"], "https://github.com/mode-io/skills")
        self.assertEqual(payload["sourceLinks"]["skillsDetailUrl"], "https://skills.sh/mode-io/skills/mode-switch")
        self.assertNotIn("sourceLocator", payload)
        self.assertNotIn("sourceKind", payload)
        self.assertNotIn("documentMarkdown", payload)

    def test_marketplace_search_rejects_short_queries(self) -> None:
        with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
            payload = harness.get_json("/api/marketplace/search?q=a", expected_status=400)

        self.assertIn("Enter at least 2 characters", payload["error"])

    def test_marketplace_install_requires_install_token(self) -> None:
        with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
            payload = harness.post_json("/api/marketplace/install", {}, expected_status=422)

        self.assertIn("installToken", payload["error"])


class MarketplaceInstallConsolidationTests(unittest.TestCase):
    """Marketplace install runs end-to-end on the consolidated archive path."""

    REVISION = "b" * 40

    @staticmethod
    def _install_token(kind: str, locator: str) -> str:
        raw = json.dumps([kind, locator]).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _zip_entry(archive: ZipFile, name: str, content: str) -> None:
        # The bounded extractor requires explicit regular-file attributes.
        info = ZipInfo("fixture-repo-main/" + name)
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, content)

    def _archive(self) -> bytes:
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            self._zip_entry(
                archive,
                "skills/mode-switch/SKILL.md",
                "---\nname: mode-switch\ndescription: Mode Switch skill\n---\n"
                "Switches modes when marketplace install tests run.",
            )
        return buffer.getvalue()

    def _serve_github(self, url: str, **_kwargs) -> bytes:
        if "/commits/" in url:
            return json.dumps({"sha": self.REVISION}).encode()
        if "codeload.github.com" in url:
            return self._archive()
        raise AssertionError(f"unexpected public source URL: {url}")

    def test_marketplace_install_uses_bounded_archive_acquisition(self) -> None:
        with patch("skill_manager.sources.github.read_public_bytes") as read:
            read.side_effect = self._serve_github
            with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
                token = self._install_token("github", "github:mode-io/fixture-repo/mode-switch")
                result = harness.post_json("/api/marketplace/install", {"installToken": token})
                self.assertEqual(result, {"ok": True})

                rows = harness.get_json("/api/skills")["rows"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["name"], "mode-switch")
                # The consolidated path records the exact resolved commit as
                # the standalone skill's source ref in the store.
                scan = harness.container.skills_read_models.store.scan()
                self.assertEqual(len(scan.packages), 1)
                package = scan.packages[0]
                self.assertEqual(package.package.source.kind, "github")
                self.assertEqual(
                    package.package.source.locator,
                    "github:mode-io/fixture-repo/mode-switch")
                self.assertEqual(package.recorded_source_ref, self.REVISION)

    def test_marketplace_install_reports_ambiguous_skill_matches(self) -> None:
        def serve_ambiguous(url: str, **_kwargs) -> bytes:
            if "/commits/" in url:
                return json.dumps({"sha": self.REVISION}).encode()
            if "codeload.github.com" in url:
                buffer = io.BytesIO()
                with ZipFile(buffer, "w") as archive:
                    self._zip_entry(
                        archive, "a/mode-switch/SKILL.md",
                        "---\nname: mode-switch\ndescription: duplicate one\n---\nbody")
                    self._zip_entry(
                        archive, "b/mode-switch/SKILL.md",
                        "---\nname: mode-switch\ndescription: duplicate two\n---\nbody")
                return buffer.getvalue()
            raise AssertionError(f"unexpected public source URL: {url}")

        with patch("skill_manager.sources.github.read_public_bytes") as read:
            read.side_effect = serve_ambiguous
            with AppTestHarness(marketplace=create_fixture_marketplace_service()) as harness:
                token = self._install_token("github", "github:mode-io/fixture-repo/mode-switch")
                payload = harness.post_json(
                    "/api/marketplace/install", {"installToken": token}, expected_status=400)
                self.assertIn("matches multiple locations", payload["error"])
                self.assertEqual(harness.get_json("/api/skills")["rows"], [])


if __name__ == "__main__":
    unittest.main()
