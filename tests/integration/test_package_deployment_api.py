import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.support.app_harness import AppTestHarness
from tests.support.package_ux_fixture import PackageUXFixture
from skill_manager.application.skills.package_resolution import DistributionRelationship, PackageSource
from skill_manager.application.skills.native_package_runtime import NativePackageAdapterFactory


class PackageDeploymentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = PackageUXFixture(Path(self.temp.name))
        self.app = AppTestHarness(native_package_adapter_factory=self.fixture)
        self.addCleanup(self.app.__exit__, None, None, None)
        self.store = self.app.container.skills_queries.managed_packages
        self.package = self.fixture.adopt(self.store)

    def endpoint(self, package=None):
        return '/api/skills/managed-packages/' + (package or self.package)['id'] + '/deployments'

    def action(self, harness, action, package=None, status=200, **body):
        return self.app.post_json(self.endpoint(package) + '/' + harness, {'action': action, **body}, expected_status=status)

    @staticmethod
    def row(view, harness):
        return next(row for row in view['harnesses'] if row['harness'] == harness)

    def test_real_service_two_native_mechanisms_and_current_toggle_state(self):
        initial = self.app.get_json(self.endpoint())
        self.assertEqual(self.row(initial, 'cursor')['state'], 'manual')
        self.assertEqual(self.row(initial, 'opencode')['state'], 'manual')
        central = self.store.manifest.read_bytes()
        for harness, strategy in [('claude', 'native-local'), ('codex', 'native-install')]:
            deployed = self.action(harness, 'deploy')
            self.assertEqual(self.row(deployed, harness)['strategy'], strategy)
            self.assertEqual(self.row(deployed, harness)['state'], 'enabled')
            self.action(harness, 'deploy')
            self.assertEqual(self.row(self.action(harness, 'disable'), harness)['state'], 'disabled')
            self.assertEqual(self.row(self.action(harness, 'enable'), harness)['state'], 'enabled')
        # A native change outside the ledger must be reflected by a read without rewriting it.
        self.fixture.claude.disabled.add('fixture@skills-dir')
        ledger = self.app.container.skills_mutations.package_deployments.state_path
        before = ledger.read_bytes()
        self.assertEqual(self.row(self.app.get_json(self.endpoint()), 'claude')['state'], 'disabled')
        self.assertEqual(before, ledger.read_bytes())
        self.action('claude', 'remove')
        remaining = self.app.get_json(self.endpoint())
        self.assertEqual(self.row(remaining, 'codex')['state'], 'enabled')
        self.action('codex', 'remove')
        self.assertEqual(central, self.store.manifest.read_bytes())
        self.assertEqual(len(self.app.get_json('/api/skills/managed-packages')['packages']), 1)

    def test_explicit_proven_replacement_updates_returned_package_and_preserves_disabled(self):
        self.action('codex', 'deploy')
        self.action('codex', 'disable')
        newer = self.fixture.adopt(self.store, 'b', '2.0.0')
        unrelated = self.fixture.adopt(self.store, 'c', '3.0.0', locator='github:unrelated/same-name')
        row = self.row(self.app.get_json(self.endpoint()), 'codex')
        self.assertEqual([option['packageId'] for option in row['replacementOptions']], [newer['id']])
        before = len(self.fixture.codex.mutations)
        self.action('codex', 'update', status=409, replacementPackageId=unrelated['id'])
        self.assertEqual(before, len(self.fixture.codex.mutations))
        result = self.action('codex', 'update', replacementPackageId=newer['id'])
        self.assertEqual(result['packageId'], newer['id'])
        self.assertEqual(self.row(result, 'codex')['state'], 'disabled')
        self.assertEqual(self.row(self.app.get_json(self.endpoint()), 'codex')['actions'], [])

    def test_external_opencode_and_cache_conflict_cannot_mutate(self):
        root = self.fixture.adapters['opencode'].root
        root.mkdir(parents=True)
        config = root / 'opencode.json'
        config.write_text(json.dumps({'plugin': ['github:example/fixture#' + 'a' * 40]}))
        before = config.read_bytes()
        self.assertEqual(self.row(self.app.get_json(self.endpoint()), 'opencode')['state'], 'external-existing')
        self.action('opencode', 'deploy', status=409)
        self.assertEqual(before, config.read_bytes())
        self.action('codex', 'deploy')
        native = next(iter(self.fixture.codex.installed.values()))
        cache = self.fixture.codex.root / 'plugins/cache' / native['marketplaceName'] / native['name']
        (cache / 'external.txt').write_text('external')
        count = len(self.fixture.codex.mutations)
        self.assertEqual(self.row(self.app.get_json(self.endpoint()), 'codex')['state'], 'conflict')
        self.action('codex', 'remove', status=409)
        self.assertEqual(count, len(self.fixture.codex.mutations))

    def test_stale_artifact_blocks_all_actions(self):
        self.action('claude', 'deploy')
        (Path(self.package['artifactRoot']) / 'changed.txt').write_text('changed')
        row = self.row(self.app.get_json(self.endpoint()), 'claude')
        self.assertEqual(row['state'], 'stale')
        self.assertEqual(row['actions'], [])
        self.action('claude', 'disable', status=409)

    def test_explicit_distribution_selects_its_own_managed_snapshot(self):
        child = self.fixture.adopt(self.store, 'b', '2.0.0', locator='github:example/codex-distribution')
        resolution = self.fixture.resolution('d', '1.0.0', 'parent', locator='github:example/parent')
        resolution.distributions = (DistributionRelationship(PackageSource(**child['source']),
                                    'native-distribution', 'explicit-test-declaration', 'codex'),)
        parent = self.store.adopt(resolution, name='Parent', observations=[])
        view = self.app.get_json(self.endpoint(parent))
        self.assertEqual(self.row(view, 'claude')['selectedPackageId'], parent['id'])
        self.assertEqual(self.row(view, 'codex')['selectedPackageId'], child['id'])
        deployed = self.action('codex', 'deploy', parent)
        self.assertEqual(self.row(deployed, 'codex')['source']['locator'], child['source']['locator'])
        self.assertEqual(self.row(deployed, 'codex')['state'], 'enabled')

    def test_legacy_opencode_declarations_are_included_in_external_evidence(self):
        legacy = self.app.spec.home / '.opencode/opencode.jsonc'
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({'plugin': ['github:example/fixture#' + 'a' * 40]}))
        before = legacy.read_bytes()
        factory = NativePackageAdapterFactory(self.app.container.harness_kernel, {})
        self.fixture.adapters['opencode'] = factory('opencode')
        row = self.row(self.app.get_json(self.endpoint()), 'opencode')
        self.assertEqual(row['state'], 'external-existing')
        self.assertEqual(row['actions'], [])
        self.assertEqual(legacy.read_bytes(), before)

    def test_npm_replacement_uses_the_explicit_registry_coordinate_across_versions(self):
        records = []
        for revision, version in [('e', '1.0.0'), ('f', '2.0.0')]:
            resolution = self.fixture.resolution(revision, version, revision)
            resolution.source = PackageSource('npm', f'npm:@example/fixture@{version}', version=version, revision=version)
            records.append(self.store.adopt(resolution, name='Package', observations=[]))
        first, second = records
        self.action('codex', 'deploy', first)
        options = self.row(self.app.get_json(self.endpoint(first)), 'codex')['replacementOptions']
        self.assertEqual([option['packageId'] for option in options], [second['id']])
        result = self.action('codex', 'update', first, replacementPackageId=second['id'])
        self.assertEqual(result['packageId'], second['id'])
        self.assertEqual(self.row(result, 'codex')['state'], 'enabled')
