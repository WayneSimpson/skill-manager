import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package


class PackageResolutionServiceTests(unittest.TestCase):
    def test_real_inventory_query_resolves_without_changing_native_install(self):
        def seed(spec):
            root = spec.root / 'native-package'
            seed_skill_package(root / 'skills', 'example', 'Example')
            path = root / '.codex-plugin/plugin.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'name': 'example', 'repository': 'https://github.com/example/package'}))
            config = spec.xdg_config_home / 'opencode/opencode.jsonc'
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({'skills': {'paths': [str(root / 'skills')]}}))

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as bundle:
            bundle.writestr('root/.codex-plugin/plugin.json', '{"name":"example"}')
            bundle.writestr('root/.claude-plugin/plugin.json', '{"name":"example"}')
            bundle.writestr('root/skills/example/SKILL.md', '---\nname: example\n---\nContent')

        with AppTestHarness(fixture_factory=seed) as harness:
            root = harness.spec.root / 'native-package'
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with patch('skill_manager.sources.github.read_public_bytes') as read:
                row = harness.get_json('/api/skills')['rows'][0]
                harness.get_json(f'/api/skills/{row["skillRef"]}')
                read.assert_not_called()
                read.side_effect = lambda url, **kw: (stream.getvalue() if 'codeload' in url
                                                      else json.dumps({'sha': 'a' * 40}).encode())
                with TemporaryDirectory() as work:
                    result = harness.container.skills_queries.resolve_package_source(row['skillRef'], work_dir=Path(work))
                    self.assertEqual(result.status, 'resolved', result.reason)
                    self.assertEqual(len(result.capabilities['manifests']), 2)
                    self.assertTrue(result.artifact_root.is_dir())
                self.assertFalse(result.artifact_root.exists())
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(harness.get_json('/api/skills')['rows'][0]['displayStatus'], 'Unmanaged')
