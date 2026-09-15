"""Whole-package ownership, separate from standalone skill copies and harnesses."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import TemporaryDirectory

from skill_manager.atomic_files import atomic_write_text, file_lock
from skill_manager.sources.artifacts import MAX_EXPANDED, MAX_FILES, excluded_source_part, relative_path
from .package_resolution import PackageResolution


def managed_package_id(source) -> str:
    if source is None or source.kind not in ('github', 'npm') or not source.revision:
        raise ValueError('An exact authoritative package source is required')
    # Skill paths, branch aliases and temporary discovery IDs are not package identity.
    identity = [source.kind, source.locator, source.revision, source.version,
                source.package_path or '.', source.integrity]
    return hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()


def _safe_path(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked package storage is not owned storage')


def _files(root: Path, *, filter_forbidden: bool = False):
    _safe_path(root)
    if not root.is_dir():
        raise ValueError('Package artifact is missing')
    count = 0
    size = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        base = Path(parent)
        if len(base.relative_to(root).parts) > 64:
            raise ValueError('Package depth limit exceeded')
        for name in sorted(directories + files):
            if excluded_source_part(name) and not filter_forbidden:
                raise ValueError('Managed artifact contains forbidden machine state')
            path = base / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Package contains a link or special file')
            count += 1
            size += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if count > MAX_FILES or size > MAX_EXPANDED:
                raise ValueError('Package size limit exceeded')
        directories[:] = sorted(name for name in directories if not excluded_source_part(name))
        for name in directories:
            yield base / name
        for name in sorted(files):
            if not excluded_source_part(name):
                yield base / name


def package_fingerprint(root: Path, *, filter_forbidden: bool = False) -> str:
    digest = hashlib.sha256()
    for path in _files(root, filter_forbidden=filter_forbidden):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        if path.is_dir():
            digest.update(b'd\0')
            continue
        digest.update(b'x' if path.stat().st_mode & 0o111 else b'-')
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                digest.update(chunk)
        digest.update(b'\0')
    return digest.hexdigest()


def _capabilities(value):
    if value is None:
        return None
    # 04A contains structural metadata only. Discard its ephemeral root and root ID.
    keys = ('name', 'version', 'evidence', 'manifests', 'components', 'diagnostics', 'revision')
    return {key: deepcopy(value[key]) for key in keys}


class ManagedPackageStore:
    def __init__(self, root: Path):
        self.root = root
        self.manifest = root / 'manifest.json'
        self.lock = root / 'manifest.lock'

    def _load(self):
        _safe_path(self.manifest)
        if not self.manifest.exists():
            return {'version': 1, 'packages': {}, 'skills': {}}
        value = json.loads(self.manifest.read_text(encoding='utf-8'))
        if value.get('version') != 1 or not isinstance(value.get('packages'), dict) or not isinstance(value.get('skills'), dict):
            raise ValueError('Unsupported managed package manifest')
        return value

    def _write(self, value):
        _safe_path(self.manifest)
        atomic_write_text(self.manifest, json.dumps(value, indent=2, ensure_ascii=False) + '\n')

    def _prepare(self):
        _safe_path(self.root)
        _safe_path(self.lock)
        self.root.mkdir(parents=True, exist_ok=True)

    def _artifact(self, package_id: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{64}', package_id):
            raise ValueError('Invalid managed package identity')
        path = self.root / 'artifacts' / package_id
        _safe_path(path)
        return path

    def links(self) -> dict:
        return deepcopy(self._load()['skills'])

    def list(self) -> list[dict]:
        data = self._load()
        return [self._view(record, data) for record in data['packages'].values()]

    def get(self, package_id: str) -> dict:
        data = self._load()
        if package_id not in data['packages']:
            raise ValueError('Unknown managed package')
        return self._view(data['packages'][package_id], data)

    def _view(self, record: dict, data: dict) -> dict:
        result = deepcopy(record)
        try:
            artifact = self._artifact(record['id'])
            state = ('not-retained' if not artifact.exists() and record['fingerprint'] is None else
                     'missing' if not artifact.exists() else
                     'current' if package_fingerprint(artifact) == record['fingerprint'] else 'changed')
        except (OSError, ValueError, RuntimeError):
            artifact = None
            state = 'unavailable'
        result['artifactRoot'] = str(artifact) if artifact is not None and artifact.is_dir() else None
        result['artifactState'] = state
        result['state'] = state if state not in ('current', 'not-retained') else record['upstreamState']
        links = {key: value for key, value in data['skills'].items() if value.get('packageId') == record['id']}
        result['skillRefs'] = sorted(links)
        result['observations'] = [observation for link in links.values() for observation in link['observations']]
        for observation in result['observations']:
            path = observation.get('path')
            observation['state'] = 'present' if path and Path(path).exists() else 'missing' if path else 'unknown'
        return result

    def record_failure(self, resolution: PackageResolution, *, name: str, observations: list) -> None:
        self._prepare()
        with file_lock(self.lock):
            data = self._load()
            old = data['skills'].get(resolution.observation_ref, {})
            if old.get('packageId') in data['packages']:
                record = data['packages'][old['packageId']]
                record.update(upstreamState=resolution.status, resolutionStatus=resolution.status, reason=resolution.reason)
            data['skills'][resolution.observation_ref] = {
                'packageId': old.get('packageId'), 'name': name,
                'resolutionStatus': resolution.status, 'reason': resolution.reason,
                'observations': observations,
            }
            self._write(data)

    def adopt(self, resolution: PackageResolution, *, name: str, observations: list) -> dict:
        if resolution.status != 'resolved' or resolution.source is None:
            self.record_failure(resolution, name=name, observations=observations)
            raise ValueError('Package source is not resolved; individual-skill fallback is disabled')
        package_id = managed_package_id(resolution.source)
        source_root = resolution.artifact_root
        fingerprint = package_fingerprint(source_root, filter_forbidden=True) if source_root else None
        self._prepare()
        with file_lock(self.lock):
            data = self._load()
            destination = self._artifact(package_id)
            old = data['packages'].get(package_id)
            if source_root is None and old is not None:
                fingerprint = old['fingerprint']
            created = False
            try:
                if destination.exists():
                    if old is None:
                        raise ValueError('Existing artifact has no ownership record')
                    if package_fingerprint(destination) != fingerprint or old['fingerprint'] != fingerprint:
                        raise ValueError('Existing managed artifact changed; refusing to overwrite it')
                elif source_root is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with TemporaryDirectory(prefix='.capture-', dir=destination.parent) as temporary:
                        staged = Path(temporary) / 'package'
                        staged.mkdir()
                        for path in _files(source_root, filter_forbidden=True):
                            relative = relative_path(path.relative_to(source_root).as_posix())
                            target = staged / relative
                            if path.is_dir():
                                target.mkdir(parents=True, exist_ok=True)
                                continue
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(path, target, follow_symlinks=False)
                            target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
                        if package_fingerprint(staged) != fingerprint:
                            raise ValueError('Authoritative artifact changed during capture')
                        staged.rename(destination)
                        created = True
                captured = {
                    'id': package_id, 'ownership': 'skill-manager', 'source': asdict(resolution.source),
                    'fingerprint': fingerprint, 'capabilities': _capabilities(resolution.capabilities),
                    'evidence': [asdict(item) for item in resolution.evidence],
                    'distributions': [asdict(item) for item in resolution.distributions],
                    'limitations': resolution.limitations, 'resolutionStatus': 'resolved',
                    'upstreamState': 'current' if source_root else 'unavailable', 'candidateSource': None,
                    'reason': None if source_root else 'Authoritative source resolved; artifact not available.',
                }
                if old is not None:
                    # Associating another skill must not erase a known newer-source warning.
                    captured = deepcopy(old)
                    if old['fingerprint'] is None:
                        captured['fingerprint'] = fingerprint
                        captured['capabilities'] = _capabilities(resolution.capabilities)
                    if old.get('candidateSource') is None and source_root is not None:
                        captured.update(upstreamState='current', resolutionStatus='resolved', reason=None)
                    if source_root is None:
                        captured.update(upstreamState='unavailable', reason='Authoritative source resolved; artifact not available.')
                    if resolution.source.ref != old['source']['ref']:
                        captured.update(upstreamState='changed', candidateSource=asdict(resolution.source))
                data['packages'][package_id] = captured
                data['skills'][resolution.observation_ref] = {
                    'packageId': package_id, 'name': name, 'resolutionStatus': 'resolved', 'reason': None,
                    'observations': observations, 'sourceSkillPath': resolution.source.skill_path,
                }
                self._write(data)
            except BaseException:
                if created:
                    shutil.rmtree(destination)
                raise
        return self.get(package_id)

    def record_refresh(self, package_id: str, resolution: PackageResolution, *, retained_only: bool = False) -> dict:
        self._prepare()
        with file_lock(self.lock):
            data = self._load()
            record = data['packages'][package_id]
            state = resolution.status
            if state == 'resolved':
                state = 'current'
                if (managed_package_id(resolution.source) != package_id
                        or resolution.source.ref != record['source']['ref']
                        or (resolution.artifact_root is not None and package_fingerprint(resolution.artifact_root) != record['fingerprint'])
                        or _capabilities(resolution.capabilities) != record['capabilities']
                        or [asdict(item) for item in resolution.distributions] != record['distributions']):
                    state = 'changed'
                if resolution.artifact_root is None:
                    state = 'unavailable'
            keep_candidate = state == 'current' and retained_only and record.get('candidateSource') is not None
            if keep_candidate:
                state = 'changed'
            record['upstreamState'] = state
            record['resolutionStatus'] = resolution.status
            if state == 'changed' and not keep_candidate:
                record['candidateSource'] = asdict(resolution.source)
            elif state == 'current':
                record['candidateSource'] = None
            record['reason'] = resolution.reason
            self._write(data)
        return self.get(package_id)
