from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import shutil
from unittest.mock import patch

from skill_manager.application.skills.managed_packages import ManagedPackageStore
from skill_manager.application.skills.package_deployment import (
    NativeRegistration,
    NativeTarget,
    PackageDeploymentPlanner,
)
from skill_manager.application.skills.package_deployment_service import (
    PackageDeploymentError,
    PackageDeploymentService,
)
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource
from skill_manager.application.skills.source_package import SourcePackageDiscovery


class FakeNativeAdapter:
    def __init__(self, harness: str, root: Path, *, registrations=(), verify=True, enabled=True):
        self.harness = harness
        self.root = root
        self.registrations = list(registrations)
        self.verify_result = verify
        self.enabled = enabled
        self.inspect_calls = []
        self.verify_calls = []
        self.set_enabled_calls = []

    def inspect(self, package_id, deployment=None):
        self.inspect_calls.append((package_id, deployment))
        registrations = list(self.registrations)
        if deployment is not None:
            registrations.append(NativeRegistration(
                deployment['nativeId'], 'native-root',
                root=Path(deployment['target']), deployment_id=deployment['deploymentId'],
            ))
        return NativeTarget(
            self.harness,
            self.root,
            tuple(registrations),
            inventory_complete=True,
            mechanism_available=True,
        )

    def verify(self, plan, expected):
        self.verify_calls.append((plan, expected))
        if callable(self.verify_result):
            verified = self.verify_result(plan, expected)
        else:
            verified = self.verify_result
        return {'verified': verified, 'enabled': self.enabled}

    def set_enabled(self, plan, enabled):
        self.set_enabled_calls.append((plan, enabled))
        self.enabled = enabled


class PackageDeploymentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ManagedPackageStore(self.root / 'managed')
        self.record = self._adopt('a' * 40, 'source-a', 'initial')
        self.service = PackageDeploymentService(self.store, PackageDeploymentPlanner(self.store))

    def _adopt(self, revision, locator, content):
        artifact = self.root / f'acquired-{revision[:4]}'
        (artifact / '.claude-plugin').mkdir(parents=True)
        (artifact / '.cursor-plugin').mkdir()
        (artifact / 'agents').mkdir()
        (artifact / 'rules').mkdir()
        (artifact / '.claude-plugin/plugin.json').write_text('{"name":"example"}')
        (artifact / '.cursor-plugin/plugin.json').write_text('{"name":"example"}')
        (artifact / 'SKILL.md').write_text('# Example Skill\n\nA whole package fixture.\n')
        (artifact / 'agents/example-agent.md').write_text('agent\n')
        (artifact / 'rules/example-rule.md').write_text('rule\n')
        (artifact / 'payload.txt').write_text(content)
        resolution = PackageResolution(
            'resolved', 'skill-ref',
            source=PackageSource('github', f'github:example/{locator}', revision=revision, package_path='.'),
            artifact_root=artifact,
            capabilities=SourcePackageDiscovery().inspect_root(artifact)['package'],
        )
        return self.store.adopt(resolution, name='Example', observations=[])

    def _adapter(self, harness='claude', **kwargs):
        return FakeNativeAdapter(harness, self.root / 'native' / harness, **kwargs)

    def test_repeat_without_deployment_id_is_noop_and_remove_repeats_after_restart(self):
        adapter = self._adapter()
        deployed = self.service.deploy(self.record['id'], adapter)['deployment']
        self.assertFalse(self.service.deploy(self.record['id'], adapter)['changed'])
        self.service.remove(deployed['deploymentId'], adapter)
        restarted = PackageDeploymentService(ManagedPackageStore(self.store.root))
        self.assertFalse(restarted.remove(deployed['deploymentId'], adapter)['changed'])
        self.assertEqual(restarted.list_deployments(), {})
        self.assertEqual(self.store.get(self.record['id'])['artifactState'], 'current')

    def test_owned_install_never_hides_separate_external_source_match(self):
        adapter = self._adapter()
        deployed = self.service.deploy(self.record['id'], adapter)['deployment']
        source = PackageSource(**self.record['source'])
        adapter.registrations.append(NativeRegistration('other', 'native-source', source=source))
        before = self.service.state_path.read_bytes()
        with self.assertRaises(PackageDeploymentError) as error:
            self.service.remove(deployed['deploymentId'], adapter)
        self.assertEqual(error.exception.plan.ownership, 'external-existing')
        self.assertEqual(before, self.service.state_path.read_bytes())
        self.assertTrue(Path(deployed['target']).is_dir())

    def test_malformed_verification_never_creates_ownership(self):
        for value in ('false', 1, [], None):
            with self.subTest(value=value):
                with self.assertRaises(PackageDeploymentError):
                    self.service.deploy(self.record['id'], self._adapter(verify=value))
                self.assertEqual(self.service.list_deployments(), {})

    def test_replaced_identical_target_during_verification_is_not_owned_or_deleted(self):
        adapter = self._adapter()
        def replaced(plan, expected):
            destination = Path(plan.surface['path'])
            displaced = self.root / 'displaced'
            destination.rename(displaced)
            shutil.copytree(displaced, destination)
            return True
        adapter.verify_result = replaced
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)
        self.assertEqual(self.service.list_deployments(), {})
        self.assertTrue((adapter.root / 'skills' / ('skill-manager-' + self.record['id'][:16])).exists())

    def test_replaced_backup_is_not_restored_or_deleted(self):
        adapter = self._adapter()
        record = self.service.deploy(self.record['id'], adapter)['deployment']
        newer = self._adopt('b'*40, 'next', 'updated')
        replacement = []
        def verify(plan, expected):
            backups = list((adapter.root / '.skill-manager-staging').glob('backup-*'))
            if backups:
                backup = backups[0]
                original = self.root / 'displaced-backup'
                backup.rename(original)
                shutil.copytree(original, backup)
                replacement.append(backup)
                return False
            return True
        adapter.verify_result = verify
        with self.assertRaises(PackageDeploymentError):
            self.service.update(record['deploymentId'], newer['id'], adapter)
        self.assertTrue(replacement[0].exists())
        self.assertFalse(Path(record['target']).exists())

    def test_replaced_stage_is_neither_promoted_nor_deleted(self):
        adapter = self._adapter()
        inspect = adapter.inspect
        replacements = []
        def replaced(package_id, deployment=None):
            target = inspect(package_id, deployment)
            stages = list((adapter.root / '.skill-manager-staging').glob('*'))
            if stages and not replacements:
                stage = stages[0]
                original = self.root / 'displaced-stage'
                stage.rename(original)
                shutil.copytree(original, stage)
                replacements.append(stage)
            return target
        adapter.inspect = replaced
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)
        self.assertTrue(replacements[0].exists())
        self.assertFalse((adapter.root / 'skills' / ('skill-manager-' + self.record['id'][:16])).exists())
        self.assertEqual(self.service.list_deployments(), {})

    def test_staging_and_backup_are_not_inside_loader_directories(self):
        adapter = self._adapter()
        def verify(plan, expected):
            loader = adapter.root / 'skills'
            self.assertEqual(len(list(loader.iterdir())), 1)
            return True
        adapter.verify_result = verify
        deployed = self.service.deploy(self.record['id'], adapter)['deployment']
        newer = self._adopt('b' * 40, 'next', 'updated')
        self.service.update(deployed['deploymentId'], newer['id'], adapter)

    def test_replacement_is_rechecked_after_staging(self):
        adapter = self._adapter()
        record = self.service.deploy(self.record['id'], adapter)['deployment']
        newer = self._adopt('b'*40, 'next', 'updated')
        stage = self.service._stage_snapshot
        def changed(*args, **kwargs):
            result = stage(*args, **kwargs)
            (Path(newer['artifactRoot']) / 'tampered').write_text('changed')
            return result
        before = self.service.state_path.read_bytes()
        with patch.object(self.service, '_stage_snapshot', side_effect=changed):
            with self.assertRaises(PackageDeploymentError):
                self.service.update(record['deploymentId'], newer['id'], adapter)
        self.assertEqual((Path(record['target']) / 'payload.txt').read_text(), 'initial')
        self.assertEqual(before, self.service.state_path.read_bytes())

    def test_reconcile_reports_drift_without_mutating_target_or_state(self):
        adapter = self._adapter()
        deployed = self.service.deploy(self.record['id'], adapter)['deployment']
        changed = Path(deployed['target']) / 'external-change'
        changed.write_text('keep')
        before = self.service.state_path.read_bytes()
        self.assertEqual(self.service.reconcile(deployed['deploymentId'], adapter)['state'], 'conflict')
        self.assertEqual(self.service.state_path.read_bytes(), before)
        self.assertEqual(changed.read_text(), 'keep')

    def test_failed_open_code_proof_does_not_mutate_config(self):
        from skill_manager.application.skills.native_package_cli import ReadOnlyNativePackageAdapter
        adapter = ReadOnlyNativePackageAdapter('opencode', self.root / 'opencode')
        adapter.root.mkdir()
        config = adapter.root / 'opencode.jsonc'
        config.write_text('{"plugins":["github:example/source-a#' + 'a'*40 + '","-example"]}')
        before = config.read_bytes()
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.service.list_deployments(), {})

    def test_separate_distributions_deploy_only_selected_snapshots(self):
        from skill_manager.application.skills.package_resolution import DistributionRelationship
        from dataclasses import replace
        other = self._adopt('b'*40, 'other-harness', 'cursor-only')
        base = self.root / 'acquired-aaaa'
        family = self.store.adopt(PackageResolution('resolved', 'family',
            source=PackageSource('github', 'github:example/family', revision='c'*40), artifact_root=base,
            capabilities=SourcePackageDiscovery().inspect_root(base)['package'], distributions=(
                DistributionRelationship(PackageSource(**self.record['source']), 'native', 'explicit', 'claude'),
                DistributionRelationship(PackageSource(**other['source']), 'native', 'explicit', 'cursor'))),
            name='Family', observations=[])
        left = self.service.deploy(family['id'], self._adapter('claude'))['deployment']
        right = self.service.deploy(family['id'], self._adapter('cursor'))['deployment']
        self.assertEqual(left['selectedPackageId'], self.record['id'])
        self.assertEqual(right['selectedPackageId'], other['id'])
        self.assertEqual((Path(right['target']) / 'payload.txt').read_text(), 'cursor-only')
        self.service.remove(left['deploymentId'], self._adapter('claude'))
        self.assertTrue(Path(right['target']).is_dir())

    def test_deploys_one_owned_whole_snapshot_and_persists_minimal_state(self):
        adapter = self._adapter()

        result = self.service.deploy(self.record['id'], adapter)

        deployment = result['deployment']
        target = Path(deployment['target'])
        self.assertTrue((target / 'SKILL.md').is_file())
        self.assertTrue((target / 'agents/example-agent.md').is_file())
        self.assertTrue((target / 'rules/example-rule.md').is_file())
        self.assertEqual(deployment['managedPackageId'], self.record['id'])
        self.assertEqual(deployment['selectedPackageId'], self.record['id'])
        self.assertTrue(deployment['verified'])
        self.assertTrue((self.root / 'managed/deployments.json').is_file())
        self.assertEqual(
            set(deployment), {
                'deploymentId', 'managedPackageId', 'selectedPackageId', 'harness', 'root',
                'target', 'nativeId', 'strategy', 'appliedFingerprint', 'verified', 'enabled',
                'targetFingerprint', 'targetIdentity', 'placementPackageId', 'verifiedAt',
            },
        )

    def test_repeat_and_restart_reuse_owned_target_without_copying_external_state(self):
        adapter = self._adapter()
        first = self.service.deploy(self.record['id'], adapter)['deployment']
        before = (Path(first['target']) / 'payload.txt').read_bytes()

        restarted = PackageDeploymentService(
            ManagedPackageStore(self.root / 'managed'),
            PackageDeploymentPlanner(ManagedPackageStore(self.root / 'managed')),
        )
        second = restarted.deploy(self.record['id'], adapter, deployment_id=first['deploymentId'])['deployment']

        self.assertEqual(second, first)
        self.assertEqual((Path(first['target']) / 'payload.txt').read_bytes(), before)
        self.assertEqual(len(adapter.set_enabled_calls), 0)

    def test_remove_replans_current_package_then_removes_only_owned_target(self):
        adapter = self._adapter()
        deployment = self.service.deploy(self.record['id'], adapter)['deployment']

        result = self.service.remove(deployment['deploymentId'], adapter)

        self.assertTrue(result['verified'])
        self.assertFalse(Path(deployment['target']).exists())
        self.assertEqual(self.service.list_deployments(), {})
        self.assertGreaterEqual(len(adapter.inspect_calls), 2)

    def test_update_requires_explicit_new_package_id_and_replaces_owned_target(self):
        adapter = self._adapter()
        deployment = self.service.deploy(self.record['id'], adapter)['deployment']
        new_record = self._adopt('b' * 40, 'source-b', 'updated')
        (Path(new_record['artifactRoot']) / '.claude-plugin/plugin.json').write_text('{"name":"example"}')

        updated = self.service.update(deployment['deploymentId'], new_record['id'], adapter)['deployment']

        self.assertEqual(updated['deploymentId'], deployment['deploymentId'])
        self.assertEqual(updated['managedPackageId'], new_record['id'])
        self.assertEqual(updated['selectedPackageId'], new_record['id'])
        self.assertEqual(Path(updated['target']), Path(deployment['target']))
        self.assertEqual((Path(updated['target']) / 'payload.txt').read_text(), 'updated')
        self.assertEqual(self.store.get(self.record['id'])['state'], 'current')

    def test_blocked_external_and_conflicting_plans_make_no_target_or_state_mutation(self):
        cases = (
            ('blocked', self._adapter(verify=True), {'mechanism_available': False}),
            ('external', self._adapter(registrations=(NativeRegistration(
                'github:example/source-a:.', 'native-source',
                PackageSource('github', 'github:example/source-a', revision='a' * 40, package_path='.'),
            ),)), {}),
            ('conflict', self._adapter(registrations=(NativeRegistration('example@skills-dir'),)), {}),
        )
        for label, adapter, target_changes in cases:
            with self.subTest(label=label):
                if target_changes:
                    adapter = FakeNativeAdapter(
                        adapter.harness, adapter.root,
                        verify=adapter.verify_result,
                    )
                    # The fake's inspect method is intentionally replaced only for this case.
                    original_inspect = adapter.inspect
                    adapter.inspect = lambda package_id, deployment=None, original=original_inspect: replace(
                        original(package_id, deployment), mechanism_available=False,
                    )
                with self.assertRaises(PackageDeploymentError):
                    self.service.deploy(self.record['id'], adapter)
                self.assertFalse((self.root / 'managed/deployments.json').exists())
                self.assertFalse((self.root / 'native').exists())

    def test_unhealthy_task05_blocks_remove_without_state_or_target_mutation(self):
        adapter = self._adapter()
        deployment = self.service.deploy(self.record['id'], adapter)['deployment']
        self.store.record_refresh(self.record['id'], PackageResolution('unavailable', 'skill-ref', reason='offline'))
        with self.assertRaises(PackageDeploymentError):
            self.service.remove(deployment['deploymentId'], adapter)

        self.assertTrue(Path(deployment['target']).is_dir())
        self.assertEqual(self.service.list_deployments()[deployment['deploymentId']], deployment)

    def test_name_only_or_unrecorded_existing_directory_is_conflict(self):
        adapter = self._adapter()
        destination = adapter.root / 'skills' / f"skill-manager-{self.record['id'][:16]}"
        destination.mkdir(parents=True)
        (destination / 'external.txt').write_text('keep')

        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        self.assertEqual((destination / 'external.txt').read_text(), 'keep')
        self.assertFalse((self.root / 'managed/deployments.json').exists())

    def test_owned_target_drift_is_not_adopted_or_overwritten(self):
        adapter = self._adapter()
        deployment = self.service.deploy(self.record['id'], adapter)['deployment']
        (Path(deployment['target']) / 'payload.txt').write_text('external drift')
        before = (Path(deployment['target']) / 'payload.txt').read_text()

        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter, deployment_id=deployment['deploymentId'])

        self.assertEqual((Path(deployment['target']) / 'payload.txt').read_text(), before)
        self.assertEqual(self.service.list_deployments()[deployment['deploymentId']], deployment)

    def test_failed_verification_rolls_back_only_just_created_matching_target(self):
        adapter = self._adapter(verify=False)

        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        self.assertFalse((self.root / 'native').exists())
        self.assertFalse((self.root / 'managed/deployments.json').exists())

    def test_verifier_exception_rolls_back_the_new_snapshot_without_claiming_it(self):
        def fail(_plan, _expected):
            raise RuntimeError('runtime unavailable')

        adapter = self._adapter(verify=fail)
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        self.assertFalse((self.root / 'native').exists())
        self.assertFalse((self.root / 'managed/deployments.json').exists())

    def test_failed_verification_does_not_delete_target_changed_by_external_boundary(self):
        def mutate_then_fail(plan, expected):
            if expected == 'present':
                (Path(plan.surface['path']) / 'external.txt').write_text('unowned')
            return False

        adapter = self._adapter(verify=mutate_then_fail)
        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        target = adapter.root / 'skills' / f"skill-manager-{self.record['id'][:16]}"
        self.assertTrue((target / 'external.txt').is_file())
        self.assertFalse((self.root / 'managed/deployments.json').exists())

    def test_symlinked_native_parent_is_never_followed(self):
        adapter = self._adapter()
        outside = self.root / 'outside'
        outside.mkdir()
        adapter.root.mkdir(parents=True)
        (adapter.root / 'skills').symlink_to(outside, target_is_directory=True)

        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.root / 'managed/deployments.json').exists())

    def test_native_install_strategies_remain_manual_in_this_slice(self):
        adapter = self._adapter('codex')

        with self.assertRaises(PackageDeploymentError):
            self.service.deploy(self.record['id'], adapter)

        self.assertFalse((self.root / 'managed/deployments.json').exists())
        self.assertFalse((self.root / 'native').exists())

    def test_enable_disable_requires_adapter_control_and_persists_actual_state(self):
        adapter = self._adapter()
        deployment = self.service.deploy(self.record['id'], adapter)['deployment']

        self.service.disable(deployment['deploymentId'], adapter)
        disabled = self.service.list_deployments()[deployment['deploymentId']]
        self.assertFalse(disabled['enabled'])
        self.service.enable(deployment['deploymentId'], adapter)
        self.assertTrue(self.service.list_deployments()[deployment['deploymentId']]['enabled'])

        unsupported = self._adapter(verify=True)
        unsupported.set_enabled = None
        with self.assertRaises(PackageDeploymentError):
            self.service.disable(deployment['deploymentId'], unsupported)
        self.assertTrue(self.service.list_deployments()[deployment['deploymentId']]['enabled'])

    def test_crash_leftover_is_not_adopted_or_deleted(self):
        adapter = self._adapter()
        leftover = adapter.root / 'skills' / '.skill-manager-leftover.staging'
        leftover.mkdir(parents=True)
        (leftover / 'keep').write_text('manual inspection')

        deployment = self.service.deploy(self.record['id'], adapter)['deployment']

        self.assertTrue(leftover.is_dir())
        self.assertTrue(Path(deployment['target']).is_dir())
        self.assertNotEqual(deployment['target'], str(leftover))


if __name__ == '__main__':
    unittest.main()
