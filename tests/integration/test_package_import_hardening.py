from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch
import unittest
import zipfile
from dataclasses import replace
from tempfile import TemporaryDirectory

from skill_manager.application.skills.managed_packages import ManagedPackageStore
from skill_manager.application.skills.package_deployment import (
    NativeTarget,
    PackageDeploymentPlanner,
)
from tests.support.app_harness import AppTestHarness


GITHUB_COMMIT = "a" * 40
N8N_COMMIT = "b" * 40
NESTED_COMMIT = "c" * 40
GITHUB_REPOSITORY = "addyosmani/agent-skills"
N8N_REPOSITORY = "n8n-io/skills"
N8N_CACHE_PATH = "cache/packages/n8n-skills@git+https:/github.com/n8n-io/skills.git"


def _zip_archive(entries: list[tuple[str, str, str | bytes, int]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, kind, value, mode in entries:
            info = zipfile.ZipInfo("package/" + name)
            info.create_system = 3
            if kind == "dir":
                info.filename += "/"
                info.external_attr = (stat.S_IFDIR | mode) << 16
                bundle.writestr(info, b"")
            elif kind == "link":
                info.external_attr = (stat.S_IFLNK | mode) << 16
                bundle.writestr(info, value)
            else:
                info.external_attr = (stat.S_IFREG | mode) << 16
                bundle.writestr(info, value)
    return output.getvalue()


def _tree_signature(root: Path) -> tuple[tuple[str, str, bytes | str, int], ...]:
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            result.append((relative, "link", os.readlink(path), mode))
        elif path.is_dir():
            result.append((relative, "dir", b"", mode))
        elif path.is_file():
            result.append((relative, "file", path.read_bytes(), mode))
    return tuple(result)


def _skill_document(name: str, body: str = "Fixture skill") -> str:
    return f"---\nname: {name}\n---\n\n# {name}\n\n{body}\n"


def _write_git_metadata(root: Path, repository: str, revision: str) -> None:
    git = root / ".git"
    (git / "refs/heads").mkdir(parents=True)
    (git / "config").write_text(
        "[core]\nrepositoryformatversion = 0\n"
        f'[remote "origin"]\nurl = https://github.com/{repository}.git\n',
        encoding="utf-8",
    )
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs/heads/main").write_text(revision + "\n", encoding="utf-8")


def _write_common_package(root: Path, *, include_opencode_alias: bool = False) -> Path:
    skill = root / "skills/deprecation-and-migration"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        _skill_document("deprecation-and-migration", "Do not reveal fixture secrets."),
        encoding="utf-8",
    )
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-skills", "version": "0.6.9", "skills": "./skills", "repository": "https://github.com/" + GITHUB_REPOSITORY}), encoding="utf-8"
    )
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-skills", "version": "0.6.9", "skills": "./skills", "repository": "https://github.com/" + GITHUB_REPOSITORY}), encoding="utf-8"
    )
    # This is deliberately not a standard Agent Plugins manifest.
    (root / "plugin.json").write_text(json.dumps({"name": "agent-skills", "version": "0.6.9"}), encoding="utf-8")
    (root / "support").mkdir()
    (root / "support/unknown.txt").write_text("retained support material", encoding="utf-8")
    (root / "empty-directory").mkdir()
    executable = root / "bin/executable"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    if include_opencode_alias:
        opencode = root / ".opencode"
        opencode.mkdir()
        (opencode / "skills").symlink_to("../skills", target_is_directory=True)
    return skill


def _github_archive() -> bytes:
    return _zip_archive([
        (".claude-plugin/plugin.json", "file", '{"name":"agent-skills","version":"0.6.9","skills":"./skills"}', 0o644),
        (".codex-plugin/plugin.json", "file", '{"name":"agent-skills","version":"0.6.9","skills":"./skills"}', 0o644),
        ("plugin.json", "file", '{"name":"agent-skills","version":"0.6.9"}', 0o644),
        ("skills/deprecation-and-migration/SKILL.md", "file", _skill_document("deprecation-and-migration"), 0o644),
        ("support/unknown.txt", "file", "retained support material", 0o644),
        ("empty-directory", "dir", "", 0o755),
        ("bin/executable", "file", "#!/bin/sh\nexit 0\n", 0o755),
        (".opencode/skills", "link", "../skills", 0o777),
    ])


def _n8n_archive() -> bytes:
    package_json = json.dumps({
        "name": "n8n-skills",
        "version": "1.2.0",
        "main": "opencode/plugin.ts",
        "files": ["opencode", "skills", "hooks"],
    })
    return _zip_archive([
        ("package.json", "file", package_json, 0o644),
        (".claude-plugin/plugin.json", "file", '{"name":"n8n-skills","version":"1.2.0","skills":"./skills"}', 0o644),
        (".codex-plugin/plugin.json", "file", '{"name":"n8n-skills","version":"1.2.0","skills":"./skills","hooks":"./hooks/hooks.json"}', 0o644),
        ("hooks/hooks.json", "file", '{"hooks":{"SessionStart":[]}}', 0o644),
        ("opencode/plugin.ts", "file", "export default {};\n", 0o644),
        ("skills/n8n-node-configuration-official/SKILL.md", "file", _skill_document("n8n-node-configuration-official"), 0o644),
        ("support/unknown.txt", "file", "retained support material", 0o644),
        ("empty-directory", "dir", "", 0o755),
        ("bin/executable", "file", "#!/bin/sh\nexit 0\n", 0o755),
    ])


def _nested_boundary_archive() -> bytes:
    return _zip_archive([
        ("plugins/native/.claude-plugin/plugin.json", "file", '{"name":"agent-skills"}', 0o644),
        ("plugins/native/.codex-plugin/plugin.json", "file", '{"name":"agent-skills"}', 0o644),
        ("plugins/native/skills/deprecation-and-migration/SKILL.md", "file", _skill_document("deprecation-and-migration"), 0o644),
        ("plugins/native/.opencode/skills", "link", "../../../outside", 0o777),
        ("outside/nested-secret.txt", "file", "nested-secret", 0o644),
    ])


def _seed_github_fixture(spec) -> None:
    root = spec.root / "github-agent-skills"
    skill = _write_common_package(root, include_opencode_alias=True)
    _write_git_metadata(root, GITHUB_REPOSITORY, GITHUB_COMMIT)
    config = spec.xdg_config_home / "opencode/opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skills": {"paths": [str(skill.parent)]}}), encoding="utf-8")


def _seed_n8n_fixture(spec) -> None:
    project = spec.root / N8N_CACHE_PATH
    package = project / "node_modules/n8n-skills"
    skill = package / 'skills/n8n-node-configuration-official'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text(_skill_document('n8n-node-configuration-official'))
    (package / "package.json").write_text(json.dumps({
        "name": "n8n-skills",
        "version": "1.2.0",
        "main": "opencode/plugin.ts",
        "files": ["opencode", "skills", "hooks"],
    }), encoding="utf-8")
    (package / "opencode/plugin.ts").parent.mkdir()
    (package / "opencode/plugin.ts").write_text("export default {};\n", encoding="utf-8")
    (project / "package-lock.json").write_text(json.dumps({
        "name": "fixture-project",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "fixture-project", "version": "1.0.0"},
            "node_modules/n8n-skills": {
                "version": "1.2.0",
                "resolved": f"git+ssh://git@github.com/{N8N_REPOSITORY}.git#{N8N_COMMIT}",
            },
        },
    }), encoding="utf-8")
    config = spec.xdg_config_home / "opencode/opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skills": {"paths": [str(skill.parent)]}}), encoding="utf-8")


def _seed_unsafe_git_ssh_fixture(spec) -> None:
    root = spec.root / "unsafe-git-source"
    skill = _write_common_package(root)
    unsafe = "git+ssh://git:unsafe-secret@github.com/mode-io/agent-skills.git"
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        (root / relative).write_text(json.dumps({"name": "agent-skills", "repository": unsafe}), encoding="utf-8")
    config = spec.xdg_config_home / "opencode/opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skills": {"paths": [str(skill.parent)]}}), encoding="utf-8")


def _seed_nested_boundary_fixture(spec) -> None:
    root = spec.root / "nested-source"
    package = root / "plugins/native"
    skill = _write_common_package(package)
    repository = {"type": "git", "url": "https://github.com/mode-io/nested.git", "directory": "plugins/native"}
    for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        (package / relative).write_text(json.dumps({"name": "agent-skills", "repository": repository}), encoding="utf-8")
    config = spec.xdg_config_home / "opencode/opencode.jsonc"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skills": {"paths": [str(skill.parent)]}}), encoding="utf-8")


class PackageImportHardeningIntegrationTests(unittest.TestCase):
    @staticmethod
    def _github_response(read, archive: bytes, revision: str, url: str, **_kwargs):
        if "/commits/" in url:
            return json.dumps({"sha": revision}).encode()
        if "codeload.github.com" in url:
            return archive
        raise AssertionError(f"unexpected public source URL: {url}")

    @staticmethod
    def _skill_ref(app: AppTestHarness) -> str:
        rows = app.get_json("/api/skills")["rows"]
        assert len(rows) == 1
        return rows[0]["skillRef"]

    @staticmethod
    def _target(root: Path, harness: str) -> NativeTarget:
        target_root = root / "native" / harness
        target_root.mkdir(parents=True, exist_ok=True)
        return NativeTarget(harness, target_root, inventory_complete=True, mechanism_available=True)

    def test_github_resolve_and_manage_retains_complete_snapshot_and_native_plans(self) -> None:
        archive = _github_archive()
        with patch("skill_manager.sources.github.read_public_bytes") as read:
            read.side_effect = lambda url, **kwargs: self._github_response(read, archive, GITHUB_COMMIT, url, **kwargs)
            with AppTestHarness(fixture_factory=_seed_github_fixture) as app:
                original = app.spec.root / "github-agent-skills"
                before = _tree_signature(original)
                skill_ref = self._skill_ref(app)
                self.assertEqual((original / ".git/HEAD").read_text(), "ref: refs/heads/main\n")
                self.assertEqual((original / ".git/refs/heads/main").read_text().strip(), GITHUB_COMMIT)
                self.assertEqual(
                    json.loads((app.spec.xdg_config_home / "opencode/opencode.jsonc").read_text())[
                        "skills"
                    ]["paths"],
                    [str(original / "skills")],
                )

                resolved = app.post_json(f"/api/skills/{skill_ref}/resolve-package")
                resolution = resolved["resolution"]
                self.assertEqual(resolution["status"], "resolved")
                self.assertEqual(resolution["source"]["kind"], "github")
                self.assertEqual(resolution["source"]["locator"], "github:" + GITHUB_REPOSITORY)
                self.assertEqual(resolution["source"]["revision"], GITHUB_COMMIT)
                self.assertTrue(any(item["kind"] == "git_origin_commit" for item in resolution["evidence"]))
                self.assertNotIn("artifact_root", json.dumps(resolved))

                managed = app.post_json(f"/api/skills/{skill_ref}/manage-package")
                package_id = managed["id"]
                self.assertEqual(managed["state"], "current")
                self.assertEqual(managed["source"]["revision"], GITHUB_COMMIT)
                self.assertTrue(any(item["kind"] == "git_origin_commit" for item in managed["evidence"]))
                self.assertIsNotNone(managed["capabilities"])
                manifests = {item["path"]: item["evidence"] for item in managed["capabilities"]["manifests"]}
                self.assertEqual(set(manifests), {
                    "plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                })
                self.assertEqual(manifests["plugin.json"], "unresolved")
                self.assertEqual(manifests[".claude-plugin/plugin.json"], "declared_harness_manifest")
                self.assertEqual(manifests[".codex-plugin/plugin.json"], "declared_harness_manifest")

                artifact = Path(managed["artifactRoot"])
                self.assertTrue(artifact.is_dir())
                self.assertEqual({path.relative_to(artifact).as_posix() for path in artifact.rglob("*")}, {
                    ".claude-plugin", ".claude-plugin/plugin.json", ".codex-plugin", ".codex-plugin/plugin.json",
                    ".opencode", ".opencode/skills", ".opencode/skills/deprecation-and-migration",
                    ".opencode/skills/deprecation-and-migration/SKILL.md", "plugin.json", "skills",
                    "skills/deprecation-and-migration", "skills/deprecation-and-migration/SKILL.md",
                    "support", "support/unknown.txt", "empty-directory", "bin", "bin/executable",
                })
                self.assertFalse(any(path.is_symlink() for path in artifact.rglob("*")))
                self.assertEqual(stat.S_IMODE((artifact / "bin/executable").stat().st_mode), 0o755)
                self.assertEqual((artifact / ".opencode/skills/deprecation-and-migration/SKILL.md").read_text(),
                                 (artifact / "skills/deprecation-and-migration/SKILL.md").read_text())
                self.assertEqual(before, _tree_signature(original))

                reloaded = ManagedPackageStore(app.container.skills_queries.managed_packages.root)
                self.assertEqual(reloaded.get(package_id)["fingerprint"], managed["fingerprint"])
                context = app.get_json(f"/api/skills/{skill_ref}/package-context")
                self.assertTrue(context["packageBacked"])
                self.assertEqual(context["managedPackage"]["id"], package_id)
                self.assertEqual(context["resolution"]["status"], "resolved")

                planner = PackageDeploymentPlanner(reloaded)
                for harness, strategy in (("claude", "native-local"), ("codex", "native-install")):
                    plan = planner.plan(package_id, self._target(app.spec.root, harness))
                    self.assertEqual(plan.support, "supported", plan.blockers)
                    self.assertEqual(plan.strategy, strategy)
                    self.assertTrue(plan.actions)
                    self.assertTrue(all(action["unit"] == "whole-package" for action in plan.actions))
                opencode = planner.plan(package_id, self._target(app.spec.root, "opencode"))
                self.assertEqual(opencode.support, "manual")
                self.assertEqual(opencode.strategy, "manual/unsupported")
                self.assertEqual(opencode.actions, [])
                self.assertIn("external-observation-needs-native-reconciliation", opencode.blockers)
                self.assertIn("opencode-native-server-entry-unproven", opencode.blockers)

                deployments = app.get_json(f"/api/skills/managed-packages/{package_id}/deployments")
                self.assertEqual({item["harness"] for item in deployments["harnesses"]}, {"claude", "codex", "cursor", "opencode"})
                self.assertEqual(next(item for item in deployments["harnesses"] if item["harness"] == "opencode")["actions"], [])

    def test_n8n_git_locked_package_resolves_canonically_and_requires_exact_opencode_proof(self) -> None:
        archive = _n8n_archive()
        with patch("skill_manager.sources.github.read_public_bytes") as read:
            read.side_effect = lambda url, **kwargs: self._github_response(read, archive, N8N_COMMIT, url, **kwargs)
            with AppTestHarness(fixture_factory=_seed_n8n_fixture) as app:
                original = app.spec.root / N8N_CACHE_PATH
                before = _tree_signature(original)
                skill_ref = self._skill_ref(app)
                resolved = app.post_json(f"/api/skills/{skill_ref}/resolve-package")
                resolution = resolved["resolution"]
                self.assertEqual(resolution["status"], "resolved")
                self.assertEqual(resolution["source"]["kind"], "github")
                self.assertEqual(resolution["source"]["locator"], "github:" + N8N_REPOSITORY)
                self.assertEqual(resolution["source"]["revision"], N8N_COMMIT)
                self.assertTrue(any(item["kind"] == "npm_lock_git" for item in resolution["evidence"]))
                self.assertNotIn("git+ssh", json.dumps(resolved))
                lock_record = json.loads((original / "package-lock.json").read_text())["packages"][
                    "node_modules/n8n-skills"
                ]
                self.assertEqual(
                    lock_record["resolved"],
                    f"git+ssh://git@github.com/{N8N_REPOSITORY}.git#{N8N_COMMIT}",
                )

                managed = app.post_json(f"/api/skills/{skill_ref}/manage-package")
                package_id = managed["id"]
                self.assertEqual(managed["source"]["locator"], "github:" + N8N_REPOSITORY)
                self.assertEqual(managed["source"]["revision"], N8N_COMMIT)
                self.assertTrue(any(item["kind"] == "npm_lock_git" for item in managed["evidence"]))
                artifact = Path(managed["artifactRoot"])
                artifact_manifest = json.loads((artifact / "package.json").read_text())
                self.assertEqual(artifact_manifest["main"], "opencode/plugin.ts")
                self.assertEqual(
                    artifact_manifest["files"],
                    ["opencode", "skills", "hooks"],
                )
                self.assertTrue((artifact / "opencode/plugin.ts").is_file())
                self.assertTrue((artifact / "skills/n8n-node-configuration-official/SKILL.md").is_file())
                self.assertTrue((artifact / '.claude-plugin/plugin.json').is_file())
                self.assertTrue((artifact / '.codex-plugin/plugin.json').is_file())
                self.assertFalse((original / 'node_modules/n8n-skills/.codex-plugin').exists())
                self.assertTrue((artifact / "support/unknown.txt").is_file())
                self.assertTrue((artifact / "empty-directory").is_dir())
                self.assertEqual(stat.S_IMODE((artifact / "bin/executable").stat().st_mode), 0o755)
                self.assertFalse(any(path.is_symlink() for path in artifact.rglob("*")))
                self.assertEqual(before, _tree_signature(original))

                reloaded = ManagedPackageStore(app.container.skills_queries.managed_packages.root)
                planner = PackageDeploymentPlanner(reloaded)
                for harness, strategy in (("claude", "native-local"), ("codex", "native-install")):
                    plan = planner.plan(package_id, self._target(app.spec.root, harness))
                    self.assertEqual(plan.support, "supported", plan.blockers)
                    self.assertEqual(plan.strategy, strategy)
                    self.assertTrue(plan.actions)

                observations = managed["observations"]
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]["harness"], "opencode")
                opencode = planner.plan(package_id, self._target(app.spec.root, "opencode"))
                # The exact main entry and lock proof are still insufficient to
                # take over an existing external OpenCode observation.
                self.assertEqual(opencode.support, "manual")
                self.assertEqual(opencode.strategy, "native-install")
                self.assertEqual(opencode.actions, [])
                self.assertIn("external-observation-needs-native-reconciliation", opencode.blockers)
                self.assertEqual(opencode.surface["nativeId"], Path(opencode.surface["path"]).as_uri())
                self.assertEqual(opencode.surface["configKey"], "plugin")
                self.assertEqual(opencode.surface["loader"], "opencode-plugin-file-url-global")

                deployments = app.get_json(f"/api/skills/managed-packages/{package_id}/deployments")
                opencode_view = next(item for item in deployments["harnesses"] if item["harness"] == "opencode")
                self.assertEqual(opencode_view["actions"], [])

    @patch("skill_manager.sources.github.read_public_bytes")
    def test_unsafe_git_ssh_failure_is_redacted_and_creates_no_package(self, read) -> None:
        with AppTestHarness(fixture_factory=_seed_unsafe_git_ssh_fixture) as app:
            skill_ref = self._skill_ref(app)
            resolved = app.post_json(f"/api/skills/{skill_ref}/resolve-package")
            self.assertEqual(resolved["resolution"]["status"], "unresolved")
            self.assertIn("unsupported repository URL", resolved["resolution"]["reason"])
            self.assertNotIn("unsafe-secret", json.dumps(resolved))
            read.assert_not_called()

            app.post_json(f"/api/skills/{skill_ref}/manage-package", expected_status=409)
            packages = app.get_json("/api/skills/managed-packages")
            self.assertEqual(packages["packages"], [])
            self.assertNotIn("unsafe-secret", json.dumps(packages))
            self.assertFalse((app.container.skills_queries.managed_packages.root / "artifacts").exists())

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_later_pinned_opencode_observation_adds_intent_to_same_snapshot(self, read):
        archive = _n8n_archive()
        read.side_effect = lambda url, **kw: self._github_response(read, archive, N8N_COMMIT, url, **kw)
        with AppTestHarness(fixture_factory=_seed_n8n_fixture) as app:
            ref = self._skill_ref(app)
            store = app.container.skills_queries.managed_packages
            with TemporaryDirectory() as work:
                resolution = app.container.skills_queries.resolve_package_source(ref, work_dir=Path(work))
                earlier = store.adopt(replace(resolution, evidence=()), name='earlier source review', observations=[])
            result = app.post_json(f'/api/skills/{ref}/manage-package')
            self.assertEqual(result['id'], earlier['id'])
            self.assertEqual(result['fingerprint'], earlier['fingerprint'])
            self.assertIn('opencode_package_observation', {item['kind'] for item in result['evidence']})
            self.assertIn('npm_lock_git', {item['kind'] for item in result['evidence']})
            self.assertEqual(PackageDeploymentPlanner(ManagedPackageStore(store.root)).plan(
                result['id'], self._target(app.spec.root, 'opencode')).strategy, 'native-install')

    @patch("skill_manager.sources.github.read_public_bytes")
    def test_nested_package_alias_escape_fails_without_artifact_or_path_leak(self, read) -> None:
        archive = _nested_boundary_archive()
        read.side_effect = lambda url, **kwargs: self._github_response(read, archive, NESTED_COMMIT, url, **kwargs)
        with AppTestHarness(fixture_factory=_seed_nested_boundary_fixture) as app:
            original = app.spec.root / "nested-source"
            before = _tree_signature(original)
            skill_ref = self._skill_ref(app)
            resolved = app.post_json(f"/api/skills/{skill_ref}/resolve-package")
            self.assertEqual(resolved["resolution"]["status"], "unavailable")
            reason = resolved["resolution"]["reason"]
            self.assertIn("alias crosses selected package boundary", reason)
            self.assertNotIn("nested-secret", json.dumps(resolved))
            self.assertNotIn("outside", reason)

            app.post_json(f"/api/skills/{skill_ref}/manage-package", expected_status=409)
            packages = app.get_json("/api/skills/managed-packages")
            self.assertEqual(packages["packages"], [])
            self.assertIn("alias crosses selected package boundary", json.dumps(packages))
            self.assertNotIn("nested-secret", json.dumps(packages))
            self.assertFalse((app.container.skills_queries.managed_packages.root / "artifacts").exists())
            self.assertEqual(before, _tree_signature(original))


if __name__ == "__main__":
    unittest.main()
