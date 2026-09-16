#!/usr/bin/env python3
"""Opt-in source-to-native verification: fixture HTTP upstream, real offline CLIs.

Claude's initialization-only control protocol is documented by the official
claude-agent-sdk-python Query.initialize and SubprocessCLITransport sources.
No prompt, model turn, SDK installation or real-user configuration is used.
"""
import argparse
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.verify_native_package_sandbox import SandboxRunner, prepare, snapshot
from scripts.verify_native_package_lifecycle import fixture
from tests.support.app_harness import AppTestHarness
from tests.support.fake_home import seed_skill_package
from skill_manager.application.skills.native_package_cli import ClaudeNativePackageAdapter, CodexNativePackageAdapter, ReadOnlyNativePackageAdapter
from skill_manager.application.skills.package_deployment_service import PackageDeploymentService

NAME = 'task09-chain'
SOURCE = 'github:example/task09-chain'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--claude', required=True)
    parser.add_argument('--codex', required=True)
    parser.add_argument('--snapshot-root', action='append', required=True)
    args = parser.parse_args()
    before = snapshot(args.snapshot_root)
    evidence = {'nativeCommands': [], 'sourceRequests': [], 'stages': {}, 'modelTurns': 0,
                'upstream': 'bounded fixture HTTP responses; no live upstream download'}
    try:
        with TemporaryDirectory(prefix='skill-manager-native-test-task09-chain-') as directory:
            root = Path(directory)
            prepare(root)
            runner = SandboxRunner(args.image, {'claude':args.claude, 'codex':args.codex}, root)
            try:
                verify_chain(root, runner, evidence)
                evidence['result'] = 'PASS'
            finally:
                runner.release()
    finally:
        after = snapshot(args.snapshot_root)
        evidence.update(before=before, after=after, realOpenCodeUnchanged=before == after)
        print(json.dumps(evidence, indent=2))
    assert before == after, 'Snapshot roots changed during verification; investigate before accepting.'


def verify_chain(root, runner, evidence):
    current = {'revision':'a'}
    archives = {}
    for revision, version in [('a','1.0.0'), ('b','2.0.0'), ('c','3.0.0')]:
        upstream = root/'source'/revision
        fixture(upstream, NAME, version)
        (upstream/'uninterpreted.txt').write_text('Whole package supporting data ' + version)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for path in sorted(upstream.rglob('*')):
                if path.is_file():
                    archive.write(path, 'root/' + path.relative_to(upstream).as_posix())
        archives[revision] = stream.getvalue()

    def native(argv):
        result = runner(argv)
        evidence['nativeCommands'].append({'argv':list(argv), 'code':result.returncode})
        runner.release()
        assert result.returncode == 0, (argv, result.stderr)
        return result

    adapters = {
        'claude':ClaudeNativePackageAdapter(root/'claude', native, mechanism_available=True),
        'codex':CodexNativePackageAdapter(root/'codex', native, mechanism_available=True),
        'cursor':ReadOnlyNativePackageAdapter('cursor', root/'cursor'),
    }

    def seed(spec):
        observed = root/'source/observed'
        seed_skill_package(observed/'skills', 'fixture', 'Task09 observed Skill')
        (observed/'.codex-plugin').mkdir()
        (observed/'.codex-plugin/plugin.json').write_text(json.dumps({'name':NAME, 'repository':'https://github.com/example/task09-chain'}))
        config = spec.xdg_config_home/'opencode/opencode.jsonc'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({'skills':{'paths':[str(observed/'skills')]}, 'plugin':[SOURCE + '#' + 'a'*40]}))
        adapters['opencode'] = ReadOnlyNativePackageAdapter('opencode', config.parent)

    def public_bytes(url, **kwargs):
        evidence['sourceRequests'].append(url)
        if url.startswith('https://api.github.com/repos/example/task09-chain/commits/'):
            return json.dumps({'sha':current['revision']*40}).encode()
        if url.startswith('https://codeload.github.com/example/task09-chain/zip/'):
            return archives[current['revision']]
        raise AssertionError('Unexpected source request: ' + url)

    def claude_registry():
        reply = runner(['claude', '--output-format','stream-json','--verbose','--input-format','stream-json','--setting-sources=user'],
            requests=[{'type':'control_request','request_id':'task09-init','request':{'subtype':'initialize','hooks':None}}])[0]
        runner.release()
        assert reply['response']['subtype'] == 'success', reply
        body = reply['response']['response']
        assert body['account']['tokenSource'] == 'none'
        return {'skills':[c['name'] for c in body['commands'] if c['name'].startswith(NAME+':')],
                'agents':[a['name'] for a in body['agents'] if a['name'].startswith(NAME+':')],
                'session':body.get('session_state'), 'credentials':body['account']['tokenSource']}

    def codex_registry(record, *, removed=False):
        requests = [
            {'id':1,'method':'initialize','params':{'clientInfo':{'name':NAME,'version':'1'},'capabilities':{'experimentalApi':True}}},
            {'id':2,'method':'skills/list','params':{'cwds':[str(root/'workspace')],'forceReload':True}},
        ]
        if not removed:
            requests.append({'id':3,'method':'plugin/read','params':{'pluginName':NAME,
                'marketplacePath':str(Path(record['target'])/'.agents/plugins/marketplace.json')}})
        replies = runner(['codex','app-server'], requests=requests)
        runner.release()
        skills = [s for row in replies[1]['result']['data'] for s in row['skills'] if s.get('pluginId') == record['nativeId']]
        result = {'skills':[{'name':s['name'],'enabled':s['enabled']} for s in skills], 'enabledCount':sum(s['enabled'] for s in skills)}
        if not removed:
            plugin = replies[2]['result']['plugin']
            result.update(hooks=plugin['hooks'], version=plugin['summary']['localVersion'])
            assert plugin['hooks']
        return result

    evidence['versions'] = {h:native([h,'--version']).stdout.strip() for h in ('claude','codex')}
    evidence['stages']['before'] = {'claude':claude_registry()}
    assert not evidence['stages']['before']['claude']['skills']
    assert not evidence['stages']['before']['claude']['agents']
    with AppTestHarness(fixture_factory=seed, native_package_adapter_factory=lambda h:adapters[h]) as app:
        observed_before = snapshot([root/'source/observed', adapters['opencode'].root])
        component_roots = [app.container.skills_store.root, app.container.paths.slash_command_store_root]
        component_before = snapshot(component_roots)
        component_files = [app.container.paths.skills_store_manifest, app.container.paths.mcp_store_manifest,
                           app.container.paths.slash_command_sync_state_path]
        component_bytes = [p.read_bytes() if p.exists() else None for p in component_files]
        with patch('skill_manager.sources.github.read_public_bytes', side_effect=public_bytes):
            ref = app.get_json('/api/skills')['rows'][0]['skillRef']
            context = app.get_json(f'/api/skills/{ref}/package-context')
            assert context['packageBacked'] and len(context['observation']['package']['manifests']) == 1
            assert evidence['sourceRequests'] == []
            resolved = app.post_json(f'/api/skills/{ref}/resolve-package')
            assert resolved['resolution']['status'] == 'resolved'
            first = app.post_json(f'/api/skills/{ref}/manage-package')
            assert first['source']['revision'] == 'a'*40 and len(first['capabilities']['manifests']) == 3
            assert (Path(first['artifactRoot'])/'uninterpreted.txt').is_file()
            evidence['sourceToManaged'] = {'localManifestCount':1, 'managedManifestCount':3,
                'firstId':first['id'], 'source':first['source'], 'artifactState':first['artifactState']}

            def action(package_id, harness, operation, **body):
                return app.post_json(f'/api/skills/managed-packages/{package_id}/deployments/{harness}', {'action':operation, **body})

            def record(harness):
                return next(r for r in app.container.skills_mutations.package_deployments.list_deployments().values() if r['harness']==harness)

            for harness in ('claude','codex'):
                action(first['id'], harness, 'deploy')
                action(first['id'], harness, 'deploy')
            evidence['stages']['enabled'] = {'claude':claude_registry(), 'codex':codex_registry(record('codex'))}
            assert evidence['stages']['enabled']['claude']['skills'] == [NAME+':fixture']
            assert evidence['stages']['enabled']['claude']['agents'] == [NAME+':fixture-agent']
            assert evidence['stages']['enabled']['codex']['enabledCount'] == 1

            for harness in ('claude','codex'):
                action(first['id'], harness, 'disable')
            evidence['stages']['disabled'] = {'claude':claude_registry(), 'codex':codex_registry(record('codex'))}
            assert not evidence['stages']['disabled']['claude']['skills'] and not evidence['stages']['disabled']['claude']['agents']
            assert evidence['stages']['disabled']['codex']['enabledCount'] == 0

            current['revision'] = 'b'
            second = app.post_json(f'/api/skills/{ref}/manage-package')
            for harness in ('claude','codex'):
                view = action(first['id'], harness, 'update', replacementPackageId=second['id'])
                assert view['packageId']==second['id']
                assert next(h for h in view['harnesses'] if h['harness']==harness)['state']=='disabled'
                action(second['id'], harness, 'enable')
            evidence['sourceToManaged']['secondId'] = second['id']
            evidence['stages']['updated-enabled'] = {'claude':claude_registry(), 'codex':codex_registry(record('codex'))}
            assert evidence['stages']['updated-enabled']['claude']['skills']==[NAME+':fixture']
            assert evidence['stages']['updated-enabled']['claude']['agents']==[NAME+':fixture-agent']
            assert evidence['stages']['updated-enabled']['codex']['version']=='2.0.0'
            service = PackageDeploymentService(app.container.skills_queries.managed_packages)
            assert all(service.reconcile(record(h)['deploymentId'], adapters[h])['state']=='managed' for h in ('claude','codex'))

            action(second['id'], 'claude', 'remove')
            removed_claude = claude_registry()
            assert not removed_claude['skills'] and not removed_claude['agents']
            codex_record = record('codex')
            assert codex_registry(codex_record)['enabledCount']==1
            action(second['id'], 'codex', 'remove')
            assert not codex_registry(codex_record, removed=True)['skills']
            evidence['stages']['removed'] = {'claude':removed_claude, 'activeDeployments':service.list_deployments(), 'centralPackages':len(app.get_json('/api/skills/managed-packages')['packages'])}
            assert evidence['stages']['removed']['centralPackages']==2
            for harness in ('claude','codex'):
                action(second['id'], harness, 'deploy')
            evidence['stages']['redeployed'] = {'claude':claude_registry(), 'codex':codex_registry(record('codex'))}
            assert evidence['stages']['redeployed']['claude']['skills']==[NAME+':fixture']
            assert evidence['stages']['redeployed']['claude']['agents']==[NAME+':fixture-agent']
            assert evidence['stages']['redeployed']['codex']['enabledCount']==1

            current['revision'] = 'c'
            refreshed = app.post_json(f'/api/skills/managed-packages/{second["id"]}/refresh')
            assert refreshed['candidateSource'] and refreshed['upstreamState']=='changed'
            view = app.get_json(f'/api/skills/managed-packages/{second["id"]}/deployments')
            assert all(not h['actions'] for h in view['harnesses'])
            count = len(evidence['nativeCommands'])
            app.post_json(f'/api/skills/managed-packages/{second["id"]}/deployments/codex', {'action':'remove'}, expected_status=409)
            assert all(c['argv'][1:3]==['plugin','list'] or c['argv'][1:4]==['plugin','marketplace','list'] for c in evidence['nativeCommands'][count:])
            evidence['stages']['candidate-blocked'] = {'upstream':refreshed['upstreamState'], 'states':{h['harness']:h['state'] for h in view['harnesses']}, 'mutationRefused':True}
            assert observed_before == snapshot([root/'source/observed', adapters['opencode'].root])
            assert component_before == snapshot(component_roots)
            assert component_bytes == [p.read_bytes() if p.exists() else None for p in component_files]
            evidence['externalObservationUnchanged'] = True
            evidence['noDecomposition'] = {'standaloneSkillMcpCommandStateUnchanged':True, 'nativePackages':NAME}


if __name__ == '__main__':
    main()
