from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
from threading import Lock
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from skill_manager.atomic_files import atomic_write_text


# OpenCode's agent tool uses Skill.Service, not the separate V2 registry.
RUNTIME_SKILLS_API_PATH = "/skill"
RUNTIME_SKILLS_TIMEOUT_SECONDS = 5.0
RUNTIME_SKILLS_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RUNTIME_SKILL_NAME_LENGTH = 200
MAX_RUNTIME_SKILL_DESCRIPTION_LENGTH = 4_000
MAX_RUNTIME_SKILL_LOCATION_LENGTH = 4_000


class RuntimeSkillClientError(Exception):
    """Raised when an explicitly requested runtime skill refresh is unsafe or fails."""


@dataclass(frozen=True)
class RuntimeSkillRecord:
    name: str
    description: str
    location: Path | None
    content: str | None
    slash: object | None = None

    @property
    def package_path(self) -> Path | None:
        if self.location is not None and self.location.name == "SKILL.md":
            return self.location.parent
        return None

    @property
    def revision(self) -> str:
        digest = hashlib.sha256()
        slash = json.dumps(self.slash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in (self.name, self.description, str(self.location or ""), self.content or "", slash):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()


class RuntimeSkillClient(Protocol):
    def fetch(
        self,
        *,
        server_url: str,
        directory: Path,
        username: str | None,
        password: str | None,
    ) -> tuple[RuntimeSkillRecord, ...]: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise RuntimeSkillClientError("runtime skill server redirects are not allowed")


class OpenCodeRuntimeSkillsClient:
    def __init__(
        self,
        *,
        opener=None,
        timeout_seconds: float = RUNTIME_SKILLS_TIMEOUT_SECONDS,
        max_response_bytes: int = RUNTIME_SKILLS_MAX_RESPONSE_BYTES,
    ) -> None:
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirectHandler())
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def fetch(
        self,
        *,
        server_url: str,
        directory: Path,
        username: str | None,
        password: str | None,
    ) -> tuple[RuntimeSkillRecord, ...]:
        normalized_url = _validate_server_url(server_url)
        if not directory.is_absolute():
            raise RuntimeSkillClientError("runtime skill directory must be absolute")
        if username is not None and password is None or username is None and password is not None:
            raise RuntimeSkillClientError("runtime skill username and password must be supplied together")

        url = _runtime_skills_url(normalized_url, directory)
        request = Request(url, headers={"Accept": "application/json"})
        if username is not None and password is not None:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            request.add_header("Authorization", f"Basic {encoded}")

        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                body = self._read_bounded_body(response)
        except RuntimeSkillClientError:
            raise
        except HTTPError as error:
            error.close()
            raise RuntimeSkillClientError(
                f"runtime skill server returned HTTP {error.code}"
            ) from error
        except (TimeoutError, socket.timeout):
            raise RuntimeSkillClientError("runtime skill server timed out") from None
        except (URLError, OSError):
            raise RuntimeSkillClientError("runtime skill server could not be reached") from None

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeSkillClientError("runtime skill server returned invalid JSON") from error
        return _parse_runtime_skill_response(payload)

    def _read_bounded_body(self, response) -> bytes:
        content_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
        if content_length is None and hasattr(response, "getheader"):
            content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_response_bytes:
                    raise RuntimeSkillClientError("runtime skill response is too large")
            except ValueError:
                raise RuntimeSkillClientError("runtime skill server returned an invalid content length") from None

        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise RuntimeSkillClientError("runtime skill response is too large")
        return body


@dataclass(frozen=True)
class _RuntimeSnapshot:
    records: tuple[RuntimeSkillRecord, ...] = ()
    status: str = "disconnected"
    server_url: str | None = None
    directory: str | None = None
    error: str | None = None
    last_refreshed: str | None = None
    stale: bool = False


class RuntimeSkillSnapshotStore:
    """Restore last-known data locally; only deliberate refreshes replace it."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._snapshot = self._restore()
        self._lock = Lock()

    def _restore(self) -> _RuntimeSnapshot:
        if self._path is None:
            return _RuntimeSnapshot()
        try:
            with self._path.open("rb") as stream:
                raw = stream.read(RUNTIME_SKILLS_MAX_RESPONSE_BYTES + 1)
            if len(raw) > RUNTIME_SKILLS_MAX_RESPONSE_BYTES:
                raise ValueError("snapshot too large")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("invalid snapshot")
            server_url = _validate_server_url(payload["serverUrl"])
            directory = payload["directory"]
            refreshed = payload["lastRefreshed"]
            if not isinstance(directory, str) or not Path(directory).is_absolute():
                raise ValueError("invalid directory")
            if not isinstance(refreshed, str) or datetime.fromisoformat(refreshed).tzinfo is None:
                raise ValueError("invalid timestamp")
            if not isinstance(payload["records"], list):
                raise ValueError("invalid records")
            records = tuple(
                record for item in payload["records"]
                if (record := _parse_runtime_skill_entry(item)) is not None
            )
            return _RuntimeSnapshot(
                records=records, status="ready", server_url=server_url,
                directory=directory, last_refreshed=refreshed, stale=True,
            )
        except FileNotFoundError:
            return _RuntimeSnapshot()
        except (OSError, ValueError, TypeError, KeyError, RuntimeSkillClientError):
            return _RuntimeSnapshot(status="error", error="Saved runtime snapshot could not be restored.", stale=True)

    def records(self) -> tuple[RuntimeSkillRecord, ...]:
        with self._lock:
            return self._snapshot.records

    def replace(self, *, records: tuple[RuntimeSkillRecord, ...], server_url: str, directory: str) -> None:
        with self._lock:
            refreshed = datetime.now(timezone.utc).isoformat()
            if self._path is not None:
                # Explicit allowlist: connection credentials never enter the snapshot.
                payload = {
                    "version": 1, "serverUrl": server_url, "directory": directory,
                    "lastRefreshed": refreshed,
                    "records": [
                        {"name": r.name, "description": r.description,
                         "location": str(r.location) if r.location is not None else None,
                         "content": r.content, "slash": r.slash}
                        for r in records
                    ],
                }
                try:
                    encoded = json.dumps(payload, ensure_ascii=False)
                    if len(encoded.encode("utf-8")) > RUNTIME_SKILLS_MAX_RESPONSE_BYTES:
                        raise RuntimeSkillClientError("Runtime snapshot is too large to save.")
                    atomic_write_text(self._path, encoded)
                except OSError:
                    raise RuntimeSkillClientError("Runtime snapshot could not be saved.") from None
            self._snapshot = _RuntimeSnapshot(
                records=records,
                status="ready",
                server_url=server_url,
                directory=directory,
                last_refreshed=refreshed,
            )

    def mark_error(self, *, server_url: str, directory: str, error: str) -> None:
        with self._lock:
            current = self._snapshot
            retained_server_url = current.server_url if current.last_refreshed else server_url
            retained_directory = current.directory if current.last_refreshed else directory
            self._snapshot = _RuntimeSnapshot(
                records=current.records,
                status="error",
                server_url=retained_server_url,
                directory=retained_directory,
                error=error,
                last_refreshed=current.last_refreshed,
                stale=True,
            )

    def clear(self) -> None:
        with self._lock:
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    raise RuntimeSkillClientError("Saved runtime snapshot could not be cleared.") from None
            self._snapshot = _RuntimeSnapshot()

    def status(self) -> dict[str, object]:
        with self._lock:
            current = self._snapshot
            return {
                "status": current.status,
                "serverUrl": current.server_url,
                "directory": current.directory,
                "skillCount": len(current.records),
                "error": current.error,
                "lastRefreshed": current.last_refreshed,
                "stale": current.stale,
            }


class RuntimeSkillsService:
    def __init__(
        self,
        *,
        store: RuntimeSkillSnapshotStore,
        client: RuntimeSkillClient,
    ) -> None:
        self.store = store
        self.client = client

    def records(self) -> tuple[RuntimeSkillRecord, ...]:
        return self.store.records()

    def status(self) -> dict[str, object]:
        return self.store.status()

    def refresh(
        self,
        *,
        server_url: str,
        directory: Path,
        username: str | None,
        password: str | None,
    ) -> dict[str, object]:
        normalized_server_url = _validate_server_url(server_url)
        try:
            records = self.client.fetch(
                server_url=normalized_server_url,
                directory=directory,
                username=username,
                password=password,
            )
            self.store.replace(
                records=records,
                server_url=normalized_server_url,
                directory=str(directory),
            )
        except RuntimeSkillClientError as error:
            self.store.mark_error(
                server_url=normalized_server_url,
                directory=str(directory),
                error=str(error),
            )
            raise
        return self.store.status()

    def disconnect(self) -> dict[str, object]:
        self.store.clear()
        return self.store.status()


def runtime_skill_document(record: RuntimeSkillRecord) -> str | None:
    if not record.content:
        return None
    name = json.dumps(record.name, ensure_ascii=False)
    description = json.dumps(record.description, ensure_ascii=False)
    slash = ""
    if record.slash is not None:
        slash = f"slash: {json.dumps(record.slash, ensure_ascii=False, separators=(',', ':'))}\n"
    suffix = "" if record.content.endswith("\n") else "\n"
    return f"---\nname: {name}\ndescription: {description}\n{slash}---\n\n{record.content}{suffix}"


def _validate_server_url(server_url: str) -> str:
    if len(server_url) > 2_000:
        raise RuntimeSkillClientError("runtime skill server URL is too long")
    try:
        parsed = urlsplit(server_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise RuntimeSkillClientError("runtime skill server URL is invalid") from None
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise RuntimeSkillClientError("runtime skill server URL must be a loopback HTTP(S) URL without credentials")
    if parsed.query or parsed.fragment:
        raise RuntimeSkillClientError("runtime skill server URL must not include a query or fragment")
    if hostname.casefold() != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                raise RuntimeSkillClientError("runtime skill server URL must target loopback")
        except ValueError:
            raise RuntimeSkillClientError("runtime skill server URL must target loopback") from None
    if port is not None and not 1 <= port <= 65_535:
        raise RuntimeSkillClientError("runtime skill server URL has an invalid port")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _runtime_skills_url(server_url: str, directory: Path) -> str:
    return f"{server_url}{RUNTIME_SKILLS_API_PATH}?{urlencode({'directory': str(directory)})}"


def _parse_runtime_skill_response(
    payload: object,
) -> tuple[RuntimeSkillRecord, ...]:
    if not isinstance(payload, list):
        raise RuntimeSkillClientError("runtime skill server returned an invalid response")

    records: list[RuntimeSkillRecord] = []
    for item in payload:
        record = _parse_runtime_skill_entry(item)
        if record is not None:
            records.append(record)
    return tuple(records)


def _parse_runtime_skill_entry(item: object) -> RuntimeSkillRecord | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    description = item.get("description", "")
    location = item.get("location")
    content = item.get("content")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_RUNTIME_SKILL_NAME_LENGTH:
        return None
    if not isinstance(description, str) or len(description) > MAX_RUNTIME_SKILL_DESCRIPTION_LENGTH:
        return None
    if content is not None and not isinstance(content, str):
        return None

    path: Path | None = None
    if location is not None:
        if not isinstance(location, str) or len(location) > MAX_RUNTIME_SKILL_LOCATION_LENGTH:
            return None
        try:
            candidate = Path(location)
        except (OSError, ValueError):
            return None
        # Agent-facing built-ins use a non-filesystem location marker.
        if candidate.is_absolute() and candidate.suffix.casefold() == ".md":
            path = candidate

    slash = item.get("slash")
    try:
        json.dumps(slash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        slash = None

    return RuntimeSkillRecord(
        name=name.strip(),
        description=description.strip(),
        location=path,
        content=content if content and content.strip() else None,
        slash=slash,
    )


__all__ = [
    "OpenCodeRuntimeSkillsClient",
    "RuntimeSkillClient",
    "RuntimeSkillClientError",
    "RuntimeSkillRecord",
    "RuntimeSkillSnapshotStore",
    "RuntimeSkillsService",
    "runtime_skill_document",
]
