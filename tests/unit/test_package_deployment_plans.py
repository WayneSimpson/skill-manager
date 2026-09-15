from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import asdict, replace
import json
import shutil
from unittest.mock import patch

from skill_manager.application.skills.managed_packages import ManagedPackageStore
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource, DistributionRelationship
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from skill_manager.application.skills.package_deployment import (
    NativeRegistration, NativeTarget, PackageDeploymentPlanner, opencode_isolation, read_opencode_registrations,
)


class PackageDeploymentPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ManagedPackageStore(self.root / 'managed')
        artifact = self.root / 'acquired'
        for harness in ('claude', 'cursor', 'codex'):
            directory = artifact / f'.{harness}-plugin'
            directory.mkdir(parents=True)
            (directory / 'plugin.json').write_text('{"name":"example"}')
        resolution = PackageResolution('resolved', 'skill',
            source=PackageSource('github', 'github:example/package', revision='a' * 40, package_path='.'),
            artifact_root=artifact, capabilities=SourcePackageDiscovery().inspect_root(artifact)['package'])
        self.resolution = resolution
        self.record = self.store.adopt(resolution, name='Example', observations=[])
        self.planner = PackageDeploymentPlanner(self.store)

    def target(self, harness):
        return NativeTarget(harness, self.root / 'native' / harness,
                            inventory_complete=True, mechanism_available=True)

    def test_cross_harness_whole_package_strategies(self):
        expected = {'claude': 'native-local', 'cursor': 'native-local', 'codex': 'native-install'}
        for harness, strategy in expected.items():
            with self.subTest(harness=harness):
                plan = self.planner.plan(self.record['id'], self.target(harness))
                self.assertEqual(plan.strategy, strategy)
                self.assertEqual(plan.support, 'supported', plan.blockers)
                self.assertEqual(plan.ownership, 'absent')
                self.assertTrue(plan.actions)
                self.assertTrue(all(action['unit'] == 'whole-package' for action in plan.actions))

    def test_changed_artifact_blocks_all_actions(self):
        (Path(self.record['artifactRoot']) / 'new-file').write_text('changed')
        plan = self.planner.plan(self.record['id'], self.target('claude'))
        self.assertEqual(plan.artifact_state, 'changed')
        self.assertEqual(plan.actions, [])
        self.assertTrue(plan.requires_reconciliation)

    def test_no_native_ownership_is_fabricated(self):
        plan = self.planner.plan(self.record['id'], NativeTarget('claude', self.root / 'native'))
        self.assertEqual(plan.actions, [])
        self.assertNotEqual(plan.ownership, 'managed')
        self.assertIn('native-inventory-incomplete', plan.blockers)

    def family(self, harness='codex', source=None):
        distribution = DistributionRelationship(source or self.resolution.source, 'native distribution',
                                                'authoritative fixture declaration', harness)
        resolution = replace(self.resolution, observation_ref='family',
                             source=replace(self.resolution.source, locator='github:example/family'),
                             distributions=(distribution,))
        return self.store.adopt(resolution, name='Family', observations=[])

    def test_explicit_distribution_selects_existing_managed_snapshot(self):
        family = self.family()
        plan = self.planner.plan(family['id'], self.target('codex'))
        self.assertEqual(plan.selected_package_id, self.record['id'])
        self.assertEqual(plan.source['locator'], 'github:example/package')
        self.assertEqual(plan.support, 'supported', plan.blockers)
        self.assertEqual(plan.actions[-1]['managedPackageId'], self.record['id'])

    def test_unmanaged_distribution_never_falls_back_to_parent(self):
        family = self.family(source=replace(self.resolution.source, locator='github:unknown/distribution'))
        plan = self.planner.plan(family['id'], self.target('codex'))
        self.assertEqual(plan.actions, [])
        self.assertIn('distribution-not-uniquely-managed', plan.blockers)
        self.assertIsNone(plan.selected_package_id)

    def test_unscoped_source_relationship_is_not_harness_equivalence(self):
        family = self.family(harness=None, source=replace(self.resolution.source, locator='github:unknown/distribution'))
        plan = self.planner.plan(family['id'], self.target('codex'))
        self.assertEqual(plan.selected_package_id, family['id'])
        self.assertIsNone(plan.selected_distribution)

    def test_explicit_opencode_distribution_uses_pinned_native_registration(self):
        family = self.family('opencode')
        plan = self.planner.plan(family['id'], self.target('opencode'))
        self.assertEqual(plan.strategy, 'native-install')
        self.assertEqual(plan.support, 'supported', plan.blockers)
        self.assertEqual(plan.surface['configKey'], 'plugins')
        self.assertEqual(plan.actions[0]['packageSpec'], 'github:example/package#' + 'a' * 40)

    def test_main_or_name_does_not_prove_opencode_native_intent(self):
        plan = self.planner.plan(self.record['id'], replace(self.target('opencode'), registrations=(
            NativeRegistration('example'),)))
        self.assertEqual(plan.strategy, 'manual/unsupported')
        self.assertEqual(plan.actions, [])
        self.assertNotEqual(plan.ownership, 'external-existing')

    def test_existing_opencode_config_source_suppresses_install_without_writing(self):
        config = self.root / 'opencode.jsonc'
        config.write_text(json.dumps({'plugins': [
            {'package': 'github:example/package#' + 'a' * 40, 'options': {'apiKey': 'never-output'}}]}))
        before = config.read_bytes()
        records = read_opencode_registrations((config,))
        plan = self.planner.plan(self.record['id'], replace(self.target('opencode'), registrations=records))
        self.assertEqual(plan.ownership, 'external-existing')
        self.assertEqual(plan.actions, [])
        self.assertEqual(plan.strategy, 'native-install')
        self.assertEqual(config.read_bytes(), before)
        self.assertNotIn('never-output', repr(plan))

    def test_native_identifier_collision_is_conflict_not_external_match(self):
        target = replace(self.target('claude'), registrations=(NativeRegistration('example@skills-dir'),))
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.ownership, 'conflict')
        self.assertEqual(plan.actions, [])
        self.assertIn('native-identifier-conflict', plan.blockers)

    def test_name_similar_registration_not_matched(self):
        target = replace(self.target('claude'), registrations=(NativeRegistration('example-other@skills-dir'),))
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.ownership, 'absent')
        self.assertEqual(plan.selected_package_id, self.record['id'])

    def test_occupied_target_directory_never_overwritten(self):
        target = self.target('cursor')
        initial = self.planner.plan(self.record['id'], target)
        destination = Path(initial.surface['path'])
        destination.mkdir(parents=True)
        (destination / 'keep.txt').write_text('external')
        result = self.planner.plan(self.record['id'], target)
        self.assertEqual(result.ownership, 'conflict')
        self.assertEqual(result.actions, [])
        self.assertEqual((destination / 'keep.txt').read_text(), 'external')

    def test_cursor_local_import_policy_blocks_actions(self):
        result = self.planner.plan(self.record['id'], replace(self.target('cursor'), mechanism_available=False))
        self.assertEqual(result.actions, [])
        self.assertEqual(result.support, 'manual')

    def test_upstream_candidate_never_applied(self):
        incoming = replace(self.resolution, source=replace(self.resolution.source, revision='b' * 40))
        self.store.record_refresh(self.record['id'], incoming)
        plan = self.planner.plan(self.record['id'], self.target('claude'))
        self.assertEqual(plan.source['revision'], 'a' * 40)
        self.assertEqual(plan.candidate_source['revision'], 'b' * 40)
        self.assertEqual(plan.actions, [])
        self.assertTrue(plan.requires_reconciliation)

    def test_missing_and_unavailable_artifacts_block(self):
        artifact = Path(self.record['artifactRoot'])
        shutil.rmtree(artifact)
        plan = self.planner.plan(self.record['id'], self.target('claude'))
        self.assertEqual(plan.artifact_state, 'missing')
        self.assertEqual(plan.actions, [])
        artifact.symlink_to(self.root / 'acquired')
        plan = self.planner.plan(self.record['id'], self.target('claude'))
        self.assertEqual(plan.artifact_state, 'unavailable')
        self.assertEqual(plan.actions, [])

    def test_offline_upstream_blocks_even_intact_artifact(self):
        self.store.record_refresh(self.record['id'], PackageResolution('unavailable', 'skill', reason='offline'))
        plan = self.planner.plan(self.record['id'], self.target('codex'))
        self.assertEqual(plan.artifact_state, 'current')
        self.assertEqual(plan.actions, [])
        self.assertIn('package-upstream-unavailable', plan.blockers)

    def test_unsupported_harness_is_manual_without_components(self):
        plan = self.planner.plan(self.record['id'], self.target('hermes'))
        self.assertEqual(plan.support, 'unsupported')
        self.assertEqual(plan.actions, [])

    def test_plans_rebuild_after_restart_without_persisting_deployment_state(self):
        before = self.store.manifest.read_bytes()
        first = self.planner.plan(self.record['id'], self.target('codex'))
        restarted = PackageDeploymentPlanner(ManagedPackageStore(self.root / 'managed'))
        second = restarted.plan(self.record['id'], self.target('codex'))
        self.assertEqual(asdict(first), asdict(second))
        self.assertEqual(before, self.store.manifest.read_bytes())
        self.assertFalse((self.root / 'native').exists())

    def test_isolation_contract_inherits_no_environment_or_files(self):
        root = self.root / 'sandbox'
        with patch.dict('os.environ', {'OPENCODE_CONFIG': '/real/config', 'SECRET_TOKEN': 'secret'}), \
                patch('subprocess.run', side_effect=AssertionError('No process allowed')):
            isolation = opencode_isolation(root)
        self.assertFalse(root.exists())
        self.assertNotIn('SECRET_TOKEN', isolation['environment'])
        self.assertNotIn('OPENCODE_CONFIG', isolation['environment'])
        self.assertTrue(all(Path(value).is_relative_to(root) for value in isolation['environment'].values()))
        self.assertFalse(isolation['runtimeVerified'])

    def test_invalid_native_config_does_not_prove_absence(self):
        config = self.root / 'opencode.jsonc'
        config.write_text('{"plugins":["https://token@untrusted.invalid/plugin"]}')
        with self.assertRaisesRegex(ValueError, 'manual reconciliation'):
            read_opencode_registrations((config,))

    def test_symlinked_native_parent_blocks_escaped_destination(self):
        target = self.target('cursor')
        target.root.mkdir(parents=True)
        outside = self.root / 'outside'
        outside.mkdir()
        (target.root / 'plugins').symlink_to(outside, target_is_directory=True)
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.actions, [])
        self.assertIn('unsafe-native-destination', plan.blockers)
        self.assertEqual(list(outside.iterdir()), [])

    def test_isolation_fixture_static_config_reads_stay_within_fixture(self):
        from skill_manager.harness.resolution import ResolutionContext
        from skill_manager.opencode.resolver import resolve_opencode_config
        root = self.root / 'sandbox'
        route = opencode_isolation(root)
        env = route['environment']
        fixture = Path(env['XDG_CONFIG_HOME']) / 'opencode/opencode.jsonc'
        fixture.parent.mkdir(parents=True)
        fixture.write_text('{"plugins": []}')
        context = ResolutionContext(env, 'linux', 'linux', Path(env['HOME']),
                                    Path(env['XDG_CONFIG_HOME']), Path(env['XDG_DATA_HOME']), Path(env['XDG_STATE_HOME']))
        before = fixture.read_bytes()
        result = resolve_opencode_config(context)
        self.assertTrue(all(item.path.is_relative_to(root) for item in result.sources))
        self.assertEqual(result.config['plugins'], [])
        self.assertEqual(fixture.read_bytes(), before)

    def test_standard_package_loads_natively_without_component_conversion(self):
        root = self.root / 'portable'
        root.mkdir()
        (root / 'plugin.json').write_text(json.dumps({
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'portable'}))
        resolution = PackageResolution('resolved', 'portable',
            source=replace(self.resolution.source, locator='github:example/portable'), artifact_root=root,
            capabilities=SourcePackageDiscovery().inspect_root(root)['package'])
        record = self.store.adopt(resolution, name='Portable', observations=[])
        for harness in ('cursor', 'codex'):
            plan = self.planner.plan(record['id'], self.target(harness))
            self.assertEqual(plan.support, 'supported', plan.blockers)
            self.assertTrue(all(action['unit'] == 'whole-package' for action in plan.actions))

    def test_cursor_unsupported_standard_variables_are_manual_not_rewritten(self):
        root = self.root / 'portable-paths'
        root.mkdir()
        (root / 'plugin.json').write_text(json.dumps({
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'portable'}))
        mcp = json.dumps({'mcpServers': {'example': {'command': '${PLUGIN_ROOT}/run'}}})
        (root / 'mcp.json').write_text(mcp)
        resolution = PackageResolution('resolved', 'portable',
            source=replace(self.resolution.source, locator='github:example/portable'), artifact_root=root,
            capabilities=SourcePackageDiscovery().inspect_root(root)['package'])
        record = self.store.adopt(resolution, name='Portable', observations=[])
        plan = self.planner.plan(record['id'], self.target('cursor'))
        self.assertEqual(plan.actions, [])
        self.assertIn('cursor-standard-path-variables-unsupported', plan.blockers)
        self.assertEqual((Path(record['artifactRoot']) / 'mcp.json').read_text(), mcp)

    def test_codex_marketplace_uses_supported_layout(self):
        plan = self.planner.plan(self.record['id'], self.target('codex'))
        self.assertTrue(plan.surface['marketplacePath'].endswith('/.agents/plugins/marketplace.json'))

    def test_matching_source_in_occupied_ids_remains_external(self):
        native = NativeRegistration('github:example/package:.', 'native-source', self.resolution.source)
        target = replace(self.target('opencode'), registrations=(native,), occupied_identifiers=(native.native_id,))
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.ownership, 'external-existing')
        self.assertEqual(plan.actions, [])

    def test_unpinned_registration_is_conflict_not_absent(self):
        config = self.root / 'opencode.json'
        config.write_text('{"plugins":["github:example/package"]}')
        target = replace(self.target('opencode'), registrations=read_opencode_registrations((config,)))
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.ownership, 'conflict')
        self.assertEqual(plan.actions, [])

    def test_disabled_but_registered_source_still_prevents_duplicate(self):
        config = self.root / 'opencode.json'
        config.write_text(json.dumps({'plugins': ['github:example/package#' + 'a' * 40, '-example']}))
        plan = self.planner.plan(self.record['id'], replace(self.target('opencode'),
                                 registrations=read_opencode_registrations((config,))))
        self.assertEqual(plan.ownership, 'external-existing')
        self.assertEqual(plan.actions, [])
        self.assertFalse(plan.runtime_verified)

    def test_regular_file_cannot_be_native_or_isolation_root(self):
        target = self.target('claude')
        target.root.parent.mkdir(parents=True)
        target.root.write_text('file')
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.actions, [])
        self.assertIn('unsafe-native-target-root', plan.blockers)
        with self.assertRaises(ValueError):
            opencode_isolation(target.root)

    def test_not_retained_source_is_not_installable(self):
        resolution = replace(self.resolution, source=replace(self.resolution.source, locator='github:example/missing'),
                             artifact_root=None)
        record = self.store.adopt(resolution, name='Missing', observations=[])
        plan = self.planner.plan(record['id'], self.target('claude'))
        self.assertEqual(plan.artifact_state, 'not-retained')
        self.assertEqual(plan.actions, [])

    def test_unreconciled_external_observation_blocks_duplicate(self):
        self.store.adopt(self.resolution, name='Example', observations=[{
            'harness': 'opencode', 'path': str(self.root / 'acquired'), 'ownership': 'external-existing'}])
        plan = self.planner.plan(self.record['id'], self.target('opencode'))
        self.assertEqual(plan.ownership, 'conflict')
        self.assertIn('external-observation-needs-native-reconciliation', plan.blockers)
        self.assertEqual(plan.actions, [])

    def test_two_explicit_harness_distributions_select_different_snapshots(self):
        cursor_source = replace(self.resolution.source, locator='github:example/cursor-distribution')
        cursor = self.store.adopt(replace(self.resolution, observation_ref='cursor', source=cursor_source),
                                  name='Example', observations=[])
        family = self.store.adopt(replace(self.resolution, observation_ref='family',
            source=replace(self.resolution.source, locator='github:example/family'), distributions=(
                DistributionRelationship(self.resolution.source, 'native distribution', 'explicit fixture', 'codex'),
                DistributionRelationship(cursor_source, 'native distribution', 'explicit fixture', 'cursor'),
            )), name='Example', observations=[])
        codex_plan = self.planner.plan(family['id'], self.target('codex'))
        cursor_plan = self.planner.plan(family['id'], self.target('cursor'))
        self.assertEqual(codex_plan.selected_package_id, self.record['id'])
        self.assertEqual(cursor_plan.selected_package_id, cursor['id'])
        self.assertEqual(codex_plan.strategy, 'native-install')
        self.assertEqual(cursor_plan.strategy, 'native-local')

    def test_unreadable_native_root_evidence_fails_closed(self):
        loop = self.root / 'loop'
        loop.symlink_to(loop)
        target = replace(self.target('claude'), registrations=(NativeRegistration('example', 'native-root', root=loop),))
        plan = self.planner.plan(self.record['id'], target)
        self.assertEqual(plan.ownership, 'conflict')
        self.assertEqual(plan.actions, [])
        self.assertIn('native-source-unreadable', plan.blockers)

    def test_disabled_only_control_cannot_prove_absence_or_source_identity(self):
        family = self.family('opencode')
        config = self.root / 'opencode.json'
        config.write_text('{"plugins":["-example"]}')
        target = replace(self.target('opencode'), registrations=read_opencode_registrations((config,)))
        plan = self.planner.plan(family['id'], target)
        self.assertEqual(plan.ownership, 'conflict')
        self.assertEqual(plan.actions, [])
        self.assertIn('native-controls-need-reconciliation', plan.blockers)

    def test_invalid_standard_manifest_is_not_hidden_by_valid_vendor_manifest(self):
        root = self.resolution.artifact_root
        (root / 'plugin.json').write_text('{"name":"invalid-standard"}')
        record = self.store.adopt(replace(self.resolution,
            source=replace(self.resolution.source, locator='github:example/mixed'),
            capabilities=SourcePackageDiscovery().inspect_root(root)['package']), name='Mixed', observations=[])
        plan = self.planner.plan(record['id'], self.target('codex'))
        self.assertEqual(plan.actions, [])
        self.assertIn('primary-standard-manifest-invalid', plan.blockers)
