import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile

from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package


def seed_native(spec):
    root = spec.root / 'external-package'
    seed_skill_package(root / 'skills', 'one', 'One')
    (root / '.codex-plugin').mkdir()
    (root / '.codex-plugin/plugin.json').write_text(json.dumps({
        'name': 'example', 'repository': 'https://github.com/example/package'}))
    config = spec.xdg_config_home / 'opencode/opencode.jsonc'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'skills': {'paths': [str(root / 'skills')]}}))


def upstream_bytes(url, **kwargs):
    if '/commits/' in url:
        return json.dumps({'sha': 'a' * 40}).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as bundle:
        for path, text in {
            '.codex-plugin/plugin.json': '{"name":"example"}',
            '.claude-plugin/plugin.json': '{"name":"example"}',
            'skills/one/SKILL.md': '---\nname: one\n---\nAuthoritative',
            'uninterpreted/support.txt': 'retained',
        }.items():
            bundle.writestr('package/' + path, text)
    return stream.getvalue()


class ManagedPackagesApiTests(unittest.TestCase):
    @patch('skill_manager.sources.github.read_public_bytes')
    def test_owned_standalone_skill_is_not_migrated_by_package_adopt(self, read):
        def seed(spec):
            seed_skill_package(spec.claude_root, 'standalone', 'Standalone')
        with AppTestHarness(fixture_factory=seed) as harness:
            row = harness.get_json('/api/skills')['rows'][0]
            harness.post_json(f'/api/skills/{row["skillRef"]}/manage')
            row = harness.get_json('/api/skills')['rows'][0]
            harness.post_json(f'/api/skills/{row["skillRef"]}/manage-package', expected_status=409)
            self.assertEqual(harness.get_json('/api/skills/managed-packages')['packages'], [])
            read.assert_not_called()

    @patch('skill_manager.sources.github.read_public_bytes', side_effect=upstream_bytes)
    def test_adopt_keeps_native_install_untouched_and_exposes_retained_state(self, read):
        with AppTestHarness(fixture_factory=seed_native) as harness:
            native = harness.spec.root / 'external-package'
            before = {str(p.relative_to(native)): p.read_bytes() for p in native.rglob('*') if p.is_file()}
            row = harness.get_json('/api/skills')['rows'][0]
            harness.post_json(f'/api/skills/{row["skillRef"]}/manage')
            packages = harness.get_json('/api/skills/managed-packages')['packages']
            self.assertEqual(len(packages), 1)
            package = packages[0]
            self.assertEqual(package['state'], 'current')
            self.assertEqual(len(package['capabilities']['manifests']), 2)
            self.assertEqual(package['observations'][0]['ownership'], 'external-existing')
            self.assertTrue((Path(package['artifactRoot']) / 'uninterpreted/support.txt').is_file())
            self.assertEqual(before, {str(p.relative_to(native)): p.read_bytes() for p in native.rglob('*') if p.is_file()})
            self.assertEqual(list(harness.container.skills_store.scan().packages), [])
            after = harness.get_json('/api/skills')['rows'][0]
            self.assertEqual(after['displayStatus'], 'Managed')
            self.assertFalse(after['actions']['canManage'])
            self.assertFalse(any(cell['interactive'] for cell in after['cells']))
            harness.post_json(f'/api/skills/{row["skillRef"]}/enable', {'harness': 'opencode'}, expected_status=409)
            # Once observation disappears, refresh uses saved coordinates through 04B.
            shutil.rmtree(native)
            harness.container.skills_read_models.invalidate()
            refreshed = harness.post_json(f'/api/skills/managed-packages/{package["id"]}/refresh')
            self.assertEqual(refreshed['id'], package['id'])
            self.assertEqual(refreshed['artifactState'], 'current')
            self.assertIn('a' * 40, read.call_args_list[-2].args[0])

    @patch('skill_manager.sources.github.read_public_bytes', side_effect=TimeoutError('offline'))
    def test_unavailable_package_never_falls_back_to_standalone(self, read):
        with AppTestHarness(fixture_factory=seed_native) as harness:
            row = harness.get_json('/api/skills')['rows'][0]
            harness.post_json(f'/api/skills/{row["skillRef"]}/manage', expected_status=409)
            result = harness.get_json('/api/skills/managed-packages')
            self.assertEqual(result['packages'], [])
            self.assertEqual(result['skills'][row['skillRef']]['resolutionStatus'], 'unavailable')
            self.assertEqual(list(harness.container.skills_store.scan().packages), [])

    @patch('skill_manager.sources.github.read_public_bytes', side_effect=upstream_bytes)
    def test_explicit_package_adopt_for_marketplace_locator(self, read):
        def seed(spec):
            root = seed_skill_package(spec.claude_root, 'one', 'One')
            (root / 'SKILL.md').write_text('---\nname: One\nsource_kind: github\nsource_locator: github:example/package/one\n---')
        with AppTestHarness(fixture_factory=seed) as harness:
            row = harness.get_json('/api/skills')['rows'][0]
            package = harness.post_json(f'/api/skills/{row["skillRef"]}/manage-package')
            self.assertEqual(package['source']['revision'], 'a' * 40)
            self.assertEqual(package['source']['skill_path'], 'skills/one')
            self.assertEqual(package['state'], 'current')

    @patch('skill_manager.sources.github.read_public_bytes', side_effect=upstream_bytes)
    def test_observation_drift_only_records_candidate_not_new_ownership(self, read):
        with AppTestHarness(fixture_factory=seed_native) as harness:
            row = harness.get_json('/api/skills')['rows'][0]
            harness.post_json(f'/api/skills/{row["skillRef"]}/manage')
            package = harness.get_json('/api/skills/managed-packages')['packages'][0]
            manifest = harness.spec.root / 'external-package/.codex-plugin/plugin.json'
            manifest.write_text('{"name":"example", "repository":"https://github.com/other/package"}')
            changed = harness.post_json(f'/api/skills/managed-packages/{package["id"]}/refresh')
            self.assertEqual(changed['state'], 'changed')
            self.assertEqual(changed['source']['locator'], 'github:example/package')
            self.assertEqual(changed['candidateSource']['locator'], 'github:other/package')
            self.assertEqual(changed['artifactRoot'], package['artifactRoot'])
            self.assertEqual(len(harness.get_json('/api/skills/managed-packages')['packages']), 1)
