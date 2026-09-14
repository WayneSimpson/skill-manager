"""Resolve observed skills to upstream source; no ownership or deployment state."""
from __future__ import annotations

import base64
import configparser
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
from tempfile import mkdtemp
from typing import Literal
from urllib.parse import quote, urlsplit

from skill_manager.sources.artifacts import (
    extract_source, public_url, read_public_bytes, read_public_json, relative_path,
)
from skill_manager.sources import github_repo_from_locator
from skill_manager.sources.github import matching_skill_roots
from .inventory import InventoryEntry
from .package import parse_skill_manifest_text
from .source_fetch import SourceFetchService
from .source_package import MAX_ANCESTORS, MAX_JSON_BYTES, SourcePackageDiscovery


@dataclass(frozen=True)
class PackageSource:
    kind: Literal['github', 'npm']
    locator: str
    ref: str | None = None
    revision: str | None = None
    version: str | None = None
    package_path: str | None = None
    skill_path: str | None = None
    integrity: str | None = None
    artifact_url: str | None = None


@dataclass(frozen=True)
class SourceEvidence:
    kind: str
    location: str
    source: PackageSource


@dataclass(frozen=True)
class DistributionRelationship:
    source: PackageSource
    relationship: str
    evidence: str
    # A repository declaration does not prove a native harness or byte equivalence.
    harness: str | None = None


@dataclass
class PackageResolution:
    status: Literal['resolved', 'ambiguous', 'unresolved', 'unavailable']
    observation_ref: str
    source: PackageSource | None = None
    evidence: tuple[SourceEvidence, ...] = ()
    relationship: str = 'unproven'
    artifact_root: Path | None = None
    capabilities: dict[str, object] | None = None
    reason: str | None = None
    distributions: tuple[DistributionRelationship, ...] = ()
    limitations: list[str] = field(default_factory=list)
    artifact_lifetime: str = 'caller-owned workspace; not persisted'


def _text(path: Path, root: Path) -> str:
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Metadata escapes its source boundary')
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError('Linked provenance metadata is unsupported')
        current = current.parent
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
        raise ValueError('Source metadata is not a bounded regular file')
    with path.open('r', encoding='utf-8') as stream:
        value = stream.read(MAX_JSON_BYTES + 1)
    if len(value) > MAX_JSON_BYTES:
        raise ValueError('Source metadata exceeds limit')
    return value


def _json(path: Path, root: Path) -> dict:
    value = json.loads(_text(path, root))
    if not isinstance(value, dict):
        raise ValueError('Source metadata is not an object')
    return value


def _repository(value: object) -> PackageSource:
    directory = None
    if isinstance(value, dict):
        directory = value.get('directory')
        value = value.get('url')
    if not isinstance(value, str):
        raise ValueError('Repository declaration is missing')
    raw = value.removeprefix('git+')
    if raw.startswith('github:'):
        raw = 'https://github.com/' + raw[7:]
    if raw.startswith('git@github.com:'):
        raw = 'https://github.com/' + raw[len('git@github.com:'):]
    parsed = urlsplit(raw)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query
            or parsed.username or parsed.password or any(ord(c) < 33 for c in raw)):
        raise ValueError('Repository host or URL is unsupported')
    repo = parsed.path.removeprefix('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*', repo):
        raise ValueError('Invalid repository identity')
    return PackageSource('github', 'github:' + repo, ref=parsed.fragment or None,
                         package_path=relative_path(directory) if directory is not None else None)


def _npm(name: str, version: str, *, integrity: str | None = None) -> PackageSource:
    if (not isinstance(name, str) or not re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+', name)
            or not isinstance(version, str)
            or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?', version)):
        raise ValueError('Exact npm coordinate required')
    if integrity is not None:
        _integrity(integrity)
    return PackageSource('npm', f'npm:{name}@{version}', version=version, integrity=integrity)


def _integrity(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', value):
        raise ValueError('A valid sha512 integrity value is required')
    return value


def _github_locator(locator: str, ref: str | None, skill: str | None = None) -> PackageSource:
    repo = github_repo_from_locator(locator)
    source = _repository('https://github.com/' + (repo or ''))
    parts = locator.removeprefix('github:').split('/', 2)
    skill = skill or (parts[2] if len(parts) == 3 else None)
    return replace(source, ref=ref, skill_path=relative_path(skill) if skill is not None else None)


class PackageSourceResolver:
    def __init__(self, source_fetcher: SourceFetchService | None = None, *, stop_paths: tuple[Path, ...] = ()):
        self.fetcher = source_fetcher or SourceFetchService()
        self.stop_paths = tuple(p.resolve() for p in stop_paths)

    def resolve(self, entry: InventoryEntry, *, work_dir: Path) -> PackageResolution:
        """Artifacts survive until caller removes work_dir. Failure removes only our own scratch directory."""
        result = PackageResolution('unresolved', entry.skill_ref)
        scratch = None
        try:
            evidence, local = self._evidence(entry)
            result.evidence = tuple(evidence)
            if not evidence:
                result.reason = 'No deterministic upstream source evidence; names are not source authority.'
                return result
            source = self._choose(evidence)
            if source is None:
                result.status = 'ambiguous'
                result.reason = 'Conflicting repository, package, path or revision evidence.'
                return result
            result.source = source
            result.relationship = 'declared upstream of observed skill; external installation remains unowned'
            workspace = work_dir.resolve(strict=True)
            if (not workspace.is_dir() or work_dir.is_symlink() or
                    (local is not None and (workspace.is_relative_to(local) or local.is_relative_to(workspace)))):
                raise ValueError('Workspace must be separate from observation')
            scratch = Path(mkdtemp(prefix='package-source-', dir=workspace))
            result.status = 'unavailable'
            if source.kind == 'npm':
                root, source, links = self._acquire_npm(source, scratch)
                result.distributions = links
                package = SourcePackageDiscovery().inspect_root(root)['package']
            else:
                checkout, revision = self.fetcher.acquire_repository(
                    source_locator=source.locator, work_dir=scratch, ref=source.revision or source.ref,
                )
                source = replace(source, revision=revision)
                result.source = source
                root, package, skill_path = self._package_boundary(checkout, source)
                source = replace(source, package_path=root.relative_to(checkout).as_posix(), skill_path=skill_path)
                if root != checkout:
                    # Retain just the authored package, not siblings in a monorepo.
                    target = scratch / 'package'
                    root.rename(target)
                    shutil.rmtree(checkout)
                    root = target
                    package = SourcePackageDiscovery().inspect_root(root)['package']
            result.source = source
            result.artifact_root = root
            result.capabilities = package
            if package is None:
                result.limitations.append('Source acquired, but no supported package capability manifest at its root.')
            result.status = 'resolved'
            return result
        except _AmbiguousBoundary:
            result.status = 'ambiguous'
            result.reason = 'Upstream package or skill boundary is ambiguous.'
        except (OSError, ValueError, RuntimeError, configparser.Error):
            result.reason = ('Source is inaccessible, invalid or outside supported safety boundaries; '
                             'no credentials were requested and no local installation was changed.')
        finally:
            if scratch is not None and result.status != 'resolved':
                shutil.rmtree(scratch)
        return result

    @staticmethod
    def _choose(evidence: list[SourceEvidence]) -> PackageSource | None:
        # Compare identity and every explicit pin; absence is not conflicting evidence.
        sources = [item.source for item in evidence]
        if len({(s.kind, s.locator) for s in sources}) != 1:
            return None
        selected = sources[0]
        for key in ('ref', 'revision', 'package_path', 'skill_path', 'integrity', 'artifact_url'):
            values = {getattr(s, key) for s in sources if getattr(s, key) is not None}
            if len(values) > 1:
                return None
            if values:
                selected = replace(selected, **{key: values.pop()})
        if (selected.ref and selected.revision and selected.ref != selected.revision
                and not any(s.ref == selected.ref and s.revision == selected.revision for s in sources)):
            # Separate declarations do not prove that a tag names the observed commit.
            return None
        return selected

    def _evidence(self, entry: InventoryEntry) -> tuple[list[SourceEvidence], Path | None]:
        evidence = []
        if entry.source.kind == 'github':
            skill_path = entry.source_path if entry.source_path and not Path(entry.source_path).is_absolute() else None
            source = _github_locator(entry.source.locator, entry.source_ref, skill_path)
            evidence.append(SourceEvidence('skill_manager_locator', 'inventory.source', source))
            if entry.source_path is None or not Path(entry.source_path).is_absolute():
                return evidence, None
        if entry.source.kind == 'npm':
            name, version = entry.source.locator.removeprefix('npm:').rsplit('@', 1)
            evidence.append(SourceEvidence('native_coordinate', 'inventory.source', _npm(name, version)))
            return evidence, None
        paths = {Path(entry.source_path)} if entry.source_path else {
            s.path for s in entry.sightings if s.path is not None
        }
        if not paths:
            return evidence, None
        if any(not p.is_absolute() for p in paths):
            raise ValueError('Relative observation path')
        if any(candidate.is_symlink() for path in paths for candidate in (path, *path.parents)):
            raise ValueError('Linked observation requires explicit upstream locator')
        paths = {p.resolve(strict=True) for p in paths}
        if len(paths) != 1:
            raise ValueError('Multiple original observations')
        skill = paths.pop()
        if any(skill.is_relative_to(p) for p in self.stop_paths):
            return [], skill
        # A directory path from runtime does not prove a repository or package.
        if not skill.is_dir() or not (skill / 'SKILL.md').exists():
            return [], skill
        manifest = parse_skill_manifest_text(_text(skill / 'SKILL.md', skill))
        if manifest.source_kind == 'github' and manifest.source_locator:
            evidence.append(SourceEvidence('skill_frontmatter', 'SKILL.md',
                                          _github_locator(manifest.source_locator, entry.source_ref)))
        current = skill
        package_root = None
        for _ in range(MAX_ANCESTORS + 1):
            if current == Path.home() or current == Path(current.anchor) or current in self.stop_paths:
                break
            declarations = [current / p for p in ('plugin.json', '.claude-plugin/plugin.json',
                                                  '.cursor-plugin/plugin.json', '.codex-plugin/plugin.json', 'package.json')]
            present = [p for p in declarations if p.exists() or p.is_symlink()]
            if package_root is None and present:
                package_root = current
                locked = self._locked_package(current)
                if locked:
                    return [locked], skill
                membership = SourcePackageDiscovery(stop_paths=self.stop_paths).resolve(skill)['package']
                for path in present:
                    data = _json(path, current)
                    if 'repository' in data:
                        if path.name != 'package.json' and (
                            membership is None or Path(membership['root']) != current
                        ):
                            continue
                        source = _repository(data['repository'])
                        evidence.append(SourceEvidence('package_repository', str(path.relative_to(current)), source))
            if (current / '.git').exists():
                git = current / '.git'
                if not git.is_dir() or git.is_symlink():
                    raise ValueError('Linked Git metadata unsupported')
                config = configparser.ConfigParser(interpolation=None)
                config.read_string(_text(git / 'config', git))
                if config.has_option('remote "origin"', 'url'):
                    source = _repository(config.get('remote "origin"', 'url'))
                    head = _text(git / 'HEAD', git).strip()
                    ref = None
                    if head.startswith('ref: '):
                        ref = relative_path(head[5:])
                        if not ref.startswith('refs/'):
                            raise ValueError('Invalid Git ref')
                        if (git / ref).exists():
                            head = _text(git / ref, git).strip()
                        else:
                            head = next((line.split(' ')[0] for line in _text(git / 'packed-refs', git).splitlines()
                                         if line.endswith(' ' + ref)), '')
                    if not re.fullmatch(r'[a-f0-9]{40}', head):
                        raise ValueError('Exact Git revision missing')
                    evidence.append(SourceEvidence('git_origin_commit', '.git/config + HEAD', replace(
                        source, revision=head, ref=source.ref or (ref.removeprefix('refs/heads/') if ref else None),
                        skill_path=skill.relative_to(current).as_posix(),
                        package_path=package_root.relative_to(current).as_posix() if package_root else None,
                    )))
                break
            if current.name == 'node_modules':
                break
            current = current.parent
        return evidence, package_root or skill

    @staticmethod
    def _locked_package(package: Path) -> SourceEvidence | None:
        for parent in list(package.parents)[:MAX_ANCESTORS]:
            if parent == Path.home() or parent == Path(parent.anchor):
                break
            relative = package.relative_to(parent).as_posix()
            if not relative.startswith('node_modules/'):
                continue
            lock = parent / 'package-lock.json'
            if not lock.exists():
                continue
            payload = _json(lock, parent)
            if payload.get('lockfileVersion') not in (2, 3):
                return None
            packages = payload.get('packages')
            if not isinstance(packages, dict):
                raise ValueError('Invalid native package table')
            record = packages.get(relative)
            if not isinstance(record, dict) or record.get('link'):
                return None
            data = _json(package / 'package.json', package)
            if data.get('version') != record.get('version'):
                raise ValueError('Native version mismatch')
            resolved = record.get('resolved', '')
            if not isinstance(resolved, str):
                raise ValueError('Invalid native artifact locator')
            if resolved.startswith(('git+https://github.com/', 'https://github.com/')):
                source = _repository(resolved)
                if not source.ref or not re.fullmatch(r'[a-f0-9]{40}', source.ref):
                    raise ValueError('Native Git lock missing exact revision')
                return SourceEvidence('npm_lock_git', 'package-lock.json:' + relative,
                                      replace(source, revision=source.ref))
            public_url(resolved)
            if urlsplit(resolved).hostname != 'registry.npmjs.org':
                raise ValueError('Unsupported native registry')
            source = _npm(data.get('name'), data.get('version'), integrity=record.get('integrity'))
            if not source.integrity:
                raise ValueError('Native integrity missing')
            return SourceEvidence('npm_lock', 'package-lock.json:' + relative, replace(source, artifact_url=resolved))
        return None

    @staticmethod
    def _package_boundary(checkout: Path, source: PackageSource) -> tuple[Path, dict | None, str | None]:
        inspector = SourcePackageDiscovery(boundary=checkout)
        if source.package_path is not None:
            root = checkout / relative_path(source.package_path)
            if not root.is_dir() or not root.resolve().is_relative_to(checkout):
                raise ValueError('Declared package directory absent')
            if source.skill_path:
                skill = checkout / relative_path(source.skill_path)
                if not skill.is_relative_to(root):
                    raise _AmbiguousBoundary()
                membership = inspector.resolve(skill)['package']
                if membership is None or Path(membership['root']) != root:
                    raise ValueError('Pinned skill is not declared by pinned package')
            return root, inspector.inspect_root(root)['package'], source.skill_path
        if source.skill_path:
            skill_path = relative_path(source.skill_path)
            exact = checkout / skill_path
            if (exact / 'SKILL.md').is_file():
                candidates = [exact]
            elif '/' not in skill_path:
                candidates = matching_skill_roots(checkout, skill_path)
            else:
                raise ValueError('Pinned upstream skill path absent')
            if len(candidates) > 1:
                raise _AmbiguousBoundary()
            if not candidates:
                raise ValueError('Upstream skill identifier absent')
            result = inspector.resolve(candidates[0])
            if result['package']:
                return Path(result['package']['root']), result['package'], candidates[0].relative_to(checkout).as_posix()
            raise ValueError('Upstream package boundary unproven')
        result = inspector.inspect_root(checkout)
        if result['package'] is None and not (checkout / 'package.json').is_file():
            raise ValueError('Repository root is not a proven package')
        return checkout, result['package'], None

    @staticmethod
    def _acquire_npm(source: PackageSource, scratch: Path):
        name, version = source.locator.removeprefix('npm:').rsplit('@', 1)
        data = read_public_json(f'https://registry.npmjs.org/{quote(name, safe="")}/{quote(version, safe="")}')
        if data.get('name') != name or data.get('version') != version:
            raise ValueError('Registry coordinate mismatch')
        dist = data.get('dist', {})
        if not isinstance(dist, dict):
            raise ValueError('Invalid registry distribution metadata')
        integrity = _integrity(dist.get('integrity'))
        if source.integrity and source.integrity != integrity:
            raise ValueError('Native and registry integrity disagree')
        url = public_url(dist.get('tarball', ''))
        if urlsplit(url).hostname != 'registry.npmjs.org':
            raise ValueError('Unsupported registry artifact host')
        if source.artifact_url and source.artifact_url != url:
            raise ValueError('Native artifact URL disagrees with registry')
        content = read_public_bytes(url)
        if base64.b64encode(hashlib.sha512(content).digest()).decode() != integrity[7:]:
            raise ValueError('Registry artifact integrity mismatch')
        root = scratch / 'package'
        extract_source(content, root, kind='tar')
        manifest = _json(root / 'package.json', root)
        if manifest.get('name') != name or manifest.get('version') != version:
            raise ValueError('Artifact coordinate mismatch')
        links = ()
        if data.get('repository'):
            repository = _repository(data['repository'])
            revision = data.get('gitHead')
            if isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{40}', revision):
                repository = replace(repository, revision=revision)
            links = (DistributionRelationship(repository, 'publisher-declared source repository',
                                               'npm version repository/gitHead; not byte equivalence'),)
        return root, replace(source, integrity=integrity, revision=integrity, artifact_url=url), links


class _AmbiguousBoundary(ValueError):
    pass
