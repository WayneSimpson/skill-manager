import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import shutil
from dataclasses import replace

from skill_manager.application.skills.managed_packages import ManagedPackageStore
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource
from skill_manager.application.skills.package_resolution import DistributionRelationship
from skill_manager.application.skills.source_package import SourcePackageDiscovery


class ManagedPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ManagedPackageStore(self.root / 'managed')

    def resolution(self, folder='download', revision='a' * 40):
        root = self.root / folder
        (root / '.codex-plugin').mkdir(parents=True)
        (root / '.codex-plugin/plugin.json').write_text('{"name":"example"}')
        (root / 'uninterpreted.txt').write_text('keep the whole package')
        return PackageResolution('resolved', 'skill-one', source=PackageSource(
            'github', 'github:example/package', revision=revision, package_path='.'),
            artifact_root=root, capabilities=SourcePackageDiscovery().inspect_root(root)['package'])

    def test_identity_is_independent_of_temp_root_and_survives_restart(self):
        first = self.resolution()
        record = self.store.adopt(first, name='One', observations=[])
        second = self.resolution('another')
        second.observation_ref = 'skill-two'
        again = self.store.adopt(second, name='Two', observations=[])
        self.assertEqual(record['id'], again['id'])
        self.assertNotEqual(record['id'], first.capabilities['id'])
        loaded = ManagedPackageStore(self.root / 'managed').get(record['id'])
        self.assertEqual(loaded['state'], 'current')
        self.assertEqual(set(loaded['skillRefs']), {'skill-one', 'skill-two'})
        self.assertTrue((Path(loaded['artifactRoot']) / 'uninterpreted.txt').is_file())
        self.assertNotIn(str(first.artifact_root), (self.root / 'managed/manifest.json').read_text())

    def test_source_change_is_stale_without_replacing_retained_artifact(self):
        old = self.store.adopt(self.resolution(), name='One', observations=[])
        self.store.record_refresh(old['id'], self.resolution('new', revision='b' * 40))
        changed = self.store.get(old['id'])
        self.assertEqual(changed['state'], 'changed')
        self.assertEqual(changed['source']['revision'], 'a' * 40)
        self.assertEqual(changed['candidateSource']['revision'], 'b' * 40)

    def test_unavailable_keeps_captured_package(self):
        old = self.store.adopt(self.resolution(), name='One', observations=[])
        self.store.record_refresh(old['id'], PackageResolution('unavailable', 'skill-one', reason='offline'))
        result = self.store.get(old['id'])
        self.assertEqual(result['state'], 'unavailable')
        self.assertEqual(result['artifactState'], 'current')
        self.assertTrue(Path(result['artifactRoot']).is_dir())

    def test_artifact_survives_original_disappearance(self):
        source = self.resolution()
        record = self.store.adopt(source, name='One', observations=[{
            'harness': 'opencode', 'path': str(source.artifact_root), 'ownership': 'external-existing'}])
        shutil.rmtree(source.artifact_root)
        result = self.store.get(record['id'])
        self.assertEqual(result['state'], 'current')
        self.assertEqual(result['observations'][0]['state'], 'missing')
        self.assertEqual((Path(result['artifactRoot']) / 'uninterpreted.txt').read_text(), 'keep the whole package')

    def test_local_changes_and_missing_artifact_are_distinct(self):
        record = self.store.adopt(self.resolution(), name='One', observations=[])
        root = Path(record['artifactRoot'])
        (root / 'uninterpreted.txt').write_text('edited')
        self.assertEqual(self.store.get(record['id'])['artifactState'], 'changed')
        with self.assertRaises(ValueError):
            self.store.adopt(self.resolution('new'), name='One', observations=[])
        self.assertEqual((root / 'uninterpreted.txt').read_text(), 'edited')
        shutil.rmtree(root)
        self.assertEqual(self.store.get(record['id'])['state'], 'missing')

    def test_capture_preserves_unknown_files_empty_directories_and_executable_bits(self):
        source = self.resolution()
        root = source.artifact_root
        (root / 'empty-support').mkdir()
        (root / 'tool.sh').write_text('exit 91')
        (root / 'tool.sh').chmod(0o755)
        for folder in ('.git', '.cache', 'node_modules'):
            (root / folder).mkdir()
            (root / folder / 'secret').write_text('not retained')
        (root / '.env').write_text('SECRET=value')
        record = self.store.adopt(source, name='One', observations=[])
        owned = Path(record['artifactRoot'])
        self.assertTrue((owned / 'empty-support').is_dir())
        self.assertTrue((owned / 'tool.sh').stat().st_mode & 0o111)
        self.assertFalse((owned / '.env').exists())
        self.assertFalse((owned / 'node_modules').exists())
        self.assertFalse((owned / '.git').exists())
        self.assertEqual((root / '.env').read_text(), 'SECRET=value')

    def test_symlink_escape_and_unowned_destination_are_rejected(self):
        source = self.resolution()
        outside = self.root / 'outside'
        outside.write_text('keep')
        (source.artifact_root / 'escape').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.store.adopt(source, name='One', observations=[])
        self.assertEqual(outside.read_text(), 'keep')
        (source.artifact_root / 'escape').unlink()
        from skill_manager.application.skills.managed_packages import managed_package_id
        dest = self.root / 'managed/artifacts' / managed_package_id(source.source)
        dest.mkdir(parents=True)
        (dest / 'not-owned').write_text('leave alone')
        with self.assertRaises(ValueError):
            self.store.adopt(source, name='One', observations=[])
        self.assertEqual((dest / 'not-owned').read_text(), 'leave alone')

    def test_failed_manifest_write_does_not_publish_artifact(self):
        source = self.resolution()
        with patch.object(self.store, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.adopt(source, name='One', observations=[])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(list((self.root / 'managed/artifacts').iterdir()), [])

    def test_unresolved_and_ambiguous_do_not_fabricate_package(self):
        for state in ('unresolved', 'ambiguous', 'unavailable'):
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.store.adopt(PackageResolution(state, state, reason=state), name='Unknown', observations=[])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(set(self.store.links()), {'unresolved', 'ambiguous', 'unavailable'})
        self.assertFalse((self.root / 'managed/artifacts').exists())

    def test_resolved_source_without_artifact_is_explicit(self):
        resolution = self.resolution()
        resolution.artifact_root = None
        record = self.store.adopt(resolution, name='One', observations=[])
        self.assertEqual(record['state'], 'unavailable')
        self.assertEqual(record['artifactState'], 'not-retained')
        self.assertIsNone(record['artifactRoot'])
        self.assertIsNone(record['fingerprint'])
        self.assertEqual(record['source']['revision'], 'a' * 40)

    def test_distribution_links_persist_only_when_explicit(self):
        resolution = self.resolution()
        resolution.distributions = (DistributionRelationship(
            PackageSource('npm', 'npm:example@1.2.3', version='1.2.3'),
            'publisher-declared distribution', 'fixture authoritative relationship'),)
        record = self.store.adopt(resolution, name='One', observations=[])
        restarted = ManagedPackageStore(self.root / 'managed').get(record['id'])
        self.assertEqual(restarted['distributions'][0]['source']['locator'], 'npm:example@1.2.3')
        self.assertIsNone(restarted['distributions'][0]['harness'])

    def test_ref_only_change_is_reported(self):
        resolution = self.resolution()
        record = self.store.adopt(resolution, name='One', observations=[])
        resolution.source = replace(resolution.source, ref='new-ref')
        changed = self.store.record_refresh(record['id'], resolution)
        self.assertEqual(changed['state'], 'changed')

    def test_owned_tree_symlink_or_credential_injection_not_reported_current(self):
        record = self.store.adopt(self.resolution(), name='One', observations=[])
        root = Path(record['artifactRoot'])
        (root / '.env').write_text('secret')
        self.assertEqual(self.store.get(record['id'])['artifactState'], 'unavailable')

    def test_new_link_and_failed_refresh_keep_known_changed_source(self):
        old = self.store.adopt(self.resolution(), name='One', observations=[])
        self.store.record_refresh(old['id'], self.resolution('next', revision='b' * 40))
        another = self.resolution('same-version')
        another.observation_ref = 'skill-two'
        linked = self.store.adopt(another, name='Two', observations=[])
        self.assertEqual(linked['candidateSource']['revision'], 'b' * 40)
        self.assertEqual(linked['state'], 'changed')
        offline = self.store.record_refresh(old['id'], PackageResolution('unavailable', 'skill-one'))
        self.assertEqual(offline['candidateSource']['revision'], 'b' * 40)
        self.assertEqual(offline['state'], 'unavailable')

    def test_environment_loader_file_not_captured(self):
        source = self.resolution()
        (source.artifact_root / '.envrc').write_text('export SECRET=value')
        record = self.store.adopt(source, name='One', observations=[])
        self.assertFalse((Path(record['artifactRoot']) / '.envrc').exists())

    def test_rechecking_pinned_source_does_not_erase_known_newer_candidate(self):
        old_resolution = self.resolution()
        old = self.store.adopt(old_resolution, name='One', observations=[])
        self.store.record_refresh(old['id'], self.resolution('next', revision='b' * 40))
        verified = self.store.record_refresh(old['id'], old_resolution, retained_only=True)
        self.assertEqual(verified['state'], 'changed')
        self.assertEqual(verified['candidateSource']['revision'], 'b' * 40)

    def test_failed_readoption_records_unavailability_without_losing_ownership(self):
        old = self.store.adopt(self.resolution(), name='One', observations=[])
        with self.assertRaises(ValueError):
            self.store.adopt(PackageResolution('unavailable', 'skill-one', reason='offline'), name='One', observations=[])
        self.assertEqual(self.store.get(old['id'])['state'], 'unavailable')
        self.assertEqual(self.store.get(old['id'])['artifactState'], 'current')
