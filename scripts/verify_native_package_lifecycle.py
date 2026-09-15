#!/usr/bin/env python3
"""Opt-in real native lifecycle assertions in disposable offline test state."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_native_package_sandbox import SandboxRunner, prepare, snapshot
from skill_manager.application.skills.managed_packages import ManagedPackageStore, package_fingerprint
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from skill_manager.application.skills.package_deployment_service import PackageDeploymentService, PackageDeploymentError
from skill_manager.application.skills.native_package_cli import ClaudeNativePackageAdapter, CodexNativePackageAdapter, ReadOnlyNativePackageAdapter


def fixture(path, name, version):
    for harness in ('claude', 'codex', 'cursor'):
        directory = path / f'.{harness}-plugin'
        directory.mkdir(parents=True)
        (directory / 'plugin.json').write_text(json.dumps({'name': name, 'version': version}))
    (path / 'skills/fixture').mkdir(parents=True)
    (path / 'skills/fixture/SKILL.md').write_text('---\nname: fixture\ndescription: Safe offline fixture ' + version + '\n---\nReturn fixture-ok.\n')
    (path / 'agents').mkdir()
    (path / 'agents/fixture.md').write_text('---\nname: fixture-agent\ndescription: Safe offline agent\n---\nReturn agent-ok.\n')
    (path / 'hooks').mkdir()
    (path / 'hooks/hooks.json').write_text('{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"/usr/bin/true"}]}]}}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    for harness in ('claude', 'codex', 'opencode'):
        parser.add_argument('--' + harness, required=True)
    parser.add_argument('--snapshot-root', action='append', required=True)
    args = parser.parse_args()
    before = snapshot(args.snapshot_root)
    evidence = {}
    try:
        with TemporaryDirectory(prefix='skill-manager-native-test-') as directory:
            root = Path(directory)
            prepare(root)
            runner = SandboxRunner(args.image, {h: getattr(args, h) for h in ('claude','codex','opencode')}, root)
            def run(argv):
                result = runner(argv)
                runner.release()
                assert result.returncode == 0, (argv, result.stderr)
                return result
            try:
                for harness in runner.binaries:
                    evidence[harness] = {'version': run([harness, '--version']).stdout.strip()}
                packages = ManagedPackageStore(root / 'managed')
                records = []
                for revision, version in (('a', '1.0.0'), ('b', '2.0.0')):
                    source = root / ('source-' + revision)
                    fixture(source, 'skill-manager-task07-fixture', version)
                    records.append(packages.adopt(PackageResolution('resolved', revision,
                        source=PackageSource('github', 'github:skill-manager-fixtures/whole-package', revision=revision*40),
                        artifact_root=source, capabilities=SourcePackageDiscovery().inspect_root(source)['package']), name='Fixture', observations=[]))
                central = packages.manifest.read_bytes()

                # Existing native fixtures are created outside Skill Manager ownership.
                external_claude = root / 'claude/skills/external-task07'
                fixture(external_claude, 'external-task07', '1.0.0')
                external_market = root / 'codex/external-market'
                external_codex = external_market / 'plugins/external'
                fixture(external_codex, 'external-task07', '1.0.0')
                catalog = external_market / '.agents/plugins/marketplace.json'
                catalog.parent.mkdir(parents=True)
                catalog.write_text(json.dumps({'name':'external-task07', 'plugins':[{'name':'external-task07',
                    'source':{'source':'local', 'path':'./plugins/external'}}]}))
                run(['codex','plugin','marketplace','add',str(external_market),'--json'])
                run(['codex','plugin','add','external-task07@external-task07','--json'])
                external_cache = root / 'codex/plugins/cache/external-task07'
                external_before = [package_fingerprint(p) for p in (external_claude, external_market, external_cache)]
                adapters = {'claude':ClaudeNativePackageAdapter(root/'claude', run, mechanism_available=True),
                            'codex':CodexNativePackageAdapter(root/'codex', run, mechanism_available=True)}
                service = PackageDeploymentService(packages)
                deployments = {h: service.deploy(records[0]['id'], a)['deployment'] for h, a in adapters.items()}
                for harness, adapter in adapters.items():
                    record = deployments[harness]
                    assert not service.deploy(records[0]['id'], adapter)['changed']
                    assert service.disable(record['deploymentId'], adapter)['deployment']['enabled'] is False
                    record = service.update(record['deploymentId'], records[1]['id'], adapter)['deployment']
                    assert record['enabled'] is False
                    assert service.enable(record['deploymentId'], adapter)['deployment']['enabled'] is True
                    service = PackageDeploymentService(ManagedPackageStore(packages.root))
                    assert service.reconcile(record['deploymentId'], adapter)['state'] == 'managed'
                    if harness == 'claude':
                        details = run(['claude','plugin','details',record['nativeId']]).stdout
                        assert all(text in details for text in ('Skills (1)', 'Agents (1)', 'Hooks (1)', '2.0.0'))
                        evidence[harness]['components'] = 'Skill, agent and SessionStart hook reported by native details'
                    else:
                        replies = runner(['codex','app-server'], requests=[
                            {'id':1,'method':'initialize','params':{'clientInfo':{'name':'skill-manager-task07-fixture','version':'1'},'capabilities':{'experimentalApi':True}}},
                            {'id':2,'method':'skills/list','params':{'cwds':[str(root/'workspace')],'forceReload':True}},
                            {'id':3,'method':'plugin/read','params':{'pluginName':'skill-manager-task07-fixture','marketplacePath':str(Path(record['target'])/'.agents/plugins/marketplace.json')}}])
                        runner.release()
                        skills = replies[1]['result']['data'][0]['skills']
                        assert any(s['pluginId'] == record['nativeId'] and s['enabled'] for s in skills)
                        plugin = replies[2]['result']['plugin']
                        assert plugin['summary']['localVersion'] == '2.0.0' and plugin['skills'] and plugin['hooks']
                        evidence[harness]['components'] = 'Updated namespaced Skill and hook reported by native skills/list and plugin/read'
                    assert service.remove(record['deploymentId'], adapter)['changed']
                    assert not service.remove(record['deploymentId'], adapter)['changed']
                    evidence[harness]['lifecycle'] = 'deploy/repeat/disable/update-disabled/enable/restart/remove/repeat-remove passed'
                    assert [package_fingerprint(p) for p in (external_claude, external_market, external_cache)] == external_before
                assert service.list_deployments() == {}
                assert packages.manifest.read_bytes() == central
                for harness, path in [('cursor', root/'cursor'), ('opencode', root/'config/opencode')]:
                    adapter = ReadOnlyNativePackageAdapter(harness, path)
                    try:
                        service.deploy(records[0]['id'], adapter)
                    except PackageDeploymentError:
                        pass
                    else:
                        raise AssertionError('Unverified route mutated')
                    evidence.setdefault(harness, {})['deployment'] = 'manual: native compatibility/inventory not verified'
                evidence['external_native_fixtures_unchanged'] = True
                evidence['task05_manifest_unchanged'] = True
            finally:
                runner.release()
    finally:
        after = snapshot(args.snapshot_root)
        evidence.update(real_opencode_unchanged=before == after, before=before, after=after)
        print(json.dumps(evidence, indent=2))
        assert before == after, 'Real OpenCode state changed'


if __name__ == '__main__':
    main()
