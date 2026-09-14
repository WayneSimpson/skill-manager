from pathlib import Path
from tempfile import TemporaryDirectory
import io
import json
import base64
import hashlib
import tarfile
import unittest
from unittest.mock import patch
import zipfile

from skill_manager.application.skills.identity import SourceDescriptor
from skill_manager.application.skills.inventory import InventoryEntry
from skill_manager.application.skills.package_resolution import PackageSourceResolver
from skill_manager.application.skills.marketplace.models import SkillsShSkill
from skill_manager.sources.artifacts import extract_source, public_url
from skill_manager.sources import artifacts


COMMIT = 'a' * 40


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as bundle:
        for name, text in files.items():
            bundle.writestr('repo-root/' + name, text)
    return output.getvalue()


def upstream():
    return archive({
        'skills/one/SKILL.md': '---\nname: one\n---\nExample',
        '.claude-plugin/plugin.json': '{"name":"example"}',
        '.codex-plugin/plugin.json': '{"name":"example"}',
        'hooks/hooks.json': '{"hooks":{"SessionStart":[]}}',
        'support.txt': 'whole package',
    })


class PackageResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / 'observation' / 'skills' / 'one'
        self.skill.mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('---\nname: one\n---\nExample')
        self.entry = InventoryEntry('test', 'one', '', 'unmanaged', SourceDescriptor('runtime', 'opencode'),
                                    source_path=str(self.skill))
        self.work = self.root / 'work'
        self.work.mkdir()
        self.resolver = PackageSourceResolver()

    def declare(self, repository='https://github.com/example/package'):
        path = self.skill.parents[1] / '.codex-plugin' / 'plugin.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'name': 'example', 'repository': repository}))

    def network(self, url, **kwargs):
        if '/commits/' in url:
            return json.dumps({'sha': COMMIT}).encode()
        if 'codeload.github.com' in url:
            return upstream()
        raise AssertionError('Unexpected network request: ' + url)

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_partial_native_observation_acquires_full_upstream(self, read):
        self.declare()
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.revision, COMMIT)
        self.assertEqual(result.source.locator, 'github:example/package')
        self.assertTrue((result.artifact_root / 'support.txt').is_file())
        self.assertEqual({x['path'] for x in result.capabilities['manifests']},
                         {'.claude-plugin/plugin.json', '.codex-plugin/plugin.json'})
        self.assertTrue((self.skill / 'SKILL.md').exists())

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_no_name_based_source_selection(self, read):
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unresolved')
        self.assertIsNone(result.artifact_root)
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes', side_effect=TimeoutError('secret-token'))
    def test_unavailable_is_sanitized_and_has_no_artifact(self, read):
        self.declare()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unavailable')
        self.assertIsNone(result.artifact_root)
        self.assertNotIn('secret-token', str(result))
        self.assertEqual(list(self.work.iterdir()), [])

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_marketplace_locator_reuses_repo_and_exact_skill_path(self, read):
        item = SkillsShSkill('example/package', 'one', 'Display name is irrelevant', 1)
        self.entry.source = SourceDescriptor('github', item.source_locator)
        self.entry.source_path = 'skills/one'
        self.entry.source_ref = 'release/v1'
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.ref, 'release/v1')
        self.assertIn('/commits/release%2Fv1', read.call_args_list[0].args[0])

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_manual_skill_frontmatter_source(self, read):
        (self.skill / 'SKILL.md').write_text('---\nname: one\nsource_kind: github\nsource_locator: github:example/package/one\n---')
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_conflicting_manifests_stay_ambiguous(self, read):
        self.declare()
        other = self.skill.parents[1] / '.claude-plugin/plugin.json'
        other.parent.mkdir()
        other.write_text('{"name":"example", "repository":"https://github.com/other/package"}')
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'ambiguous')
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_content_only_runtime_remains_unresolved(self, read):
        self.entry.source_path = None
        self.entry.runtime_only = True
        self.entry.runtime_content = '---\nname: one\n---'
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unresolved')
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_git_commit_and_monorepo_boundary(self, read):
        self.declare()
        git = self.root / 'observation/.git'
        git.mkdir()
        (git / 'config').write_text('[core]\nrepositoryformatversion = 0\n[remote "origin"]\nurl = https://github.com/example/package.git\n')
        (git / 'HEAD').write_text(COMMIT)
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.revision, COMMIT)
        self.assertIn('/commits/' + COMMIT, read.call_args_list[0].args[0])
        self.assertFalse((result.artifact_root / '.git').exists())

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_nested_package_does_not_retain_siblings(self, read):
        self.declare({'type': 'git', 'url': 'https://github.com/example/package.git', 'directory': 'plugins/native'})
        data = archive({'plugins/native/.codex-plugin/plugin.json': '{"name":"example"}',
                        'plugins/native/skills/one/SKILL.md': '---\nname: one\n---',
                        'unrelated/private.txt': 'do not retain'})
        read.side_effect = lambda url, **kw: data if 'codeload' in url else json.dumps({'sha': COMMIT}).encode()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.package_path, 'plugins/native')
        self.assertFalse(any(self.work.rglob('private.txt')))

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_ambiguous_skill_id_inside_known_repository(self, read):
        self.entry.source = SourceDescriptor('github', 'github:example/package/one')
        self.entry.source_path = None
        read.side_effect = lambda url, **kw: archive({
            'first/skills/one/SKILL.md': '---\nname: one\n---',
            'second/skills/one/SKILL.md': '---\nname: one\n---',
        }) if 'codeload' in url else json.dumps({'sha': COMMIT}).encode()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'ambiguous')
        self.assertIsNone(result.artifact_root)
        self.assertEqual(list(self.work.iterdir()), [])

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_marketplace_exact_declared_identifier_with_different_directory(self, read):
        self.entry.source = SourceDescriptor('github', 'github:example/package/public-name')
        self.entry.source_path = None
        read.side_effect = lambda url, **kw: archive({
            '.claude-plugin/plugin.json': '{"name":"example"}',
            'skills/internal/SKILL.md': '---\nname: public-name\n---',
        }) if 'codeload' in url else json.dumps({'sha': COMMIT}).encode()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.skill_path, 'skills/internal')

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_known_commit_never_falls_back_to_latest(self, read):
        self.declare('https://github.com/example/package#' + 'b' * 40)
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(read.call_count, 1)

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_metadata_symlink_escape_rejected(self, read):
        self.declare()
        manifest = self.skill.parents[1] / '.codex-plugin/plugin.json'
        outside = self.root / 'outside.json'
        outside.write_text(manifest.read_text())
        manifest.unlink()
        manifest.symlink_to(outside)
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertNotEqual(result.status, 'resolved')
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_subprocesses_are_not_used_for_acquisition(self, read):
        self.declare()
        read.side_effect = self.network
        with patch('subprocess.run', side_effect=AssertionError('Execution forbidden')):
            result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_credentials_in_repository_not_exposed(self, read):
        self.declare('https://user:private-token@github.com/example/package')
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertNotIn('private-token', repr(result))
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_malformed_archive_returns_unavailable(self, read):
        self.declare()
        read.side_effect = lambda url, **kw: b'not zip' if 'codeload' in url else json.dumps({'sha': COMMIT}).encode()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(list(self.work.iterdir()), [])

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_frontmatter_conflict_with_repository_is_not_hidden(self, read):
        self.declare()
        (self.skill / 'SKILL.md').write_text('---\nname: one\nsource_kind: github\nsource_locator: github:other/package/one\n---')
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'ambiguous')
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_invalid_nearest_plugin_does_not_assert_provenance(self, read):
        self.declare()
        path = self.skill.parents[1] / '.codex-plugin/plugin.json'
        path.write_text('{"name":"example", "skills":"./unrelated", "repository":"https://github.com/example/package"}')
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unresolved')
        read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_skill_and_package_path_conflict(self, read):
        self.declare({'url': 'https://github.com/example/package', 'directory': 'unrelated'})
        git = self.root / 'observation/.git'
        git.mkdir()
        (git / 'config').write_text('[remote "origin"]\nurl=https://github.com/example/package\n')
        (git / 'HEAD').write_text(COMMIT)
        read.side_effect = self.network
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'ambiguous')
        read.assert_not_called()

    def test_source_url_and_archive_safety(self):
        for url in ['https://localhost/file', 'http://github.com/a/b', 'https://registry.npmjs.org/x?token=secret',
                    'https://api.github.com@localhost/file']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)
        for path in ['../outside', '/absolute', 'a/../../outside', 'C:\\outside']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                extract_source(archive({path: 'bad'}), self.work / 'artifact', kind='zip')
        self.assertFalse((self.root / 'outside').exists())

    def test_link_archive_rejected(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            member = tarfile.TarInfo('package/skills/one')
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc'
            tar.addfile(member)
        with self.assertRaises(ValueError):
            extract_source(output.getvalue(), self.work / 'artifact', kind='tar')

    def test_archive_limits_and_failed_write_leave_no_partial_artifact(self):
        data = archive({'one': 'first', 'one/child': 'conflicts with file'})
        destination = self.work / 'artifact'
        with self.assertRaises(ValueError):
            extract_source(data, destination, kind='zip')
        self.assertFalse(destination.exists())
        with patch.object(artifacts, 'MAX_EXPANDED', 2), self.assertRaises(ValueError):
            extract_source(archive({'large': 'too large'}), destination, kind='zip')
        self.assertFalse(destination.exists())

    def test_download_bound_and_redirects(self):
        response = io.BytesIO(b'too much')
        with patch.object(artifacts, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                artifacts.read_public_bytes('https://api.github.com/repos/example/package', limit=2)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 10)
        with self.assertRaises(ValueError):
            artifacts._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://localhost/')

    def test_excluded_machine_state_not_in_artifact(self):
        root = self.work / 'artifact'
        extract_source(archive({'support/script.sh': 'exit 99', '.git/config': 'credential',
                                'node_modules/other/index.js': 'outside', '.env': 'secret',
                                '.npmrc': 'token', '.cache/private': 'secret'}), root, kind='zip')
        self.assertEqual([p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()],
                         ['support/script.sh'])

    @patch('skill_manager.sources.github.read_public_bytes')
    def test_genuine_git_checkout_retains_branch_and_commit(self, read):
        import os
        import subprocess
        self.declare()
        checkout = self.root / 'observation'
        env = {'PATH': os.environ['PATH'], 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
               'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        def git(*args):
            return subprocess.run(['git', *args], cwd=checkout, env=env, check=True,
                                  capture_output=True, text=True, timeout=10).stdout.strip()
        git('init', '-b', 'fixture-branch')
        git('add', '.')
        git('commit', '-m', 'fixture')
        git('remote', 'add', 'origin', 'https://github.com/example/package')
        commit = git('rev-parse', 'HEAD')
        read.side_effect = lambda url, **kw: upstream() if 'codeload' in url else json.dumps({'sha': commit}).encode()
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.revision, commit)
        self.assertEqual(result.source.ref, 'fixture-branch')

    @patch('skill_manager.application.skills.package_resolution.read_public_json')
    @patch('skill_manager.application.skills.package_resolution.read_public_bytes')
    def test_native_npm_lock_and_explicit_repository_relationship(self, read, metadata):
        package = self.root / 'native/node_modules/@sample/extension'
        skill = package / 'skills/one'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: one\n---')
        manifest = {'name': '@sample/extension', 'version': '1.2.3'}
        (package / 'package.json').write_text(json.dumps(manifest))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for name, text in {'package.json': json.dumps(manifest), '.codex-plugin/plugin.json': '{"name":"example"}',
                               'skills/one/SKILL.md': '---\nname: one\n---'}.items():
                member = tarfile.TarInfo('package/' + name)
                data = text.encode()
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        data = output.getvalue()
        integrity = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
        url = 'https://registry.npmjs.org/@sample/extension/-/extension-1.2.3.tgz'
        (self.root / 'native/package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': {
            'node_modules/@sample/extension': {'version': '1.2.3', 'resolved': url, 'integrity': integrity}}}))
        metadata.return_value = {**manifest, 'dist': {'integrity': integrity, 'tarball': url},
                                 'repository': {'url': 'https://github.com/example/package.git'}, 'gitHead': COMMIT}
        read.return_value = data
        self.entry.source_path = str(skill)
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'resolved', result.reason)
        self.assertEqual(result.source.locator, 'npm:@sample/extension@1.2.3')
        self.assertEqual(result.distributions[0].source.revision, COMMIT)
        self.assertIsNone(result.distributions[0].harness)
        self.assertEqual(result.capabilities['manifests'][0]['path'], '.codex-plugin/plugin.json')
        # Same name is insufficient to invent a source relationship.
        del metadata.return_value['repository']
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.distributions, ())
        read.return_value = data + b'corrupt'
        result = self.resolver.resolve(self.entry, work_dir=self.work)
        self.assertEqual(result.status, 'unavailable')
        read.return_value = data
        for dist in (None, [], {'tarball': url, 'integrity': 'sha512-secret-token'},
                     {'tarball': 'https://registry.npmjs.org/other.tgz', 'integrity': integrity}):
            with self.subTest(dist=dist):
                metadata.return_value['dist'] = dist
                result = self.resolver.resolve(self.entry, work_dir=self.work)
                self.assertEqual(result.status, 'unavailable')
                self.assertNotIn('secret-token', repr(result))
        lock = self.root / 'native/package-lock.json'
        for packages in (None, [], {'node_modules/@sample/extension': {'version': '1.2.3', 'resolved': []}}):
            lock.write_text(json.dumps({'lockfileVersion': 3, 'packages': packages}))
            result = self.resolver.resolve(self.entry, work_dir=self.work)
            self.assertEqual(result.status, 'unresolved')
