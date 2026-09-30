"""Bounded public downloads and inert source archives; never run package tools."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time
from collections.abc import Mapping
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile
import zlib

MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_FILES = 10000
MAX_DEPTH = 64
MAX_ALIAS_DEPTH = 64
MAX_LINK_TARGET = 4096
PUBLIC_HOSTS = frozenset({'api.github.com', 'codeload.github.com', 'registry.npmjs.org'})
_EXCLUDED = frozenset({'.git', 'node_modules', '.cache', '__pycache__', '.npmrc', '.pypirc',
                       '.ssh', '.aws', '.azure', '.venv', '.env', '.netrc'})


def excluded_source_part(name: str) -> bool:
    return name in _EXCLUDED or name.startswith('.env') or name.endswith(('.pem', '.key'))


def public_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError('Invalid source URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in PUBLIC_HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in url) or '\\' in url):
        raise ValueError('Unsupported public source URL')
    return url


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Source redirects are not accepted')


def read_public_bytes(url: str, *, limit: int = MAX_DOWNLOAD) -> bytes:
    request = Request(public_url(url), headers={'User-Agent': 'skill-manager/0.1'})
    deadline = time.monotonic() + 30
    with build_opener(_NoRedirect).open(request, timeout=10) as response:
        result = bytearray()
        while True:
            chunk = response.read(min(65536, limit + 1 - len(result)))
            result.extend(chunk)
            if len(result) > limit or time.monotonic() > deadline:
                raise ValueError('Source download limit exceeded')
            if not chunk:
                return bytes(result)


def read_public_json(url: str) -> dict:
    value = json.loads(read_public_bytes(url, limit=1024 * 1024))
    if not isinstance(value, dict):
        raise ValueError('Invalid source metadata')
    return value


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or value.startswith('/') or any(ord(c) < 32 for c in value)):
        raise ValueError('Unsafe source path')
    parts = value.split('/')
    if '..' in parts or any(not part for part in parts):
        raise ValueError('Unsafe source path')
    return PurePosixPath(value).as_posix()


def _relative_parts(value: str | Path) -> tuple[str, ...]:
    raw = value.as_posix() if isinstance(value, Path) else value
    normalized = relative_path(raw)
    return () if normalized == '.' else tuple(normalized.split('/'))


def _parts_key(parts: tuple[str, ...]) -> str:
    return '/'.join(parts) or '.'


def _join_paths(parent: str, child: str) -> str:
    if not child or child == '.':
        return parent or '.'
    if not parent or parent == '.':
        return child
    return f'{parent}/{child}'


def _alias_prefix(path: str, aliases: Mapping[str, str]) -> str | None:
    if path in aliases:
        return path
    parts = () if path in ('', '.') else tuple(path.split('/'))
    for length in range(len(parts), 0, -1):
        candidate = '/'.join(parts[:length])
        if candidate in aliases:
            return candidate
    return None


def _resolve_alias_path(
    path: str,
    aliases: Mapping[str, str],
    chain: set[str] | None = None,
) -> tuple[str, set[str], tuple[str, ...]]:
    current = path or '.'
    used = set(chain or ())
    edges: list[str] = []
    while True:
        source = _alias_prefix(current, aliases)
        if source is None:
            return current, used, tuple(edges)
        if source in used:
            raise ValueError('Cyclic source link')
        if len(used) >= MAX_ALIAS_DEPTH:
            raise ValueError('Source alias chain limit exceeded')
        suffix = current[len(source):].lstrip('/')
        current = _join_paths(aliases[source], suffix)
        used.add(source)
        edges.append(current)


def validate_alias_boundary(aliases: Mapping[str, str], package_path: str | Path | None) -> None:
    """Reject relevant alias chains that leave a selected package boundary.

    ``aliases`` and ``package_path`` are relative to the extracted archive root.
    Direct targets are not enough for this check: every alias edge in a chain
    starting inside or above the selected package must remain inside it. The
    archive wrapper itself is not a nested-package boundary.
    """
    boundary = _relative_parts(package_path or '.')
    normalized: dict[str, tuple[str, ...]] = {}
    for source, target in aliases.items():
        source_parts = _relative_parts(source)
        source_key = _parts_key(source_parts)
        if source_key in normalized:
            raise ValueError('Duplicate alias source')
        normalized[source_key] = _relative_parts(target)

    def inside(path: tuple[str, ...]) -> bool:
        return path[:len(boundary)] == boundary

    def ancestor(path: tuple[str, ...]) -> bool:
        return len(path) < len(boundary) and boundary[:len(path)] == path

    alias_map = {source: _parts_key(target) for source, target in normalized.items()}
    for source in alias_map:
        source_parts = _relative_parts(source)
        if not (inside(source_parts) or ancestor(source_parts)):
            continue
        _, _, edges = _resolve_alias_path(source, alias_map)
        for edge in edges:
            if not inside(_relative_parts(edge)):
                raise ValueError('Alias crosses selected package boundary')
    _, _, incoming_edges = _resolve_alias_path(_parts_key(boundary), alias_map)
    for edge in incoming_edges:
        if not inside(_relative_parts(edge)):
            raise ValueError('Alias crosses selected package boundary')


def _ensure_destination_safe(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError('Artifact destination must be new')
    current = destination.parent
    while True:
        if current.is_symlink():
            raise ValueError('Artifact destination uses a linked parent')
        if current.exists():
            if not current.is_dir():
                raise ValueError('Artifact destination parent is not a directory')
        parent = current.parent
        if parent == current:
            return
        current = parent


def extract_source(data: bytes, destination: Path, *, kind: str) -> dict[str, str]:
    """Strip one archive wrapper and materialise safe internal aliases as copies."""
    _ensure_destination_safe(destination)
    try:
        return _extract_source(data, destination, kind=kind)
    except (OSError, ValueError, RuntimeError, EOFError, UnicodeError,
            zipfile.BadZipFile, tarfile.TarError, zlib.error) as error:
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise ValueError('Source archive could not be extracted safely') from error


def _zip_member_kind(item: zipfile.ZipInfo) -> tuple[str, int]:
    mode = (item.external_attr >> 16) & 0xffff
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        return 'link', mode
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError('Source archive contains a link or special file')
    if item.is_dir() or file_type == stat.S_IFDIR:
        return 'dir', mode
    return 'file', mode


def _zip_link_target(bundle: zipfile.ZipFile, item: zipfile.ZipInfo) -> str:
    if item.file_size > MAX_LINK_TARGET:
        raise ValueError('Source link target is too large')
    with bundle.open(item) as source:
        raw = source.read(MAX_LINK_TARGET + 1)
    if len(raw) != item.file_size:
        raise ValueError('Invalid source link target')
    return raw.decode('utf-8')


def _archive_entries(bundle, *, kind: str) -> list[tuple[str, str, int, int, object, str | None]]:
    entries: list[tuple[str, str, int, int, object, str | None]] = []
    seen: set[str] = set()
    input_size = 0
    iterator = bundle.infolist() if kind == 'zip' else bundle
    for item in iterator:
        raw_name = item.filename if kind == 'zip' else item.name
        normalized = relative_path(raw_name.rstrip('/'))
        if normalized in seen:
            raise ValueError('Duplicate source path')
        seen.add(normalized)
        parts = PurePosixPath(normalized).parts
        if len(parts) > MAX_DEPTH:
            raise ValueError('Source path depth limit exceeded')

        if kind == 'zip':
            member_kind, mode = _zip_member_kind(item)
            size = item.file_size
            link_target = _zip_link_target(bundle, item) if member_kind == 'link' else None
        else:
            if item.issym():
                member_kind, mode, link_target = 'link', item.mode, item.linkname
                if not isinstance(link_target, str):
                    raise ValueError('Invalid source link target')
                if len(link_target.encode('utf-8')) > MAX_LINK_TARGET:
                    raise ValueError('Source link target is too large')
            elif item.islnk() or not (item.isfile() or item.isdir()):
                raise ValueError('Source archive contains a link or special file')
            else:
                member_kind, mode, link_target = ('dir' if item.isdir() else 'file', item.mode, None)
            size = item.size
        if not isinstance(size, int) or size < 0:
            raise ValueError('Invalid source member size')
        input_size += size
        if len(entries) + 1 > MAX_FILES or input_size > MAX_EXPANDED:
            raise ValueError('Source archive limit exceeded')
        entries.append((normalized, member_kind, size, mode, item, link_target))
    return entries


def _normalise_link_target(source: str, target: str) -> str:
    if (not isinstance(target, str) or not target or '\\' in target or ':' in target
            or any(ord(c) < 32 for c in target) or target.startswith(('/', '\\'))):
        raise ValueError('Unsafe source link target')
    if target.endswith('/'):
        target = target[:-1]
        if not target:
            raise ValueError('Unsafe source link target')
    raw_parts = target.split('/')
    if any(not part for part in raw_parts):
        raise ValueError('Unsafe source link target')
    if any(excluded_source_part(part) for part in raw_parts):
        raise ValueError('Source link target is excluded')
    resolved = list(PurePosixPath(source).parent.parts)
    for part in raw_parts:
        if part == '.':
            continue
        if part == '..':
            if not resolved:
                raise ValueError('Source link target escapes archive wrapper')
            resolved.pop()
        else:
            resolved.append(part)
        if len(resolved) > MAX_DEPTH:
            raise ValueError('Source path depth limit exceeded')
    return '/'.join(resolved) or '.'


def _prepare_archive_members(
    entries: list[tuple[str, str, int, int, object, str | None]],
) -> tuple[dict[str, tuple[str, int, int, object, str | None]], dict[str, str], dict[str, set[str]]]:
    roots = {PurePosixPath(name).parts[0] for name, *_ in entries}
    if len(roots) != 1:
        raise ValueError('Ambiguous source archive wrapper')

    members: dict[str, tuple[str, int, int, object, str | None]] = {}
    aliases: dict[str, str] = {}
    for name, member_kind, size, mode, item, link_target in entries:
        parts = PurePosixPath(name).parts
        if len(parts) == 1:
            if member_kind != 'dir':
                raise ValueError('Missing source archive wrapper')
            continue
        if any(excluded_source_part(part) for part in parts[1:]):
            continue
        relative = '/'.join(parts[1:])
        normalized_target = None
        if member_kind == 'link':
            normalized_target = _normalise_link_target(relative, link_target or '')
            target_parts = _relative_parts(normalized_target)
            if any(excluded_source_part(part) for part in target_parts):
                raise ValueError('Source link target is excluded')
            aliases[relative] = normalized_target
        members[relative] = (member_kind, size, mode, item, normalized_target)

    children: dict[str, set[str]] = {'': set()}
    for path in members:
        parts = path.split('/')
        parent = ''
        for part in parts:
            child = f'{parent}/{part}'.removeprefix('/')
            children.setdefault(parent, set()).add(child)
            children.setdefault(child, set())
            parent = child
    for path, member in members.items():
        if member[0] in ('file', 'link') and children[path]:
            raise ValueError('Source archive contains an output type collision')
    # Lexical '..' must not change the meaning of traversal through another alias.
    for name, kind, _, _, _, target in entries:
        source = '/'.join(PurePosixPath(name).parts[1:])
        if kind != 'link' or source not in aliases:
            continue
        cursor = list(PurePosixPath(source).parent.parts)
        for part in target.rstrip('/').split('/'):
            if part == '..':
                prefix = '/'.join(cursor)
                if _alias_prefix(prefix, aliases) is not None:
                    raise ValueError('Source link parent traversal crosses an alias')
                if prefix and (prefix not in children or members.get(prefix, ('dir',))[0] != 'dir'):
                    raise ValueError('Source link target is missing')
                cursor.pop()  # Wrapper containment was checked by normalisation.
            elif part != '.':
                cursor.append(part)
    return members, aliases, children


def _materialise_archive(
    entries: list[tuple[str, str, int, int, object, str | None]],
    destination: Path,
    opener,
) -> dict[str, str]:
    members, aliases, children = _prepare_archive_members(entries)

    def key(path: str) -> str:
        return '' if path == '.' else path

    def node_kind(path: str) -> str:
        path = key(path)
        member = members.get(path)
        if member is not None:
            return member[0]
        if not path or children.get(path):
            return 'dir'
        raise ValueError('Source link target is missing')

    plan: dict[str, tuple[str, tuple[str, int, int, object, str | None] | None]] = {}
    expanded_size = 0

    def add_directory(path: str, member=None) -> None:
        nonlocal plan
        if not path:
            return
        parts = path.split('/')
        if len(parts) > MAX_DEPTH:
            raise ValueError('Expanded source path depth limit exceeded')
        parent = path.rsplit('/', 1)[0] if '/' in path else ''
        if parent:
            add_directory(parent)
        current = plan.get(path)
        if current is not None:
            if current[0] != 'dir':
                raise ValueError('Source archive contains an output type collision')
            return
        plan[path] = ('dir', member)
        if len(plan) > MAX_FILES:
            raise ValueError('Expanded source file limit exceeded')

    def add_file(path: str, member) -> None:
        nonlocal expanded_size
        parts = path.split('/')
        if len(parts) > MAX_DEPTH:
            raise ValueError('Expanded source path depth limit exceeded')
        parent = path.rsplit('/', 1)[0] if '/' in path else ''
        if parent:
            add_directory(parent)
        if path in plan:
            raise ValueError('Source archive contains an output type collision')
        plan[path] = ('file', member)
        expanded_size += member[1]
        if len(plan) > MAX_FILES or expanded_size > MAX_EXPANDED:
            raise ValueError('Expanded source archive limit exceeded')

    def plan_node(source: str, output: str, chain: set[str]) -> None:
        source, used, _ = _resolve_alias_path(source, aliases, chain)
        source = key(source)
        member = members.get(source)
        kind = node_kind(source)
        if kind == 'file':
            add_file(output, member)
            return
        if kind != 'dir':
            raise ValueError('Source link target is not an ordinary file or directory')
        add_directory(output, member)
        for child in sorted(children.get(source, ())):
            plan_node(child, _join_paths(output, child.rsplit('/', 1)[-1]), used)

    for child in sorted(children['']):
        plan_node(child, child.rsplit('/', 1)[-1], set())

    destination.mkdir(mode=0o700)
    directories = sorted((path for path, entry in plan.items() if entry[0] == 'dir'),
                         key=lambda path: (len(path.split('/')), path))
    for path in directories:
        target = destination.joinpath(*path.split('/'))
        target.mkdir(mode=0o755)
        target.chmod(0o755)
    for path in sorted(path for path, entry in plan.items() if entry[0] == 'file'):
        member = plan[path][1]
        assert member is not None
        target = destination.joinpath(*path.split('/'))
        source = opener(member[3])
        if source is None:
            if member[1]:
                raise ValueError('Truncated source archive')
            target.touch()
        else:
            with source, target.open('xb') as output:
                remaining = member[1]
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk or len(chunk) > remaining:
                        raise ValueError('Truncated source archive')
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise ValueError('Source archive member is larger than declared')
        target.chmod(0o755 if member[2] & 0o111 else 0o644)
    return aliases


def _extract_source(data: bytes, destination: Path, *, kind: str) -> dict[str, str]:
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('Source download limit exceeded')
    if kind == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            entries = _archive_entries(bundle, kind='zip')
            return _materialise_archive(entries, destination, bundle.open)
    if kind == 'tar':
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
            entries = _archive_entries(bundle, kind='tar')
            return _materialise_archive(entries, destination, bundle.extractfile)
    raise ValueError('Unsupported source archive type')
