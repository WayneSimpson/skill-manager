from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
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
from .package_deployment_service import PackageDeploymentError, PackageDeploymentService
from .native_package_runtime import PACKAGE_DEPLOYMENT_HARNESSES


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

    def get_package_context(self, skill_ref: str) -> dict[str, object] | None:
        """Return package context from local facts and retained state only."""
        entry = self.inventory().find(skill_ref)
        if entry is None:
            return None
        observation = self._source_package(entry, self._package_discovery())
        link = self.managed_packages.links().get(skill_ref)
        managed_package = None
        resolution = None
        if link is not None:
            if link.get("packageId"):
                try:
                    managed_package = self.managed_packages.get(link["packageId"])
                except ValueError:
                    managed_package = None
            resolution = _stored_resolution(link, managed_package)
        package_backed = bool(
            link is not None
            or observation.get("package") is not None
            or self.has_package_provenance(entry)
        )
        if package_backed and resolution is None:
            resolution = _unresolved_resolution(
                "The local package was identified, but its authoritative source has not been resolved."
            )
        return {
            "packageBacked": package_backed,
            "observation": observation,
            "resolution": resolution,
            "managedPackage": managed_package,
        }

    def resolve_package_context(self, skill_ref: str) -> dict[str, object] | None:
        """Resolve upstream source in a temporary workspace and omit that path."""
        entry = self.inventory().find(skill_ref)
        if entry is None:
            return None
        with TemporaryDirectory(prefix="package-review-") as directory:
            resolution = self.resolve_package_source(skill_ref, work_dir=Path(directory))
            context = self.get_package_context(skill_ref)
            if context is None:
                return None
            context["resolution"] = _resolution_payload(resolution)
            context["packageBacked"] = True
            return context

    def get_package_deployments(self, package_id: str, adapter_factory) -> dict[str, object]:
        """Reconcile native state without changing it."""
        self.managed_packages.get(package_id)
        service = PackageDeploymentService(self.managed_packages)
        active = [
            record for record in service.list_deployments().values()
            if record.get("managedPackageId") == package_id
        ]
        return {
            "packageId": package_id,
            "harnesses": [
                self._deployment_view(
                    package_id,
                    harness,
                    _deployment_for_harness(active, harness),
                    service,
                    adapter_factory,
                )
                for harness in PACKAGE_DEPLOYMENT_HARNESSES
            ],
        }

    def mutate_package_deployment(
        self,
        package_id: str,
        harness: str,
        action: str,
        adapter_factory,
        *,
        replacement_package_id: str | None = None,
    ) -> dict[str, object]:
        """Execute a validated native action and return freshly reconciled state."""
        self.managed_packages.get(package_id)
        service = PackageDeploymentService(self.managed_packages)
        records = [
            record for record in service.list_deployments().values()
            if record.get("managedPackageId") == package_id and record.get("harness") == harness
        ]
        if len(records) > 1:
            raise PackageDeploymentError("Multiple deployments exist for this package and harness.")
        deployment = records[0] if records else None
        response_package_id = package_id
        if action not in {"deploy", "update", "enable", "disable", "remove"}:
            raise PackageDeploymentError("Unsupported package deployment action.")
        if action == "update":
            if deployment is None:
                raise PackageDeploymentError("Update requires an existing deployment.")
            if replacement_package_id is None:
                raise PackageDeploymentError("Update requires an explicit replacement package ID.")
            self.managed_packages.get(replacement_package_id)

        try:
            adapter = adapter_factory(harness)
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise PackageDeploymentError("Native adapter is unavailable.") from error
        if action == "deploy":
            service.deploy(package_id, adapter, deployment_id=deployment["deploymentId"] if deployment else None)
        elif action == "update":
            if replacement_package_id != package_id and replacement_package_id not in {
                option['packageId'] for option in self._replacement_options(package_id, deployment, adapter)
            }:
                raise PackageDeploymentError('Replacement is not a currently verified source snapshot.')
            service.update(deployment["deploymentId"], replacement_package_id, adapter)
            response_package_id = replacement_package_id
        elif action in {"enable", "disable"}:
            if deployment is None:
                raise PackageDeploymentError(f"{action.title()} requires an existing deployment.")
            getattr(service, action)(deployment["deploymentId"], adapter)
        elif action == "remove":
            if deployment is None:
                raise PackageDeploymentError("Remove requires an existing deployment.")
            service.remove(deployment["deploymentId"], adapter)
        self.read_models.invalidate()
        return self.get_package_deployments(response_package_id, adapter_factory)

    def _deployment_view(self, package_id, harness, deployment, service, adapter_factory):
        adapter = None
        try:
            adapter = adapter_factory(harness)
            if deployment is not None:
                result = service.reconcile(deployment["deploymentId"], adapter)
                plan = result["plan"]
                deployment = {**deployment, 'enabled': result['verification']['enabled']}
                state = _deployment_state(plan, deployment, result["state"])
            else:
                plan = self.plan_managed_package(package_id, adapter.inspect(package_id))
                state = _deployment_state(plan, None, None)
        except PackageDeploymentError as error:
            plan = error.plan
            state = _deployment_state(plan, deployment, None)
        except (OSError, RuntimeError, ValueError, TypeError):
            plan = None
            state = "conflict" if deployment is not None else "manual"

        diagnostics = list(getattr(adapter, "diagnostics", ())) if adapter is not None else []
        factory_diagnostics = getattr(adapter_factory, "diagnostics", None)
        if callable(factory_diagnostics):
            try:
                diagnostics.extend(factory_diagnostics(harness))
            except (OSError, RuntimeError, ValueError, TypeError):
                diagnostics.append("native-adapter-diagnostics-unavailable")
        diagnostics = tuple(dict.fromkeys(diagnostics))
        if plan is None:
            return _deployment_payload(
                harness=harness,
                state=state,
                support="manual",
                ownership="conflict" if deployment is not None else "absent",
                strategy="manual/unsupported",
                selected_package_id=deployment.get("selectedPackageId") if deployment else None,
                deployment_id=deployment.get("deploymentId") if deployment else None,
                enabled=None,
                blockers=("native-inspection-failed",),
                preflight=diagnostics or ("Native package preflight could not be completed.",),
                actions=(),
            )
        blockers = tuple(dict.fromkeys((*plan.blockers, *diagnostics)))
        payload = _deployment_payload(
            harness=harness,
            state=state,
            support=plan.support,
            ownership=plan.ownership,
            strategy=plan.strategy,
            selected_package_id=plan.selected_package_id or (deployment.get("selectedPackageId") if deployment else None),
            deployment_id=deployment.get("deploymentId") if deployment else None,
            enabled=deployment.get("enabled") if deployment and state in {'installed', 'enabled', 'disabled'} else None,
            blockers=blockers,
            preflight=_preflight(plan, diagnostics),
            actions=_actions(plan, deployment, state),
        )
        selected = self.managed_packages.get(plan.selected_package_id) if plan.selected_package_id else None
        payload['source'] = selected['source'] if selected else None
        if deployment is not None and payload['actions'] and not blockers:
            payload['replacementOptions'] = self._replacement_options(package_id, deployment, adapter)
            if payload['replacementOptions']:
                payload['actions'].append('update')
        if blockers:
            payload['actions'] = []
        return payload

    def _replacement_options(self, package_id, deployment, adapter):
        source = self.managed_packages.get(package_id)['source']
        options = []
        for candidate in self.managed_packages.list():
            if candidate['id'] == package_id or _source_coordinate(candidate['source']) != _source_coordinate(source):
                continue
            target = adapter.inspect(candidate['id'], deployment)
            plan = PackageDeploymentPlanner(self.managed_packages).plan(
                candidate['id'], target, owned_deployment=deployment)
            if plan.support == 'supported' and plan.ownership == 'managed' and not plan.blockers and plan.actions:
                options.append({'packageId': candidate['id'], 'source': candidate['source']})
        return options

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


def _source_coordinate(source: dict) -> tuple:
    # npm locators include the pin; retain the explicit registry name, not a display name.
    locator = source['locator'].rsplit('@', 1)[0] if source['kind'] == 'npm' else source['locator']
    return source['kind'], locator, source.get('package_path')


def _resolution_payload(resolution: PackageResolution) -> dict[str, object]:
    return {
        "status": resolution.status,
        "source": asdict(resolution.source) if resolution.source is not None else None,
        "reason": resolution.reason,
        "limitations": list(resolution.limitations),
        "evidence": [asdict(item) for item in resolution.evidence],
    }


def _unresolved_resolution(reason: str) -> dict[str, object]:
    return {"status": "unresolved", "source": None, "reason": reason, "limitations": [], "evidence": []}


def _stored_resolution(link: dict, package: dict | None) -> dict[str, object] | None:
    if package is not None:
        return {
            "status": package["resolutionStatus"],
            "source": deepcopy(package.get("source")),
            "reason": package.get("reason"),
            "limitations": list(package.get("limitations") or []),
            "evidence": deepcopy(package.get("evidence") or []),
        }
    status = link.get("resolutionStatus")
    if status not in {"resolved", "unresolved", "ambiguous", "unavailable"}:
        return None
    return {"status": status, "source": None, "reason": link.get("reason"), "limitations": [], "evidence": []}


def _deployment_for_harness(records: list[dict], harness: str) -> dict | None:
    matches = [record for record in records if record.get('harness') == harness]
    if len(matches) > 1:
        raise PackageDeploymentError('Multiple deployments exist for this package and harness.')
    return matches[0] if matches else None


def _deployment_state(plan, deployment: dict | None, reconcile_state: str | None) -> str:
    if plan is None:
        return "conflict" if deployment is not None else "manual"
    if plan.ownership == "external-existing":
        return "external-existing"
    if _has_stale_blocker(plan.blockers):
        return "stale"
    if set(plan.blockers) <= {'native-inventory-incomplete', 'native-version-or-policy-unverified',
                              'no-proven-native-format', 'opencode-native-intent-unproven'} and plan.blockers:
        return 'manual'
    if deployment is not None:
        if reconcile_state == "managed" and plan.ownership == "managed":
            if deployment.get("enabled") is True:
                return "enabled"
            if deployment.get("enabled") is False:
                return "disabled"
            return "installed"
        return "conflict"
    if plan.ownership == "conflict":
        return "conflict"
    if plan.support == "unsupported":
        return "unsupported"
    if plan.blockers or plan.support == "manual":
        return "manual"
    return "absent"


def _has_stale_blocker(blockers: list[str]) -> bool:
    return any(
        blocker.startswith(("package-artifact-", "package-upstream-", "package-source-", "distribution-"))
        or blocker.endswith("candidate-source-pending")
        for blocker in blockers
    )


def _actions(plan, deployment: dict | None, state: str) -> tuple[str, ...]:
    if plan.blockers or plan.support != "supported" or not plan.actions:
        return ()
    if deployment is None and state == "absent" and plan.ownership == "absent":
        return ("deploy",)
    if deployment is not None and state in {"installed", "enabled", "disabled"} and plan.ownership == "managed":
        toggle = "disable" if deployment.get("enabled") is True else "enable"
        return (toggle, "remove")
    return ()


def _preflight(plan, diagnostics: tuple[str, ...]) -> tuple[str, ...]:
    if plan.blockers:
        return tuple(dict.fromkeys((*diagnostics, "Blocked: " + ", ".join(plan.blockers))))
    values = list(diagnostics)
    if plan.actions:
        values.append("Fresh native inventory and deployment checks passed.")
    elif plan.rationale:
        values.append(plan.rationale)
    else:
        values.append("Native package deployment requires additional harness verification.")
    return tuple(dict.fromkeys(values))


def _deployment_payload(
    *,
    harness: str,
    state: str,
    support: str,
    ownership: str,
    strategy: str,
    selected_package_id: str | None,
    deployment_id: str | None,
    enabled: bool | None,
    blockers: tuple[str, ...],
    preflight: tuple[str, ...],
    actions: tuple[str, ...],
) -> dict[str, object]:
    return {
        "harness": harness,
        "state": state,
        "support": support,
        "ownership": ownership,
        "strategy": strategy,
        "selectedPackageId": selected_package_id,
        "deploymentId": deployment_id,
        "enabled": enabled,
        "blockers": list(blockers),
        "actions": list(actions),
        "preflight": list(preflight),
        'source': None,
        'replacementOptions': [],
    }
