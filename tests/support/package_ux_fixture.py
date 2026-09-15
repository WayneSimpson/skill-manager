"""Isolated whole-package UX fixture; native process responses alone are simulated."""
import json
import tomllib
from pathlib import Path

from skill_manager.application.skills.native_package_cli import (
    ClaudeNativePackageAdapter, CodexNativePackageAdapter, ReadOnlyNativePackageAdapter,
)
from skill_manager.application.skills.package_resolution import PackageResolution, PackageSource
from skill_manager.application.skills.source_package import SourcePackageDiscovery
from tests.integration.test_native_package_service import NativeProcess


class CodexProcess(NativeProcess):
    def __call__(self, argv):
        if argv[1:3] == ['plugin', 'list']:
            config = self.root / 'config.toml'
            enabled = tomllib.loads(config.read_text()).get('plugins', {}) if config.exists() else {}
            for key, row in self.installed.items():
                row['enabled'] = enabled.get(key, {}).get('enabled', True)
        result = super().__call__(argv)
        if argv[1:3] == ['plugin', 'add']:
            (self.root / 'config.toml').write_text('\n'.join(
                f'[plugins."{key}"]\nenabled = true\n' for key in self.installed))
        return result


class ClaudeProcess:
    def __init__(self, root):
        self.root, self.disabled = root, set()
        self.mutations = []

    def __call__(self, argv):
        if argv[1:3] == ['plugin', 'list']:
            rows = []
            for manifest in self.root.glob('skills/*/.claude-plugin/plugin.json'):
                data = json.loads(manifest.read_text())
                native_id = data['name'] + '@skills-dir'
                rows.append({'id': native_id, 'scope': 'user', 'version': data['version'],
                             'enabled': native_id not in self.disabled, 'installPath': str(manifest.parent.parent)})
            return rows
        self.mutations.append(argv)
        if argv[1:3] == ['plugin', 'disable']:
            self.disabled.add(argv[3])
        elif argv[1:3] == ['plugin', 'enable']:
            self.disabled.discard(argv[3])
        else:
            raise AssertionError(argv)
        return {}


class PackageUXFixture:
    def __init__(self, root):
        self.root = root
        self.claude = ClaudeProcess(root / 'native/claude')
        self.codex = CodexProcess(root / 'native/codex')
        self.adapters = {
            'claude': ClaudeNativePackageAdapter(self.claude.root, self.claude, mechanism_available=True),
            'codex': CodexNativePackageAdapter(self.codex.root, self.codex, mechanism_available=True),
            'cursor': ReadOnlyNativePackageAdapter('cursor', root / 'native/cursor'),
            'opencode': ReadOnlyNativePackageAdapter('opencode', root / 'native/opencode'),
        }

    def __call__(self, harness):
        return self.adapters[harness]

    def resolution(self, revision='a', version='1.0.0', observation='package-one', *, locator='github:example/fixture'):
        root = self.root / ('source-' + revision)
        root.mkdir(parents=True, exist_ok=True)
        for harness in ('claude', 'codex'):
            directory = root / f'.{harness}-plugin'
            directory.mkdir(exist_ok=True)
            (directory / 'plugin.json').write_text(json.dumps({'name': 'fixture', 'version': version}))
        (root / 'skills/one').mkdir(parents=True, exist_ok=True)
        (root / 'skills/one/SKILL.md').write_text('---\nname: Package One\ndescription: Package skill\n---\n' + version)
        (root / 'hooks').mkdir(exist_ok=True)
        (root / 'hooks/hooks.json').write_text('{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"true"}]}]}}')
        (root / 'agents').mkdir(exist_ok=True)
        (root / 'agents/helper.md').write_text('---\nname: helper\ndescription: Helper\n---\nHelper')
        return PackageResolution('resolved', observation,
            source=PackageSource('github', locator, revision=revision * 40), artifact_root=root,
            capabilities=SourcePackageDiscovery().inspect_root(root)['package'])

    def adopt(self, store, revision='a', version='1.0.0', *, locator='github:example/fixture'):
        return store.adopt(self.resolution(revision, version, revision, locator=locator), name='Package One', observations=[])
