"""Real package/planner/state/CLI-parser integration; only the native process is fake."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from skill_manager.application.skills.managed_packages import ManagedPackageStore
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from skill_manager.application.skills.package_deployment_service import PackageDeploymentService, PackageDeploymentError
from skill_manager.application.skills.native_package_cli import CodexNativePackageAdapter


class NativeProcess:
    def __init__(self, root):
        self.root, self.markets, self.installed = root, {}, {}
        self.mutations = []
        self.fail_install = False

    def __call__(self, argv):
        if argv[1:4] == ['plugin', 'marketplace', 'list']:
            return {'marketplaces': [{'name': n, 'root': str(p), 'marketplaceSource': {
                'sourceType': 'local', 'source': str(p)}} for n, p in self.markets.items()]}
        if argv[1:3] == ['plugin', 'list']:
            return {'installed': list(self.installed.values()), 'available': []}
        self.mutations.append(argv)
        if argv[1:4] == ['plugin', 'marketplace', 'add']:
            root = Path(argv[4])
            name = json.loads((root / '.agents/plugins/marketplace.json').read_text())['name']
            already = name in self.markets
            self.markets[name] = root
            return {'marketplaceName': name, 'installedRoot': str(root), 'alreadyAdded': already}
        if argv[1:4] == ['plugin', 'marketplace', 'remove']:
            del self.markets[argv[4]]
            return {}
        if argv[1:3] == ['plugin', 'add']:
            if self.fail_install:
                raise RuntimeError('Native install failed after marketplace creation')
            native_id = argv[3]
            name, market = native_id.split('@')
            root = self.markets[market]
            document = json.loads((root / '.agents/plugins/marketplace.json').read_text())
            source = root / document['plugins'][0]['source']['path']
            version = json.loads((source / '.codex-plugin/plugin.json').read_text())['version']
            cache = self.root / 'plugins/cache' / market / name / version
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, cache, dirs_exist_ok=True)
            self.installed[native_id] = {'pluginId':native_id, 'name':name, 'marketplaceName':market,
                'version':version, 'installed':True, 'enabled':True, 'source':{'source':'local','path':str(source)},
                'installPolicy':'AVAILABLE', 'authPolicy':'ON_INSTALL'}
            return {'pluginId':native_id, 'installedPath':str(cache)}
        if argv[1:3] == ['plugin', 'remove']:
            row = self.installed.pop(argv[3])
            shutil.rmtree(self.root / 'plugins/cache' / row['marketplaceName'] / row['name'])
            return {}
        raise AssertionError(argv)


class NativePackageServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packages = ManagedPackageStore(self.root / 'managed')
        self.record = self.adopt('a', '1.0.0')
        self.process = NativeProcess(self.root / 'native')
        self.adapter = CodexNativePackageAdapter(self.process.root, self.process, mechanism_available=True)
        self.service = PackageDeploymentService(self.packages)

    def adopt(self, revision, version):
        source = self.root / ('source-' + revision)
        (source / '.codex-plugin').mkdir(parents=True)
        (source / '.codex-plugin/plugin.json').write_text(json.dumps({'name':'fixture', 'version':version}))
        (source / 'skills/fixture').mkdir(parents=True)
        (source / 'skills/fixture/SKILL.md').write_text('---\nname: fixture\ndescription: test\n---\n' + version)
        (source / 'hooks').mkdir()
        (source / 'hooks/hooks.json').write_text('{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"true"}]}]}}')
        return self.packages.adopt(PackageResolution('resolved', revision,
            source=PackageSource('github', 'github:example/fixture', revision=revision * 40), artifact_root=source,
            capabilities=SourcePackageDiscovery().inspect_root(source)['package']), name='Fixture', observations=[])

    def test_native_install_update_restart_and_repeat_remove(self):
        newer = self.adopt('b', '2.0.0')
        central = self.packages.manifest.read_bytes()
        record = self.service.deploy(self.record['id'], self.adapter)['deployment']
        self.assertIn('nativeFingerprint', record)
        self.assertTrue((Path(record['target']) / '.agents/plugins/marketplace.json').exists())
        count = len(self.process.mutations)
        self.assertFalse(self.service.deploy(self.record['id'], self.adapter)['changed'])
        self.assertEqual(len(self.process.mutations), count)
        updated = self.service.update(record['deploymentId'], newer['id'], self.adapter)['deployment']
        self.assertEqual(updated['target'], record['target'])
        self.assertEqual(updated['placementPackageId'], self.record['id'])
        restarted = PackageDeploymentService(ManagedPackageStore(self.packages.root))
        self.assertEqual(restarted.reconcile(record['deploymentId'], self.adapter)['state'], 'managed')
        restarted.remove(record['deploymentId'], self.adapter)
        self.assertFalse(restarted.remove(record['deploymentId'], self.adapter)['changed'])
        self.assertEqual(self.process.installed, {})
        self.assertEqual(self.process.markets, {})
        self.assertEqual(restarted.list_deployments(), {})
        self.assertEqual(central, self.packages.manifest.read_bytes())

    def test_partial_native_install_never_becomes_managed_after_restart(self):
        self.process.fail_install = True
        with self.assertRaises(Exception):
            self.service.deploy(self.record['id'], self.adapter)
        self.assertEqual(self.service.list_deployments(), {})
        self.assertTrue(self.process.markets)
        count = len(self.process.mutations)
        with self.assertRaises(PackageDeploymentError) as error:
            PackageDeploymentService(self.packages).deploy(self.record['id'], self.adapter)
        self.assertEqual(error.exception.plan.actions, [])
        self.assertEqual(count, len(self.process.mutations))

    def test_native_success_with_state_write_failure_is_not_reclaimed(self):
        with patch.object(self.service, '_save_state', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.service.deploy(self.record['id'], self.adapter)
        self.assertTrue(self.process.installed)
        self.assertEqual(self.service.list_deployments(), {})
        count = len(self.process.mutations)
        with self.assertRaises(PackageDeploymentError):
            PackageDeploymentService(self.packages).deploy(self.record['id'], self.adapter)
        self.assertEqual(count, len(self.process.mutations))

    def test_native_cache_drift_blocks_owned_removal(self):
        record = self.service.deploy(self.record['id'], self.adapter)['deployment']
        row = self.process.installed[record['nativeId']]
        path = self.process.root / 'plugins/cache' / row['marketplaceName'] / row['name'] / row['version'] / 'external.txt'
        path.write_text('do not delete')
        before = self.service.state_path.read_bytes()
        count = len(self.process.mutations)
        with self.assertRaises(PackageDeploymentError):
            self.service.remove(record['deploymentId'], self.adapter)
        self.assertEqual(count, len(self.process.mutations))
        self.assertEqual(self.service.state_path.read_bytes(), before)
        self.assertEqual(path.read_text(), 'do not delete')

    def test_remove_recovers_state_write_after_native_absence_is_verified(self):
        record = self.service.deploy(self.record['id'], self.adapter)['deployment']
        with patch.object(self.service, '_save_state', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.service.remove(record['deploymentId'], self.adapter)
        self.assertFalse(self.process.installed)
        count = len(self.process.mutations)
        restarted = PackageDeploymentService(self.packages)
        self.assertTrue(restarted.remove(record['deploymentId'], self.adapter)['verified'])
        self.assertEqual(restarted.list_deployments(), {})
        self.assertEqual(count, len(self.process.mutations))

    def test_native_cache_sibling_drift_cannot_use_local_source_fallback(self):
        record = self.service.deploy(self.record['id'], self.adapter)['deployment']
        row = self.process.installed[record['nativeId']]
        sibling = self.process.root / 'plugins/cache' / row['marketplaceName'] / row['name'] / 'external.txt'
        sibling.write_text('external cache data')
        count = len(self.process.mutations)
        self.assertEqual(self.service.reconcile(record['deploymentId'], self.adapter)['state'], 'conflict')
        with self.assertRaises(PackageDeploymentError):
            self.service.remove(record['deploymentId'], self.adapter)
        self.assertEqual(count, len(self.process.mutations))
        self.assertEqual(sibling.read_text(), 'external cache data')

    def test_removed_old_namespace_reused_externally_is_not_reported_as_removed(self):
        record = self.service.deploy(self.record['id'], self.adapter)['deployment']
        newer = self.adopt('b', '2.0.0')
        self.service.update(record['deploymentId'], newer['id'], self.adapter)
        self.service.remove(record['deploymentId'], self.adapter)
        self.process.markets[record['nativeId'].split('@')[1]] = self.root / 'external'
        before = self.service.state_path.read_bytes()
        count = len(self.process.mutations)
        with self.assertRaises(PackageDeploymentError):
            self.service.remove(record['deploymentId'], self.adapter)
        self.assertEqual(self.service.state_path.read_bytes(), before)
        self.assertEqual(count, len(self.process.mutations))

    def test_marketplace_staging_failure_removes_private_scratch(self):
        with patch.object(self.service, '_stage_marketplace', side_effect=OSError('staging failure')):
            with self.assertRaises(OSError):
                self.service.deploy(self.record['id'], self.adapter)
        self.assertFalse(self.process.root.exists())
        self.assertFalse(self.process.mutations)
        self.assertEqual(self.service.list_deployments(), {})

    def test_empty_external_marketplace_reserves_its_namespace(self):
        from skill_manager.application.skills.package_deployment import PackageDeploymentPlanner, NativeTarget
        plan = PackageDeploymentPlanner(self.packages).plan(self.record['id'],
            NativeTarget('codex', self.process.root, inventory_complete=True, mechanism_available=True))
        self.process.markets[plan.surface['marketplaceId']] = self.root / 'external'
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], self.adapter)
        self.assertEqual(self.process.mutations, [])
