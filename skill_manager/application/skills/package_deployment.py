"""Read-only whole-package preflight. Task05 remains the only package authority."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Literal
from urllib.parse import unquote, urlsplit

from skill_manager.jsonc import strip_jsonc
from .managed_packages import ManagedPackageStore
from .package_resolution import PackageSource


EVIDENCE_DATE = '2026-09-15'
EVIDENCE = {
    'claude': 'https://code.claude.com/docs/en/plugins-reference#skills-directory-plugins',
    'cursor': 'https://cursor.com/docs/plugins#test-plugins-locally',
    'codex': 'https://github.com/openai/codex/blob/a8964cb1bad67bc26a826fb07d1bef99c6a3f008/codex-rs/cli/src/plugin_cmd.rs',
    'opencode': 'https://opencode.ai/v2/docs/plugins',
}


@dataclass(frozen=True)
class NativeRegistration:
    native_id: str
    evidence: Literal['native-source', 'native-root', 'name-only', 'native-control'] = 'name-only'
    source: PackageSource | None = None
    root: Path | None = None


@dataclass(frozen=True)
class NativeTarget:
    harness: str
    # User configuration home for this harness; callers supply the actual target.
    root: Path
    registrations: tuple[NativeRegistration, ...] = ()
    inventory_complete: bool = False
    # Includes installed-version support and local-import/enterprise policy.
    mechanism_available: bool | None = None
    occupied_identifiers: tuple[str, ...] = ()


@dataclass
class PackageDeploymentPlan:
    managed_package_id: str
    target_harness: str
    package_state: dict
    selected_package_id: str | None = None
    selected_distribution: dict | None = None
    source: dict | None = None
    artifact_state: str | None = None
    upstream_state: str | None = None
    fingerprint: str | None = None
    candidate_source: dict | None = None
    strategy: Literal['native-local', 'native-install', 'manual/unsupported'] = 'manual/unsupported'
    ownership: Literal['absent', 'external-existing', 'conflict'] = 'absent'
    support: Literal['supported', 'manual', 'unsupported'] = 'manual'
    surface: dict = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    rationale: str = ''
    requires_reconciliation: bool = False
    runtime_verified: bool = False


def _same_source(left: dict, right: dict) -> bool:
    if left.get('kind') != right.get('kind') or left.get('locator', '').casefold() != right.get('locator', '').casefold():
        return False
    if (left.get('package_path') or '.') != (right.get('package_path') or '.'):
        return False
    if left.get('revision'):
        return left['revision'] == right.get('revision')
    return left.get('kind') == 'npm' and bool(left.get('version')) and left['version'] == right.get('version')


def _state(record: dict) -> dict:
    return {key: deepcopy(record.get(key)) for key in
            ('artifactState', 'upstreamState', 'fingerprint', 'candidateSource', 'resolutionStatus')}


def _source_identifier(source: dict) -> str:
    if source['kind'] == 'npm':
        return source['locator'].rsplit('@', 1)[0] if source.get('version') else source['locator']
    return source['locator'] + ':' + (source.get('package_path') or '.')


def _safe_directory_target(root: Path) -> bool:
    return root.is_absolute() and root != Path(root.anchor) and not any(
        p.is_symlink() or (p.exists() and not p.is_dir()) for p in (root, *root.parents))


def _manifest_name(record: dict, relative: str) -> str:
    root = Path(record['artifactRoot'])
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 1024 * 1024:
        raise ValueError('Unsafe native manifest')
    data = json.loads(path.read_text(encoding='utf-8'))
    name = data.get('name') if isinstance(data, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', name):
        raise ValueError('Unsupported native identifier')
    return name


class PackageDeploymentPlanner:
    def __init__(self, packages: ManagedPackageStore):
        self.packages = packages

    def plan(self, package_id: str, target: NativeTarget) -> PackageDeploymentPlan:
        parent = self.packages.get(package_id)
        plan = PackageDeploymentPlan(package_id, target.harness, _state(parent))
        plan.requires_reconciliation = (parent['artifactState'] != 'current' or parent['upstreamState'] != 'current'
                                        or parent['candidateSource'] is not None)
        if target.harness not in EVIDENCE:
            plan.support = 'unsupported'
            plan.blockers.append('unsupported-harness')
            return plan
        plan.evidence = [EVIDENCE[target.harness], 'verified ' + EVIDENCE_DATE]
        if not _safe_directory_target(target.root):
            plan.blockers.append('unsafe-native-target-root')
            return plan
        relationships = [item for item in parent['distributions'] if item.get('harness') == target.harness]
        selected = parent
        if relationships:
            if len(relationships) != 1 or not relationships[0].get('evidence'):
                plan.blockers.append('ambiguous-distribution')
                return plan
            plan.selected_distribution = deepcopy(relationships[0])
            matches = [record for record in self.packages.list()
                       if _same_source(relationships[0]['source'], record['source'])]
            if len(matches) != 1:
                plan.blockers.append('distribution-not-uniquely-managed')
                return plan
            selected = matches[0]
        plan.selected_package_id = selected['id']
        plan.source = deepcopy(selected['source'])
        plan.artifact_state = selected['artifactState']
        plan.upstream_state = selected['upstreamState']
        plan.fingerprint = selected['fingerprint']
        plan.candidate_source = deepcopy(selected['candidateSource'])
        for label, record in (('package', parent), ('distribution', selected)):
            if label == 'distribution' and selected['id'] == parent['id']:
                continue
            if record['artifactState'] != 'current':
                plan.blockers.append(label + '-artifact-' + record['artifactState'])
            if record['upstreamState'] != 'current':
                plan.blockers.append(label + '-upstream-' + record['upstreamState'])
            if record['resolutionStatus'] != 'resolved':
                plan.blockers.append(label + '-source-' + record['resolutionStatus'])
            if record['candidateSource'] is not None:
                plan.blockers.append(label + '-candidate-source-pending')
        plan.requires_reconciliation = bool(plan.blockers)
        if not target.inventory_complete:
            plan.blockers.append('native-inventory-incomplete')
            plan.ownership = 'conflict'
        if target.mechanism_available is not True:
            plan.blockers.append('native-version-or-policy-unverified')
        if any(item.evidence == 'native-control' for item in target.registrations):
            plan.ownership = 'conflict'
            plan.blockers.append('native-controls-need-reconciliation')

        try:
            matches = [item for item in target.registrations if self._matches(item, selected)]
        except (OSError, ValueError, RuntimeError):
            plan.ownership = 'conflict'
            plan.requires_reconciliation = True
            plan.blockers.append('native-source-unreadable')
            return plan
        if matches:
            plan.ownership = 'external-existing'
            plan.evidence.extend(item.evidence for item in matches)
        elif any(item.evidence == 'native-source' and item.source is not None
                 and _source_identifier(asdict(item.source)).casefold() == _source_identifier(selected['source']).casefold()
                 for item in target.registrations):
            plan.ownership = 'conflict'
            plan.blockers.append('native-source-version-unreconciled')
        elif any(item.get('harness') == target.harness and item.get('state') == 'present'
                 for record in (parent, selected) for item in record['observations']):
            plan.ownership = 'conflict'
            plan.blockers.append('external-observation-needs-native-reconciliation')

        try:
            self._strategy(plan, selected, target, native_intent=bool(relationships or matches))
        except (OSError, ValueError, RuntimeError):
            plan.strategy = 'manual/unsupported'
            plan.blockers.append('native-format-unreadable')
        native_id = plan.surface.get('nativeId')
        if (native_id and native_id in target.occupied_identifiers
            and not any(item.native_id == native_id for item in matches)) or any(
            item.native_id == native_id and item not in matches for item in target.registrations
        ):
            plan.ownership = 'conflict'
            plan.blockers.append('native-identifier-conflict')
        destination = plan.surface.get('path')
        for value in (destination, plan.surface.get('configPath')):
            if value and (any(p.is_symlink() for p in (Path(value), *Path(value).parents))
                          or not Path(value).resolve().is_relative_to(target.root.resolve())):
                plan.ownership = 'conflict'
                plan.blockers.append('unsafe-native-destination')
        if destination and (Path(destination).exists() or Path(destination).is_symlink()) and not matches:
            plan.ownership = 'conflict'
            plan.blockers.append('native-destination-occupied')
        if plan.ownership == 'external-existing':
            plan.blockers.append('external-existing-no-takeover')
        if plan.blockers or plan.ownership != 'absent':
            plan.actions.clear()
        plan.requires_reconciliation |= plan.ownership == 'conflict'
        plan.support = 'supported' if not plan.blockers else 'manual'
        return plan

    @staticmethod
    def _matches(registration: NativeRegistration, selected: dict) -> bool:
        if registration.evidence == 'native-source' and registration.source is not None:
            return _same_source(asdict(registration.source), selected['source'])
        if registration.evidence == 'native-root' and registration.root and selected['artifactRoot']:
            return registration.root.resolve() == Path(selected['artifactRoot']).resolve()
        return False

    @staticmethod
    def _strategy(plan, record, target, *, native_intent):
        manifests = (record['capabilities'] or {}).get('manifests', [])
        paths = {item['path'] for item in manifests
                 if item['evidence'] != 'unresolved'}
        harness = target.harness
        if harness in ('codex', 'cursor') and any(item['path'] == 'plugin.json' for item in manifests) and 'plugin.json' not in paths:
            plan.blockers.append('primary-standard-manifest-invalid')
            return
        suffix = record['id'][:16]
        manifest = f'.{harness}-plugin/plugin.json'
        if harness == 'cursor' and manifest not in paths:
            manifest = 'plugin.json'
        if harness == 'codex' and 'plugin.json' in paths:
            manifest = 'plugin.json'  # current Codex loader gives the standard manifest precedence
        if harness != 'opencode':
            if manifest not in paths:
                plan.blockers.append('no-proven-native-format')
                return
            if record['artifactState'] != 'current':
                return
            name = _manifest_name(record, manifest)
            if harness == 'cursor' and manifest != 'plugin.json' and 'plugin.json' in paths:
                if _manifest_name(record, 'plugin.json') != name:
                    plan.blockers.append('ambiguous-native-manifest-identity')
                    return
            if harness == 'cursor' and manifest == 'plugin.json':
                mcp = Path(record['artifactRoot']) / 'mcp.json'
                if mcp.exists():
                    if mcp.is_symlink() or not mcp.is_file() or mcp.stat().st_size > 1024 * 1024:
                        raise ValueError('Unsafe standard MCP declaration')
                    text = mcp.read_text(encoding='utf-8')
                    if '${PLUGIN_ROOT}' in text or '${PLUGIN_DATA}' in text:
                        plan.blockers.append('cursor-standard-path-variables-unsupported')
                        return
            if harness in ('claude', 'cursor'):
                base = target.root / ('skills' if harness == 'claude' else 'plugins/local')
                destination = base / ('skill-manager-' + suffix)
                plan.strategy = 'native-local'
                plan.surface = {'path': str(destination), 'nativeId': name + ('@skills-dir' if harness == 'claude' else ''),
                                'scope': 'user', 'loader': harness + '-whole-plugin-directory'}
                plan.actions = [{'action': 'place-whole-package', 'unit': 'whole-package',
                                 'managedPackageId': record['id'], 'destination': str(destination)}]
                plan.rationale = 'Native directory discovery; copy the whole snapshot, never link individual components.'
            else:
                marketplace = 'skill-manager-' + suffix
                marketplace_root = target.root / 'skill-manager-marketplaces' / marketplace
                catalog = marketplace_root / '.agents/plugins/marketplace.json'
                plan.strategy = 'native-install'
                plan.surface = {'path': str(marketplace_root), 'nativeId': name + '@' + marketplace,
                                'marketplaceId': marketplace, 'marketplacePath': str(catalog), 'scope': 'user',
                                'loader': 'codex-local-marketplace',
                                'marketplaceArgv': ['codex', 'plugin', 'marketplace', 'add', str(marketplace_root)],
                                'marketplaceDocument': {'name': marketplace, 'plugins': [
                                    {'name': name, 'source': {'source': 'local', 'path': './plugins/' + suffix}}]},
                                'wholePackagePath': str(marketplace_root / 'plugins' / suffix),
                                'installArgv': ['codex', 'plugin', 'add', name + '@' + marketplace, '--json']}
                if marketplace in target.occupied_identifiers:
                    plan.blockers.append('marketplace-identifier-conflict')
                plan.actions = [
                    {'action': 'register-whole-package-marketplace', 'unit': 'whole-package',
                     'managedPackageId': record['id'], 'marketplacePath': str(catalog)},
                    {'action': 'native-install-whole-package', 'unit': 'whole-package',
                     'managedPackageId': record['id'], 'nativeId': name + '@' + marketplace},
                ]
                plan.rationale = 'Codex installs an authored package from a local native marketplace into its own cache.'
            return
        # A main field, package name or SDK dependency alone is not OpenCode native proof.
        if not native_intent:
            plan.blockers.append('opencode-native-intent-unproven')
            return
        source = record['source']
        if source['kind'] == 'npm' and source.get('version'):
            spec = source['locator'].removeprefix('npm:')
        elif source['kind'] == 'github' and re.fullmatch('[0-9a-f]{40}', source.get('revision') or ''):
            spec = source['locator'] + '#' + source['revision']
            if source.get('package_path') not in (None, '.'):
                spec += '::path:' + source['package_path']
        else:
            plan.blockers.append('native-install-source-not-pinned')
            return
        plan.strategy = 'native-install'
        plan.surface = {'nativeId': _source_identifier(source), 'configPath': str(target.root / 'opencode.jsonc'),
                        'configKey': 'plugins', 'packageSpec': spec, 'scope': 'user',
                        'loader': 'opencode-v2-package-registration'}
        plan.actions = [{'action': 'native-register-whole-package', 'unit': 'whole-package',
                         'managedPackageId': record['id'], 'packageSpec': spec}]
        plan.rationale = 'Explicit proven OpenCode distribution; pinned native package registration, with a native cache copy.'


def opencode_isolation(root: Path) -> dict:
    """Describe a fresh process/container environment; creates nothing and inherits nothing."""
    if not _safe_directory_target(root):
        raise ValueError('An absolute isolated root without symlinks is required')
    return {
        'environment': {
            'HOME': str(root / 'home'), 'OPENCODE_TEST_HOME': str(root / 'home'),
            'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
            'XDG_CACHE_HOME': str(root / 'cache'), 'XDG_STATE_HOME': str(root / 'state'),
            'TMPDIR': str(root / 'tmp'), 'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'),
            'OPENCODE_CONFIG_DIR': str(root / 'config/opencode'),
        },
        'workingDirectory': str(root / 'workspace'),
        'requirements': [
            'Fresh disposable filesystem/user namespace; no host home, credentials or project config mounts.',
            'Only an explicitly approved whole-package fixture mounted read-only; native writable copies stay inside sandbox.',
            'No inherited environment; supply a verified binary PATH inside the sandbox before starting a process.',
            'No network for initial isolation checks; separately approve any native dependency downloads.',
        ],
        'runtimeVerified': False,
    }


def read_opencode_registrations(paths: tuple[Path, ...]) -> tuple[NativeRegistration, ...]:
    """Read supplied config documents only. This is NOT a complete runtime inventory."""
    registrations = []
    for path in paths:
        if not path.exists():
            if path.is_symlink():
                raise ValueError('Unreadable native config')
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('Unsafe native config')
        try:
            data = json.loads(strip_jsonc(path.read_text(encoding='utf-8')))
        except (ValueError, UnicodeError) as error:
            raise ValueError('Invalid native config') from error
        if not isinstance(data, dict):
            raise ValueError('Invalid native config')
        # Observe both generations, but never pretend their loading semantics match.
        for key in ('plugin', 'plugins'):
            items = data.get(key, [])
            if not isinstance(items, list):
                raise ValueError('Invalid native plugin list')
            for item in items:
                spec = item.get('package') if isinstance(item, dict) else item
                if isinstance(spec, list) and spec:
                    spec = spec[0]  # legacy [package, options]
                if not isinstance(spec, str):
                    raise ValueError('Invalid native plugin entry')
                if spec.startswith('-') or spec == '*':
                    # Controls address runtime IDs/patterns, not authoritative package coordinates.
                    registrations.append(NativeRegistration('unreconciled-control', 'native-control'))
                    continue
                if spec.startswith(('./', '../', '/', 'file://')):
                    value = urlsplit(spec) if spec.startswith('file://') else None
                    if value and (value.netloc or value.query or value.fragment):
                        raise ValueError('Unsupported native file URL')
                    root = Path(unquote(value.path) if value else spec)
                    if not root.is_absolute():
                        root = path.parent / root
                    registrations.append(NativeRegistration('local-source', 'native-root', root=root.resolve()))
                    continue
                git = re.fullmatch(r'github:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:#([a-f0-9]{40}))?(?:::path:([^?#]+))?', spec)
                npm = re.fullmatch(r'((?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+)(?:@(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?))?', spec)
                if git:
                    relative = git[3] or '.'
                    if '..' in Path(relative).parts or relative.startswith('/') or '\\' in relative:
                        raise ValueError('Unsafe native package subdirectory')
                    source = PackageSource('github', 'github:' + git[1], revision=git[2], package_path=relative)
                elif npm:
                    source = PackageSource('npm', 'npm:' + npm[1] + ('@' + npm[2] if npm[2] else ''), version=npm[2])
                else:
                    # Unknown specs cannot prove absence; do not return possible embedded credentials.
                    raise ValueError('Native plugin source needs manual reconciliation')
                registrations.append(NativeRegistration(_source_identifier(asdict(source)), 'native-source', source))
    return tuple(registrations)
