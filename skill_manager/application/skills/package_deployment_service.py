"""Small, ownership-aware mutation boundary for native local packages."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from skill_manager.atomic_files import atomic_write_text, file_lock

from .managed_packages import ManagedPackageStore, package_fingerprint
from .package_deployment import NativeTarget, PackageDeploymentPlan, PackageDeploymentPlanner


class NativePackageAdapter(Protocol):
    """The intentionally small boundary around a live native harness."""

    harness: str

    def inspect(self, package_id: str, deployment: dict | None = None) -> NativeTarget:
        """Return fresh native facts without changing the harness."""

    def verify(self, plan: PackageDeploymentPlan, expected: str) -> bool | Mapping[str, Any]:
        """Verify the requested post-operation state in the real harness."""


class PackageDeploymentError(RuntimeError):
    def __init__(self, message: str, *, plan: PackageDeploymentPlan | None = None) -> None:
        super().__init__(message)
        self.plan = plan


class PackageDeploymentService:
    """Deploy only fresh, verified, whole local snapshots.

    The deployment manifest is deliberately not another package model. It stores
    only enough ownership information to prove and mutate a target later.
    """

    def __init__(
        self,
        packages: ManagedPackageStore,
        planner: PackageDeploymentPlanner | None = None,
        *,
        state_path: Path | None = None,
    ) -> None:
        self.packages = packages
        self.planner = planner or PackageDeploymentPlanner(packages)
        self.state_path = state_path or packages.root / 'deployments.json'
        self._state_version = 1

    def list_deployments(self) -> dict[str, dict]:
        return deepcopy(self._load_state()['deployments'])

    def reconcile(self, deployment_id, adapter):
        """Read current ownership; stored verification is only the last successful check."""
        with self._lock():
            existing = self._existing(self._load_state(), deployment_id)
            _, plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            verified = (plan.support == 'supported' and plan.ownership == 'managed'
                        and self._verify(adapter, plan, 'present')['verified'])
            return {'deployment': existing, 'plan': plan, 'state': 'managed' if verified else 'conflict'}

    def _lock(self):
        if not self.packages.root.is_dir() or not _safe_path(self.packages.lock) or not _safe_path(self.state_path):
            raise PackageDeploymentError('Package/deployment storage is unsafe or unavailable.')
        return file_lock(self.packages.lock)

    def deploy(
        self,
        package_id: str,
        adapter: NativePackageAdapter,
        *,
        deployment_id: str | None = None,
    ) -> dict:
        with self._lock():
            state = self._load_state()
            existing = self._existing(state, deployment_id)
            if deployment_id is None:
                observed = adapter.inspect(package_id)
                matches = [r for r in state['deployments'].values() if r['managedPackageId'] == package_id
                           and r['harness'] == observed.harness and r['root'] == str(observed.root)]
                if len(matches) > 1:
                    raise PackageDeploymentError('Ambiguous deployment ownership.')
                existing = matches[0] if matches else None
            if existing is not None and existing['managedPackageId'] != package_id:
                raise PackageDeploymentError(
                    'Deployment already exists; use update with an explicit new package ID.'
                )
            target, plan = self._fresh_plan(package_id, adapter, existing)
            self._require_mutable_local(plan, managed=existing is not None)
            if existing is not None:
                if plan.ownership != 'managed':
                    raise PackageDeploymentError('Existing deployment ownership cannot be proved.', plan=plan)
                return self._verified_repeat(state, existing, adapter, plan)
            if plan.ownership != 'absent':
                raise PackageDeploymentError('Native target is not absent and owned by this operation.', plan=plan)

            source, fingerprint = self._selected_artifact(plan)
            destination = self._planned_destination(plan)
            stage, created_parents, target_fingerprint, staged_identity = self._prepare_stage(source, fingerprint, destination, target.root, plan)
            native_started = False
            try:
                # Re-read the live facts after staging, immediately before the
                # target mutation. A staging directory is never ownership proof.
                checked_target, checked_plan = self._fresh_plan(package_id, adapter, None)
                self._require_mutable_local(checked_plan, managed=False)
                self._same_target(plan, checked_plan, checked_target)
                if checked_plan.selected_package_id != plan.selected_package_id or checked_plan.fingerprint != fingerprint:
                    raise PackageDeploymentError('Selected snapshot changed while staging.', plan=checked_plan)
                self._check_stage(stage, target_fingerprint, staged_identity)
                self._move_new_snapshot(stage, destination)
                try:
                    if checked_plan.target_harness == 'codex':
                        native_started = True
                        adapter.install(checked_plan)
                    verification = self._verify(adapter, checked_plan, 'present')
                    if package_fingerprint(destination) != target_fingerprint or _identity(destination) != staged_identity:
                        raise PackageDeploymentError('Placed target changed during verification.', plan=checked_plan)
                except Exception:
                    if not native_started:
                        self._rollback_created(destination, target_fingerprint, created_parents, staged_identity)
                    raise
                if not verification['verified']:
                    if not native_started:
                        self._rollback_created(destination, target_fingerprint, created_parents, staged_identity)
                    raise PackageDeploymentError('Native verification failed.', plan=checked_plan)
                record = self._new_record(
                    deployment_id or uuid4().hex,
                    package_id,
                    checked_plan,
                    verification,
                    checked_target.root,
                )
                state['deployments'][record['deploymentId']] = record
                try:
                    self._save_state(state)
                except BaseException:
                    if not native_started:
                        self._rollback_created(destination, target_fingerprint, created_parents, staged_identity)
                    raise
            except BaseException:
                self._remove_stage(stage, target_fingerprint, staged_identity)
                self._remove_empty_directories(created_parents)
                raise
            return {'ok': True, 'changed': True, 'deployment': deepcopy(record), 'plan': checked_plan}

    def update(self, deployment_id: str, package_id: str, adapter: NativePackageAdapter) -> dict:
        with self._lock():
            state = self._load_state()
            existing = self._existing(state, deployment_id)
            if existing is None:
                raise PackageDeploymentError('Unknown deployment ID; update requires an existing deployment.')
            if existing['managedPackageId'] == package_id:
                target, plan = self._fresh_plan(package_id, adapter, existing)
                self._require_mutable_local(plan, managed=True)
                if plan.ownership != 'managed':
                    raise PackageDeploymentError('Existing deployment ownership cannot be proved.', plan=plan)
                return self._verified_repeat(state, existing, adapter, plan)

            # Re-plan both sides: the old package proves current ownership and
            # health; the explicit new immutable ID supplies the replacement.
            _current_target, current_plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            self._require_mutable_local(current_plan, managed=True)
            if current_plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment ownership cannot be proved.', plan=current_plan)
            new_target, plan = self._fresh_plan(package_id, adapter, existing)
            self._require_mutable_local(plan, managed=True)
            if plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment ownership cannot be proved for update.', plan=plan)
            self._same_target(current_plan, plan, new_target)

            source, fingerprint = self._selected_artifact(plan)
            destination = self._planned_destination(plan)
            old_fingerprint = existing.get('targetFingerprint', existing['appliedFingerprint'])
            stage, _created_parents, target_fingerprint, staged_identity = self._prepare_stage(source, fingerprint, destination, new_target.root, plan)
            backup: Path | None = None
            cleanup_warning = None
            try:
                # Do not replace a target whose ownership changed while the
                # replacement snapshot was being staged.
                checked_target, checked_current = self._fresh_plan(existing['managedPackageId'], adapter, existing)
                self._require_mutable_local(checked_current, managed=True)
                if checked_current.ownership != 'managed':
                    raise PackageDeploymentError('Current deployment changed while staging.', plan=checked_current)
                self._same_target(current_plan, checked_current, checked_target)
                newest_target, newest_plan = self._fresh_plan(package_id, adapter, existing)
                self._require_mutable_local(newest_plan, managed=True)
                self._same_target(plan, newest_plan, newest_target)
                if newest_plan.fingerprint != fingerprint or newest_plan.selected_package_id != plan.selected_package_id:
                    raise PackageDeploymentError('Replacement changed while staging.', plan=newest_plan)
                self._prove_target(destination, existing)
                # Native cache provenance is verified before replacing the owned source tree.
                before_native = self._verify(adapter, checked_current, 'present')
                if not before_native['verified']:
                    raise PackageDeploymentError('Native state changed before update.', plan=checked_current)
                self._check_stage(stage, target_fingerprint, staged_identity)
                backup = stage.parent / f'backup-{uuid4().hex}'
                destination.rename(backup)
                try:
                    stage.rename(destination)
                except BaseException:
                    self._restore_backup(destination, backup, old_fingerprint, existing['targetIdentity'])
                    raise
                try:
                    if plan.target_harness == 'codex':
                        adapter.reinstall(plan)
                        if before_native['enabled'] is False:
                            adapter.set_enabled(plan, False)
                    verification = self._verify(adapter, plan, 'present')
                    if package_fingerprint(destination) != target_fingerprint or _identity(destination) != staged_identity:
                        raise PackageDeploymentError('Replacement changed during verification.', plan=plan)
                except Exception:
                    if plan.target_harness != 'codex':
                        self._rollback_replacement(destination, backup, target_fingerprint, old_fingerprint, staged_identity, existing['targetIdentity'])
                    raise
                if not verification['verified']:
                    if plan.target_harness != 'codex':
                        self._rollback_replacement(destination, backup, target_fingerprint, old_fingerprint, staged_identity, existing['targetIdentity'])
                    raise PackageDeploymentError('Native verification failed during update.', plan=plan)
                updated = self._new_record(deployment_id, package_id, plan, verification, new_target.root)
                state['deployments'][deployment_id] = updated
                try:
                    self._save_state(state)
                except BaseException:
                    if plan.target_harness != 'codex':
                        self._rollback_replacement(destination, backup, target_fingerprint, old_fingerprint, staged_identity, existing['targetIdentity'])
                    raise
                # The state now claims the replacement. If an old backup
                # cannot be removed safely, leave it for manual inspection.
                try:
                    self._remove_owned_tree(backup, old_fingerprint, existing['targetIdentity'])
                except (PackageDeploymentError, OSError, ValueError, RuntimeError):
                    cleanup_warning = 'Backup retained for manual inspection: ' + str(backup)
            except BaseException:
                self._remove_stage(stage, target_fingerprint, staged_identity)
                raise
            return {'ok': True, 'changed': True, 'deployment': deepcopy(updated), 'plan': plan,
                    **({'warning': cleanup_warning} if cleanup_warning else {})}

    def remove(self, deployment_id: str, adapter: NativePackageAdapter) -> dict:
        with self._lock():
            state = self._load_state()
            if deployment_id not in state['deployments'] and deployment_id in state.get('removed', {}):
                previous = state['removed'][deployment_id]
                self._verify_removed(previous, adapter)
                return {'ok': True, 'changed': False, 'deploymentId': deployment_id, 'verified': True}
            existing = self._existing(state, deployment_id)
            if existing is None:
                raise PackageDeploymentError('Unknown deployment ID; remove requires an existing deployment.')
            if not Path(existing['target']).exists() and not Path(existing['target']).is_symlink():
                # Recover only the final state write of an already absent deployment.
                self._verify_removed(existing, adapter)
                self._record_removed(state, deployment_id, existing)
                return {'ok': True, 'changed': True, 'deploymentId': deployment_id, 'verified': True}
            _target, plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            self._require_mutable_local(plan, managed=True)
            if plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment ownership cannot be proved.', plan=plan)
            self._prove_target(self._planned_destination(plan), existing)
            before = self._verify(adapter, plan, 'present')
            if not before['verified']:
                raise PackageDeploymentError('Native verification failed before remove.', plan=plan)
            # Fresh facts immediately precede the only target mutation.
            checked_target, checked_plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            self._require_mutable_local(checked_plan, managed=True)
            if checked_plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment changed before remove.', plan=checked_plan)
            self._same_target(plan, checked_plan, checked_target)
            destination = self._planned_destination(checked_plan)
            self._prove_target(destination, existing)
            if plan.target_harness == 'codex':
                adapter.uninstall(checked_plan)
            shutil.rmtree(destination)
            verification = self._verify(adapter, checked_plan, 'absent')
            if not verification['verified']:
                raise PackageDeploymentError('Native verification failed after remove.', plan=checked_plan)
            self._record_removed(state, deployment_id, existing)
            return {'ok': True, 'changed': True, 'deploymentId': deployment_id, 'verified': True}

    def _verify_removed(self, previous, adapter):
        target, plan = self._fresh_plan(previous['managedPackageId'], adapter, None)
        self._require_mutable_local(plan, managed=False)
        if (target.harness != previous['harness'] or str(target.root) != previous['root']
            or Path(previous['target']).exists() or Path(previous['target']).is_symlink()
            or previous['nativeId'] in target.occupied_identifiers
            or any(r.native_id == previous['nativeId'] for r in target.registrations)):
            raise PackageDeploymentError('Removed target has been replaced; not owned.', plan=plan)
        self.planner._apply_owned_target(plan, target, previous)
        if plan.blockers or not self._verify(adapter, plan, 'absent')['verified']:
            raise PackageDeploymentError('Removal cannot be reconciled.', plan=plan)

    def _record_removed(self, state, deployment_id, existing):
        del state['deployments'][deployment_id]
        state.setdefault('removed', {})[deployment_id] = {key: existing[key] for key in (
            'managedPackageId', 'selectedPackageId', 'placementPackageId', 'harness', 'root', 'target', 'nativeId', 'strategy')}
        self._save_state(state)

    def enable(self, deployment_id: str, adapter: NativePackageAdapter) -> dict:
        return self._set_enabled(deployment_id, True, adapter)

    def disable(self, deployment_id: str, adapter: NativePackageAdapter) -> dict:
        return self._set_enabled(deployment_id, False, adapter)

    def _set_enabled(self, deployment_id: str, enabled: bool, adapter: NativePackageAdapter) -> dict:
        with self._lock():
            state = self._load_state()
            existing = self._existing(state, deployment_id)
            if existing is None:
                raise PackageDeploymentError('Unknown deployment ID; enable/disable requires an existing deployment.')
            target, plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            self._require_mutable_local(plan, managed=True)
            if plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment ownership cannot be proved.', plan=plan)
            current = self._verify(adapter, plan, 'present')
            if not current['verified']:
                raise PackageDeploymentError('Native verification failed before enable/disable.', plan=plan)
            if current['enabled'] is enabled:
                updated = deepcopy(existing)
                updated.update(verified=True, enabled=enabled)
                if updated != existing:
                    state['deployments'][deployment_id] = updated
                    self._save_state(state)
                return {'ok': True, 'changed': False, 'deployment': updated, 'plan': plan}
            setter = getattr(adapter, 'set_enabled', None)
            if not callable(setter):
                raise PackageDeploymentError('This native adapter has no verified enable/disable control.', plan=plan)
            checked_target, checked_plan = self._fresh_plan(existing['managedPackageId'], adapter, existing)
            self._require_mutable_local(checked_plan, managed=True)
            if checked_plan.ownership != 'managed':
                raise PackageDeploymentError('Current deployment changed before enable/disable.', plan=checked_plan)
            self._same_target(plan, checked_plan, checked_target)
            self._prove_target(self._planned_destination(checked_plan), existing)
            try:
                setter(checked_plan, enabled)
            except (NotImplementedError, AttributeError) as error:
                raise PackageDeploymentError('This native adapter has no verified enable/disable control.', plan=checked_plan) from error
            verification = self._verify(adapter, checked_plan, 'enabled' if enabled else 'disabled')
            if not verification['verified']:
                raise PackageDeploymentError('Native enable/disable verification failed.', plan=checked_plan)
            updated = deepcopy(existing)
            updated.update(verified=True, enabled=verification['enabled'])
            state['deployments'][deployment_id] = updated
            self._save_state(state)
            return {'ok': True, 'changed': True, 'deployment': updated, 'plan': checked_plan}

    def _fresh_plan(self, package_id: str, adapter: NativePackageAdapter, existing: dict | None):
        try:
            target = adapter.inspect(package_id, deepcopy(existing) if existing is not None else None)
        except Exception as error:  # noqa: BLE001 - adapter is an external boundary
            raise PackageDeploymentError(f'Native inspection failed: {error}') from error
        if not isinstance(target, NativeTarget):
            raise PackageDeploymentError('Native adapter returned an invalid target.')
        if existing is not None and target.harness != existing['harness']:
            raise PackageDeploymentError('Native adapter returned a different harness.')
        plan = self.planner.plan(package_id, target, owned_deployment=existing)
        return target, plan

    @staticmethod
    def _require_mutable_local(plan: PackageDeploymentPlan, *, managed: bool) -> None:
        supported_route = (plan.strategy == 'native-local' and plan.target_harness in ('claude', 'cursor')) or (plan.strategy == 'native-install' and plan.target_harness == 'codex')
        if plan.blockers or not plan.actions or plan.support != 'supported' or not supported_route:
            reason = ', '.join(plan.blockers) or 'manual-native-strategy'
            raise PackageDeploymentError(f'Native package deployment is blocked: {reason}', plan=plan)
        if managed and plan.ownership != 'managed':
            raise PackageDeploymentError('Managed target ownership is not proven.', plan=plan)

    def _selected_artifact(self, plan: PackageDeploymentPlan) -> tuple[Path, str]:
        if plan.selected_package_id is None or plan.fingerprint is None:
            raise PackageDeploymentError('Planned package has no retained artifact.', plan=plan)
        selected = self.packages.get(plan.selected_package_id)
        if selected['artifactState'] != 'current' or selected['artifactRoot'] is None:
            raise PackageDeploymentError('Planned package artifact is not current.', plan=plan)
        if selected['fingerprint'] != plan.fingerprint:
            raise PackageDeploymentError('Planned package changed before deployment.', plan=plan)
        return Path(selected['artifactRoot']), selected['fingerprint']

    @staticmethod
    def _planned_destination(plan: PackageDeploymentPlan) -> Path:
        value = plan.surface.get('path')
        if not value:
            raise PackageDeploymentError('Native-local plan has no exact target path.', plan=plan)
        return Path(value)

    def _stage_snapshot(self, source: Path, fingerprint: str, destination: Path, root: Path) -> tuple[Path, list[Path]]:
        if not source.is_dir() or source.is_symlink() or not _safe_path(source):
            raise PackageDeploymentError('Managed package artifact is not a safe directory.')
        if package_fingerprint(source) != fingerprint:
            raise PackageDeploymentError('Managed package artifact changed before copying.')
        if not _safe_path(destination) or destination.is_symlink():
            raise PackageDeploymentError('Native destination is unsafe.')
        created_parents = self._make_parent_directories(destination.parent)
        # Complete packages in loader directories are discoverable even before promotion.
        staging = root / '.skill-manager-staging'
        created_parents[0:0] = self._make_parent_directories(staging)
        stage = staging / uuid4().hex
        stage_identity = None
        try:
            stage.mkdir()
            stage_identity = stage.stat().st_ino
            for root, directories, files in os.walk(source, followlinks=False):
                root_path = Path(root)
                relative_root = root_path.relative_to(source)
                target_root = stage / relative_root
                target_root.mkdir(parents=True, exist_ok=True)
                for name in sorted(directories):
                    path = root_path / name
                    if path.is_symlink():
                        raise PackageDeploymentError('Managed package contains a symlink.')
                    (target_root / name).mkdir()
                for name in sorted(files):
                    path = root_path / name
                    if path.is_symlink() or not path.is_file():
                        raise PackageDeploymentError('Managed package contains an unsafe file.')
                    copied = target_root / name
                    shutil.copyfile(path, copied, follow_symlinks=False)
                    copied.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
            if package_fingerprint(stage) != fingerprint:
                raise PackageDeploymentError('Staged package fingerprint does not match the plan.')
        except BaseException:
            if stage_identity is not None and _safe_path(stage) and stage.is_dir() and stage.stat().st_ino == stage_identity:
                shutil.rmtree(stage)
            self._remove_empty_directories(created_parents)
            raise
        return stage, created_parents

    def _prepare_stage(self, source, fingerprint, destination, root, plan):
        stage, parents = self._stage_snapshot(source, fingerprint, destination, root)
        identity = _identity(stage)
        try:
            stage = self._stage_marketplace(stage, plan)
            identity = _identity(stage)
            return stage, parents, package_fingerprint(stage), identity
        except BaseException:
            if _safe_path(stage) and stage.is_dir() and _identity(stage) == identity:
                shutil.rmtree(stage)
            self._remove_empty_directories(parents)
            raise

    @staticmethod
    def _stage_marketplace(stage, plan):
        if plan.target_harness != 'codex':
            return stage
        wrapper = stage.parent / uuid4().hex
        relative = Path(plan.surface['wholePackagePath']).relative_to(plan.surface['path'])
        created = False
        try:
            wrapper.mkdir()
            created = True
            (wrapper / relative).parent.mkdir(parents=True)
            stage.rename(wrapper / relative)
            catalog = wrapper / '.agents/plugins/marketplace.json'
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps(plan.surface['marketplaceDocument'], indent=2) + '\n')
            return wrapper
        except BaseException:
            for path in (stage, wrapper) if created else (stage,):
                if _safe_path(path) and path.is_dir():
                    shutil.rmtree(path)
            raise

    @staticmethod
    def _check_stage(stage, fingerprint, identity):
        if _identity(stage) != identity or package_fingerprint(stage) != fingerprint:
            raise PackageDeploymentError('Staged directory changed; refusing promotion.')

    @staticmethod
    def _move_new_snapshot(stage: Path, destination: Path) -> None:
        if destination.exists() or destination.is_symlink():
            raise PackageDeploymentError('Native destination became occupied while staging.')
        stage.rename(destination)

    @staticmethod
    def _verify(adapter: NativePackageAdapter, plan: PackageDeploymentPlan, expected: str) -> dict:
        try:
            result = adapter.verify(plan, expected)
        except Exception as error:  # noqa: BLE001 - adapter is an external boundary
            raise PackageDeploymentError(f'Native verification failed: {error}', plan=plan) from error
        if isinstance(result, Mapping):
            if (type(result.get('verified')) is not bool or
                    (result.get('enabled') is not None and type(result['enabled']) is not bool)):
                raise PackageDeploymentError('Malformed native verification result.', plan=plan)
            return {
                'verified': result['verified'],
                'enabled': result.get('enabled'),
                **({'nativeFingerprint': result['nativeFingerprint']} if result.get('nativeFingerprint') else {}),
            }
        if type(result) is not bool:
            raise PackageDeploymentError('Malformed native verification result.', plan=plan)
        return {'verified': result, 'enabled': None}

    @staticmethod
    def _new_record(
        deployment_id: str,
        package_id: str,
        plan: PackageDeploymentPlan,
        verification: dict,
        root: Path,
    ) -> dict:
        info = Path(plan.surface['path']).stat()
        return {
            'deploymentId': deployment_id,
            'managedPackageId': package_id,
            'selectedPackageId': plan.selected_package_id,
            'harness': plan.target_harness,
            'root': str(root),
            'target': str(plan.surface['path']),
            'nativeId': plan.surface['nativeId'],
            'strategy': plan.strategy,
            'appliedFingerprint': plan.fingerprint,
            'targetFingerprint': package_fingerprint(Path(plan.surface['path'])),
            'targetIdentity': [info.st_dev, info.st_ino],
            'placementPackageId': plan.surface.get('placementPackageId', plan.selected_package_id),
            'verifiedAt': datetime.now(timezone.utc).isoformat(),
            **({'nativeFingerprint': verification['nativeFingerprint']} if verification.get('nativeFingerprint') else {}),
            'verified': bool(verification['verified']),
            'enabled': verification['enabled'],
        }

    @staticmethod
    def _existing(state: dict, deployment_id: str | None) -> dict | None:
        if deployment_id is None:
            return None
        value = state['deployments'].get(deployment_id)
        if value is None:
            raise PackageDeploymentError('Unknown deployment ID.')
        return deepcopy(value)

    def _verified_repeat(self, state: dict, existing: dict, adapter: NativePackageAdapter, plan: PackageDeploymentPlan) -> dict:
        verification = self._verify(adapter, plan, 'present')
        if not verification['verified']:
            raise PackageDeploymentError('Native verification failed for existing deployment.', plan=plan)
        updated = deepcopy(existing)
        updated.update(
            verified=True,
            enabled=verification['enabled'] if verification['enabled'] is not None else existing['enabled'],
        )
        if updated != existing:
            state['deployments'][existing['deploymentId']] = updated
            self._save_state(state)
        return {'ok': True, 'changed': False, 'deployment': updated, 'plan': plan}

    def _load_state(self) -> dict:
        if not _safe_path(self.state_path) or not _safe_path(self.packages.lock):
            raise PackageDeploymentError('Deployment state path is a symlink.')
        if not self.state_path.exists():
            return {'version': self._state_version, 'deployments': {}}
        try:
            state = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError) as error:
            raise PackageDeploymentError('Deployment state cannot be read safely.') from error
        if not isinstance(state, dict) or state.get('version') != self._state_version or not isinstance(state.get('deployments'), dict):
            raise PackageDeploymentError('Unsupported deployment state.')
        return state

    def _save_state(self, state: dict) -> None:
        if not _safe_path(self.state_path):
            raise PackageDeploymentError('Deployment state path is a symlink.')
        atomic_write_text(self.state_path, json.dumps(state, indent=2, ensure_ascii=False) + '\n')

    @staticmethod
    def _same_target(left: PackageDeploymentPlan, right: PackageDeploymentPlan, target: NativeTarget) -> None:
        if left.target_harness != right.target_harness or left.strategy != right.strategy:
            raise PackageDeploymentError('Native plan changed while the snapshot was staged.', plan=right)
        for key in ('path', 'nativeId'):
            if left.surface.get(key) != right.surface.get(key):
                raise PackageDeploymentError('Native target changed while the snapshot was staged.', plan=right)
        if left.target_harness != target.harness:
            raise PackageDeploymentError('Native target harness changed while the snapshot was staged.', plan=right)

    @staticmethod
    def _prove_target(destination: Path, deployment: dict) -> None:
        if destination != Path(deployment['target']) or destination.is_symlink() or not destination.is_dir():
            raise PackageDeploymentError('Owned native target is missing or changed.')
        if not _safe_path(destination) or package_fingerprint(destination) != deployment.get('targetFingerprint', deployment['appliedFingerprint']):
            raise PackageDeploymentError('Owned native target fingerprint changed.')
        info = destination.stat()
        if deployment['targetIdentity'] != [info.st_dev, info.st_ino]:
            raise PackageDeploymentError('Owned directory was replaced.')

    @staticmethod
    def _rollback_created(destination: Path, fingerprint: str, created_parents: list[Path] | None = None, identity=None) -> None:
        if destination.is_symlink() or not destination.is_dir():
            return
        try:
            if package_fingerprint(destination) == fingerprint and identity == _identity(destination):
                shutil.rmtree(destination)
                if created_parents:
                    PackageDeploymentService._remove_empty_directories(created_parents)
        except (OSError, ValueError, RuntimeError):
            return

    @staticmethod
    def _make_parent_directories(parent: Path) -> list[Path]:
        if not _safe_path(parent):
            raise PackageDeploymentError('Native destination parent is unsafe.')
        missing: list[Path] = []
        current = parent
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
        return missing

    @staticmethod
    def _remove_empty_directories(directories: list[Path]) -> None:
        for directory in directories:
            try:
                if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                return

    @staticmethod
    def _remove_stage(stage: Path, fingerprint: str, identity) -> None:
        if not stage.is_dir() or stage.is_symlink():
            return
        try:
            if package_fingerprint(stage) == fingerprint and _identity(stage) == identity:
                shutil.rmtree(stage)
        except (OSError, ValueError, RuntimeError):
            return

    @classmethod
    def _remove_owned_tree(cls, path: Path, fingerprint: str, identity) -> None:
        if path.is_symlink() or not path.is_dir():
            raise PackageDeploymentError('Owned backup is no longer safe to remove.')
        if package_fingerprint(path) != fingerprint or _identity(path) != identity:
            raise PackageDeploymentError('Owned backup fingerprint changed.')
        shutil.rmtree(path)

    @classmethod
    def _restore_backup(cls, destination: Path, backup: Path, fingerprint: str, identity) -> None:
        if destination.exists() or destination.is_symlink():
            raise PackageDeploymentError('Cannot safely restore the owned backup.')
        if backup.is_symlink() or not backup.is_dir() or package_fingerprint(backup) != fingerprint or _identity(backup) != identity:
            raise PackageDeploymentError('Owned backup fingerprint changed.')
        backup.rename(destination)

    @classmethod
    def _rollback_replacement(cls, destination: Path, backup: Path, new_fingerprint: str, old_fingerprint: str, identity, old_identity) -> None:
        cls._rollback_created(destination, new_fingerprint, identity=identity)
        if backup.exists() and not destination.exists():
            cls._restore_backup(destination, backup, old_fingerprint, old_identity)


def _safe_path(path: Path) -> bool:
    return path.is_absolute() and not any(part.is_symlink() for part in (path, *path.parents))


def _identity(path):
    if not _safe_path(path):
        raise PackageDeploymentError('Linked native target.')
    info = path.stat()
    return [info.st_dev, info.st_ino]
