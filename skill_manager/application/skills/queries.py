from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from skill_manager.errors import MutationError
from skill_manager.sources import github_folder_url, github_repo_from_locator, github_repo_url

from .document_utils import read_skill_document_markdown
from .inventory import InventoryEntry, SkillInventory
from .package import fingerprint_package
from .policy import can_stop_managing, can_update, has_local_changes, sort_entries
from .presenters import skill_detail_payload, skills_page_payload, source_status_payload
from .read_models import SkillsReadModelService
from .source_fetch import SourceFetchService
from .source_package import SourcePackageDiscovery
from .package_resolution import PackageResolution, PackageSourceResolver
from .package_resolution import PackageSource
from .identity import SourceDescriptor
from .managed_packages import ManagedPackageStore
from .package_deployment import NativeTarget, PackageDeploymentPlan, PackageDeploymentPlanner


class SkillsQueryService:
    def __init__(
        self,
        read_models: SkillsReadModelService,
        source_fetcher: SourceFetchService,
    ) -> None:
        self.read_models = read_models
        self.source_fetcher = source_fetcher
        self.managed_packages = ManagedPackageStore(read_models.store.root.parent / 'packages')

    def list_managed_packages(self) -> dict[str, object]:
        return {'packages': self.managed_packages.list(), 'skills': self.managed_packages.links()}

    def plan_managed_package(self, package_id: str, target: NativeTarget) -> PackageDeploymentPlan:
        return PackageDeploymentPlanner(self.managed_packages).plan(package_id, target)

    def refresh_managed_package(self, package_id: str) -> dict:
        record = self.managed_packages.get(package_id)
        inventory = self.inventory()
        entry = next((inventory.find(ref) for ref in record['skillRefs'] if inventory.find(ref)), None)
        with TemporaryDirectory(prefix='package-refresh-') as directory:
            if entry is not None:
                resolution = self.resolve_package_source(entry.skill_ref, work_dir=Path(directory))
            else:
                source = PackageSource(**record['source'])
                ref = record['skillRefs'][0] if record['skillRefs'] else package_id
                retained = InventoryEntry(ref, '', '', 'unmanaged', SourceDescriptor(source.kind, source.locator))
                resolution = PackageSourceResolver(self.source_fetcher).resolve(
                    retained, work_dir=Path(directory), authoritative_source=source)
            return self.managed_packages.record_refresh(package_id, resolution, retained_only=entry is None)

    def health(self) -> dict[str, object]:
        snapshot = self.read_models.snapshot()
        return {
            "ok": True,
            "app": "skill-manager",
            "readOnly": False,
            "harnessCount": len(snapshot.harness_scans),
        }

    def list_skills(self) -> dict[str, object]:
        return skills_page_payload(self.inventory())

    def get_skill_detail(self, skill_ref: str) -> dict[str, object] | None:
        inventory = self.inventory()
        entry = inventory.find(skill_ref)
        if entry is None:
            return None
        package_root = self.resolve_detail_package_root(entry)
        payload = skill_detail_payload(
            entry,
            columns=inventory.columns,
            document_markdown=read_skill_document_markdown(package_root) or entry.document_markdown,
            source_links=self.build_source_links(entry),
        )
        payload["sourcePackage"] = self._source_package(entry, self._package_discovery())
        return payload

    def list_source_packages(self) -> dict[str, object]:
        discovery = self._package_discovery()
        packages = {}
        skills = []
        for entry in self.inventory().entries:
            result = self._source_package(entry, discovery)
            package = result.pop("package")
            if package is not None:
                packages[package["id"]] = package
            skills.append({"skillRef": entry.skill_ref, "packageId": package["id"] if package else None, **result})
        return {"packages": list(packages.values()), "skills": skills}

    def resolve_package_source(self, skill_ref: str, *, work_dir: Path) -> PackageResolution:
        """Explicit upstream operation; ordinary inventory/detail reads remain local."""
        resolver = PackageSourceResolver(self.source_fetcher, stop_paths=(self.read_models.store.root,))
        return resolver.resolve(self.require_entry(skill_ref), work_dir=work_dir)

    def has_package_provenance(self, entry: InventoryEntry) -> bool:
        return PackageSourceResolver(self.source_fetcher, stop_paths=(self.read_models.store.root,)).has_package_provenance(entry)

    def _package_discovery(self) -> SourcePackageDiscovery:
        # Fresh per request: source manifests can change outside the inventory cache.
        return SourcePackageDiscovery(stop_paths=(self.read_models.store.root,))

    @staticmethod
    def _source_package(entry: InventoryEntry, discovery: SourcePackageDiscovery) -> dict[str, object]:
        source_path: Path | None = None
        reason = None
        if entry.source.kind == "github":
            reason = "Repository-relative skill provenance has no retained local source checkout."
        elif entry.source_path is not None:
            candidate = Path(entry.source_path)
            if candidate.is_absolute():
                source_path = candidate
            else:
                reason = "Relative source provenance does not establish a local source directory."
        elif entry.kind == "unmanaged" and entry.source.kind != "runtime":
            try:
                candidates = {s.path.resolve() for s in entry.sightings if s.path is not None}
                if len(candidates) == 1:
                    source_path = candidates.pop()
                elif candidates:
                    reason = "Multiple source directories exist; no single source package is proven."
            except (OSError, RuntimeError, ValueError):
                reason = "Source directory cannot be resolved safely."
        else:
            reason = "No original file-backed skill source is available; package ownership is unresolved."
        result = discovery.resolve(source_path) if source_path is not None else {
            "status": "unresolved", "package": None, "reason": reason or "No readable skill source is available.",
        }
        return {**result, "sourceKind": entry.source.kind,
                "sourcePath": str(source_path) if source_path is not None else None,
                "sourceRevision": entry.current_revision}

    def get_skill_source_status(self, skill_ref: str) -> dict[str, object] | None:
        entry = self.inventory().find(skill_ref)
        if entry is None:
            return None
        return source_status_payload(self.resolve_update_status(entry))

    def inventory(self) -> SkillInventory:
        snapshot = self.read_models.snapshot()
        inventory = SkillInventory.from_snapshot(
            store_scan=snapshot.store_scan,
            harness_scans=self.read_models.visible_scans(snapshot),
            runtime_skills=snapshot.runtime_skills,
        )
        links = self.managed_packages.links()
        for entry in inventory.entries:
            link = links.get(entry.skill_ref)
            if link:
                entry.managed_package_id = link.get('packageId')
        entries = list(inventory.entries)
        sort_entries(entries)
        inventory.entries = tuple(entries)
        return inventory

    def require_entry(self, skill_ref: str) -> InventoryEntry:
        entry = self.inventory().find(skill_ref)
        if entry is None:
            raise MutationError(f"unknown skill ref: {skill_ref}", status=404)
        return entry

    def check_for_update(self, entry: InventoryEntry) -> bool | None:
        if not can_update(entry) or entry.current_revision is None:
            return None
        with TemporaryDirectory(prefix="skill-check-") as work_dir:
            try:
                skill_path = self.source_fetcher.fetch(
                    source_kind=entry.source.kind,
                    source_locator=entry.source.locator,
                    work_dir=Path(work_dir),
                )
            except MutationError:
                return None
            fetched_revision, _ = fingerprint_package(skill_path)
            return fetched_revision != entry.current_revision

    def resolve_detail_package_root(self, entry: InventoryEntry) -> Path | None:
        if entry.package_path is not None and _has_skill_document(entry.package_path):
            return entry.package_path

        for sighting in entry.detail_sightings():
            if sighting.path is not None and _has_skill_document(sighting.path):
                return sighting.path
        return None

    def build_source_links(self, entry: InventoryEntry) -> dict[str, str | None] | None:
        if entry.source.kind != "github":
            return None

        repo = github_repo_from_locator(entry.source.locator)
        if repo is None:
            return None

        return {
            "repoLabel": repo,
            "repoUrl": github_repo_url(repo),
            "folderUrl": self._github_folder_url(entry, repo),
        }

    def _github_folder_url(self, entry: InventoryEntry, repo: str) -> str | None:
        if entry.source_ref is not None and entry.source_path is not None:
            return github_folder_url(repo, ref=entry.source_ref, relative_path=entry.source_path)
        if entry.source.locator.removeprefix("github:").count("/") < 2:
            return None
        with TemporaryDirectory(prefix="skill-source-links-") as work_dir:
            try:
                fetched = self.source_fetcher.fetch_package(
                    source_kind=entry.source.kind,
                    source_locator=entry.source.locator,
                    work_dir=Path(work_dir),
                )
            except MutationError:
                return None
        return github_folder_url(repo, ref=fetched.source_ref, relative_path=fetched.source_path)

    def resolve_update_status(
        self,
        entry: InventoryEntry,
    ) -> Literal["update_available", "no_update_available", "no_source_available", "local_changes_detected"] | None:
        if entry.kind != "managed":
            return None
        if has_local_changes(entry):
            return "local_changes_detected"
        if not can_update(entry):
            return "no_source_available"
        if self.check_for_update(entry):
            return "update_available"
        return "no_update_available"

    def can_stop_managing(self, entry: InventoryEntry) -> bool:
        return can_stop_managing(entry)

    def get_skill_path(self, skill_ref: str) -> Path | None:
        entry = self.inventory().find(skill_ref)
        if entry is None:
            return None
        return self.resolve_detail_package_root(entry)


def _has_skill_document(package_root: Path) -> bool:
    try:
        return (package_root / "SKILL.md").is_file()
    except (OSError, RuntimeError, ValueError):
        return False
