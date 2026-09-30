from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from skill_manager.application.skills.marketplace.skillssh import (
    extract_detail_description,
    normalize_skill,
    parse_homepage_leaderboard,
    raw_skill_from_payload,
)
from skill_manager.sources.github import (
    GitHubSource,
    ResolvedGitHubSkill,
    _parse_locator,
    github_folder_url,
    github_owner_avatar_url,
    github_repo_from_locator,
    is_valid_github_repo,
    matching_skill_roots,
)

from tests.support.fake_home import seed_skill_package


class ParseLocatorTests(unittest.TestCase):
    def test_parse_three_part_locator(self) -> None:
        owner, repo, skill_dir = _parse_locator("anthropics/skills/commit-message")
        self.assertEqual(owner, "anthropics")
        self.assertEqual(repo, "skills")
        self.assertEqual(skill_dir, "commit-message")

    def test_parse_locator_preserves_nested_skill_hint(self) -> None:
        owner, repo, skill_dir = _parse_locator("vercel-labs/agent-browser/agent-browser")
        self.assertEqual(owner, "vercel-labs")
        self.assertEqual(repo, "agent-browser")
        self.assertEqual(skill_dir, "agent-browser")

    def test_parse_rejects_two_parts(self) -> None:
        with self.assertRaises(ValueError):
            _parse_locator("anthropics/skills")

    def test_github_repo_from_locator(self) -> None:
        self.assertEqual(github_repo_from_locator("github:anthropics/skills/commit-message"), "anthropics/skills")

    def test_github_repo_from_two_part_locator(self) -> None:
        self.assertEqual(github_repo_from_locator("github:mode-io/shared-audit"), "mode-io/shared-audit")

    def test_github_repo_from_nested_locator(self) -> None:
        self.assertEqual(github_repo_from_locator("github:vercel-labs/agent-browser/skills/agent-browser"), "vercel-labs/agent-browser")

    def test_github_folder_url_omits_repo_root(self) -> None:
        self.assertIsNone(github_folder_url("mode-io/shared-audit", ref="main", relative_path="."))

    def test_github_folder_url_uses_exact_relative_path(self) -> None:
        self.assertEqual(
            github_folder_url(
                "vercel-labs/agent-browser",
                ref="main",
                relative_path="skills/agent-browser",
            ),
            "https://github.com/vercel-labs/agent-browser/tree/main/skills/agent-browser",
        )


class MatchingSkillRootsTests(unittest.TestCase):
    def test_single_directory_name_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_skill_package(root / "skills", "commit-message", "Commit Message")
            matches = matching_skill_roots(root, "commit-message")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].name, "commit-message")

    def test_frontmatter_name_match_without_directory_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_skill_package(root / "renamed", "other-dir", "Frontmatter Skill")
            matches = matching_skill_roots(root, "Frontmatter Skill")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].name, "other-dir")

    def test_duplicate_directory_matches_are_all_reported(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_skill_package(root / "a", "dup-skill", "First")
            seed_skill_package(root / "b", "dup-skill", "Second")
            matches = matching_skill_roots(root, "dup-skill")
            self.assertEqual(len(matches), 2)
            self.assertEqual({m.name for m in matches}, {"dup-skill"})

    def test_duplicate_frontmatter_names_are_all_reported(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_skill_package(root / "one", "dir-one", "Same Name")
            seed_skill_package(root / "two", "dir-two", "Same Name")
            matches = matching_skill_roots(root, "Same Name")
            self.assertEqual(len(matches), 2)

    def test_no_match_returns_empty(self) -> None:
        with TemporaryDirectory() as temp_dir:
            self.assertEqual(matching_skill_roots(Path(temp_dir), "nonexistent"), ())


class GitHubSourceResolveTests(unittest.TestCase):
    """resolve() reuses the bounded archive primitive with explicit ambiguity."""

    @staticmethod
    def _fixture_source(repo_tree: Path, revision: str = "a" * 40):
        source = GitHubSource()
        original = GitHubSource.acquire_repository

        def fake_acquire(self, repo, work_dir, *, ref=None, alias_targets=None):
            root = work_dir / "repository"
            root.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copytree(repo_tree, root)
            return root, revision

        GitHubSource.acquire_repository = fake_acquire
        return source, lambda: setattr(GitHubSource, "acquire_repository", original)

    def test_resolve_returns_exact_revision_and_relative_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "repo"
            seed_skill_package(root / "skills", "my-skill", "My Skill")
            source, restore = self._fixture_source(root)
            self.addCleanup(restore)
            resolved = source.resolve("test/test-repo/my-skill", base / "work")
            self.assertEqual(resolved.repo, "test/test-repo")
            self.assertEqual(resolved.ref, "a" * 40)  # Exact commit, not branch name.
            self.assertEqual(resolved.relative_path, "skills/my-skill")
            self.assertTrue((resolved.package_path / "SKILL.md").is_file())

    def test_resolve_missing_skill_raises_not_found(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "repo"
            seed_skill_package(root / "skills", "my-skill", "My Skill")
            source, restore = self._fixture_source(root)
            self.addCleanup(restore)
            with self.assertRaisesRegex(ValueError, "not found"):
                source.resolve("test/test-repo/no-such-skill", base / "work")

    def test_resolve_duplicate_matches_is_explicitly_ambiguous(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "repo"
            seed_skill_package(root / "one", "dup-skill", "First")
            seed_skill_package(root / "two", "dup-skill", "Second")
            source, restore = self._fixture_source(root)
            self.addCleanup(restore)
            with self.assertRaisesRegex(ValueError, "matches multiple locations"):
                source.resolve("test/test-repo/dup-skill", base / "work")

    def test_fetch_returns_resolved_package_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "repo"
            seed_skill_package(root / "skills", "my-skill", "My Skill")
            source, restore = self._fixture_source(root)
            self.addCleanup(restore)
            package_path = source.fetch("test/test-repo/my-skill", base / "work")
            self.assertTrue((package_path / "SKILL.md").is_file())


    def test_resolved_skill_keeps_nested_repo_path(self) -> None:
        resolved = ResolvedGitHubSkill(
            repo="vercel-labs/agent-browser",
            ref="main",
            relative_path="skills/agent-browser",
            package_path=Path("/tmp/agent-browser/skills/agent-browser"),
            clone_dir=Path("/tmp/agent-browser"),
        )

        self.assertEqual(resolved.repo, "vercel-labs/agent-browser")
        self.assertEqual(resolved.relative_path, "skills/agent-browser")

class SkillsShParsingTests(unittest.TestCase):
    def test_parse_homepage_leaderboard_reads_embedded_initial_skills_payload(self) -> None:
        html = """
        <html>
          <body>
            <script>
              self.__next_f.push([1,"{\\"initialSkills\\":[{\\"source\\":\\"mode-io/skills\\",\\"skillId\\":\\"mode-switch\\",\\"name\\":\\"Mode Switch\\",\\"installs\\":128},{\\"source\\":\\"vercel-labs/skills\\",\\"skillId\\":\\"trace-scout\\",\\"name\\":\\"Trace Scout\\",\\"installs\\":84}]}"])
            </script>
          </body>
        </html>
        """
        skills = parse_homepage_leaderboard(html)
        self.assertEqual([(item.repo, item.skill_id, item.installs) for item in skills], [
            ("mode-io/skills", "mode-switch", 128),
            ("vercel-labs/skills", "trace-scout", 84),
        ])

    def test_parse_homepage_leaderboard_filters_unsupported_sources(self) -> None:
        html = """
        <html>
          <body>
            <script>
              self.__next_f.push([1,"{\\"initialSkills\\":[{\\"source\\":\\"unsupported-source.example\\",\\"skillId\\":\\"ui-ux-pro-max\\",\\"name\\":\\"ui-ux-pro-max\\",\\"installs\\":128},{\\"source\\":\\"mode-io/skills\\",\\"skillId\\":\\"mode-switch\\",\\"name\\":\\"Mode Switch\\",\\"installs\\":64}]}"])
            </script>
          </body>
        </html>
        """

        skills = parse_homepage_leaderboard(html)

        self.assertEqual([(item.repo, item.skill_id) for item in skills], [("mode-io/skills", "mode-switch")])

    def test_normalize_skill_rejects_unsupported_source(self) -> None:
        raw = raw_skill_from_payload({
            "source": "unsupported-source.example",
            "skillId": "ui-ux-pro-max",
            "name": "ui-ux-pro-max",
            "installs": 128,
        })

        self.assertIsNone(normalize_skill(raw, detail_base_url="https://skills.sh"))

    def test_github_repo_helpers_validate_and_derive_owner_avatar(self) -> None:
        self.assertTrue(is_valid_github_repo("mode-io/skills"))
        self.assertFalse(is_valid_github_repo("unsupported-source.example"))
        self.assertEqual(
            github_owner_avatar_url("mode-io/skills"),
            "https://github.com/mode-io.png?size=96",
        )
        self.assertIsNone(github_owner_avatar_url("unsupported-source.example"))

    def test_extract_detail_description_prefers_summary_then_skill_body_then_hint(self) -> None:
        summary_html = """
        <section>
          <h2>Summary</h2>
          <p>Investigate Azure telemetry and platform health.</p>
          <h2>SKILL.md</h2>
          <p>Ignored fallback body.</p>
        </section>
        """
        self.assertEqual(
            extract_detail_description(summary_html, skill_name="Azure Observability", description_hint="Hint"),
            "Investigate Azure telemetry and platform health.",
        )

        skill_body_html = """
        <section>
          <h2>SKILL.md</h2>
          <p>Azure Observability</p>
          <p>Use this skill to review Azure incidents and monitoring signals.</p>
        </section>
        """
        self.assertEqual(
            extract_detail_description(skill_body_html, skill_name="Azure Observability", description_hint="Hint"),
            "Use this skill to review Azure incidents and monitoring signals.",
        )

        self.assertEqual(
            extract_detail_description("<html><body><p>No useful sections.</p></body></html>", skill_name="Azure Observability", description_hint="Hint"),
            "Hint",
        )


if __name__ == "__main__":
    unittest.main()
