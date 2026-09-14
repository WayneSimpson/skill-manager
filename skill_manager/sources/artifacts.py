"""Bounded public downloads and inert source archives; never run package tools."""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024
MAX_FILES = 10000
PUBLIC_HOSTS = frozenset({'api.github.com', 'codeload.github.com', 'registry.npmjs.org'})
_EXCLUDED = frozenset({'.git', 'node_modules', '.cache', '__pycache__', '.npmrc', '.pypirc',
                       '.ssh', '.aws', '.azure', '.venv', '.env', '.netrc'})


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


def extract_source(data: bytes, destination: Path, *, kind: str) -> None:
    """Strip the single archive wrapper; reject links/devices and ambiguous paths."""
    if destination.exists() or destination.is_symlink():
        raise ValueError('Artifact destination must be new')
    try:
        _extract_source(data, destination, kind=kind)
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile, tarfile.TarError) as error:
        if destination.exists():
            shutil.rmtree(destination)
        raise ValueError('Source archive could not be extracted safely') from error


def _extract_source(data: bytes, destination: Path, *, kind: str) -> None:
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('Source download limit exceeded')
    members = []
    if kind == 'zip':
        bundle = zipfile.ZipFile(io.BytesIO(data))
        for item in bundle.infolist():
            mode = item.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Source archive contains a link or special file')
            members.append((item.filename.rstrip('/'), item.is_dir(), item.file_size, mode, item))
        opener = bundle.open
    else:
        bundle = tarfile.open(fileobj=io.BytesIO(data), mode='r:gz')
        for item in bundle:
            if not (item.isfile() or item.isdir()):
                raise ValueError('Source archive contains a link or special file')
            members.append((item.name.rstrip('/'), item.isdir(), item.size, item.mode, item))
            if len(members) > MAX_FILES:
                raise ValueError('Source file limit exceeded')
        opener = bundle.extractfile
    with bundle:
        if len(members) > MAX_FILES or sum(item[2] for item in members) > MAX_EXPANDED:
            raise ValueError('Source archive limit exceeded')
        names = set()
        roots = set()
        safe = []
        for name, directory, size, mode, item in members:
            normalized = relative_path(name)
            if normalized in names:
                raise ValueError('Duplicate source path')
            names.add(normalized)
            parts = PurePosixPath(normalized).parts
            if len(parts) > 64:
                raise ValueError('Source path depth limit exceeded')
            roots.add(parts[0])
            if len(parts) == 1:
                if not directory:
                    raise ValueError('Missing source archive wrapper')
                continue
            if any(p in _EXCLUDED or p.startswith('.env.') or p.endswith(('.pem', '.key')) for p in parts[1:]):
                continue
            safe.append((Path(*parts[1:]), directory, size, mode, item))
        if len(roots) != 1:
            raise ValueError('Ambiguous source archive wrapper')
        destination.mkdir(mode=0o700)
        for path, directory, size, mode, item in safe:
            target = destination / path
            if directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with opener(item) as source, target.open('xb') as output:
                    remaining = size
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError('Truncated source archive')
                        output.write(chunk)
                        remaining -= len(chunk)
                target.chmod(0o755 if mode & 0o111 else 0o644)
