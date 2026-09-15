from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException

from skill_manager.application import BackendContainer
from skill_manager.api.deps import get_container
from skill_manager.api.schemas.skills import (
    ManagedPackageResponse,
    ManagedPackagesResponse,
    PackageDeploymentActionRequest,
    PackageDeploymentsResponse,
    SkillPackageContextResponse,
    SourcePackagesResponse,
)
from skill_manager.application.skills.native_package_runtime import PACKAGE_DEPLOYMENT_HARNESSES
from skill_manager.application.skills.package_deployment_service import PackageDeploymentError
from skill_manager.api.schemas import (
    BulkManageResultResponse,
    DisableSkillRequest,
    EnableSkillRequest,
    OkResponse,
    SetSkillHarnessesRequest,
    SetSkillHarnessesResultResponse,
    SkillDetailResponse,
    SkillsPageResponse,
    SkillSourceStatusResponse,
)

router = APIRouter(prefix="/api/skills")


@router.get("", response_model=SkillsPageResponse)
def list_skills(container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    return container.skills_queries.list_skills()


@router.get("/source-packages", response_model=SourcePackagesResponse)
def list_source_packages(container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    return container.skills_queries.list_source_packages()


@router.get('/managed-packages', response_model=ManagedPackagesResponse)
def list_managed_packages(container: BackendContainer = Depends(get_container)):
    return container.skills_queries.list_managed_packages()


@router.post('/managed-packages/{package_id}/refresh', response_model=ManagedPackageResponse)
def refresh_managed_package(package_id: str, container: BackendContainer = Depends(get_container)):
    try:
        return container.skills_queries.refresh_managed_package(package_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get('/managed-packages/{package_id}/deployments', response_model=PackageDeploymentsResponse)
def get_package_deployments(package_id: str, container: BackendContainer = Depends(get_container)):
    _require_package_id(package_id)
    try:
        return container.skills_queries.get_package_deployments(
            package_id,
            container.skills_native_package_adapter_factory,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PackageDeploymentError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/managed-packages/{package_id}/deployments/{harness}', response_model=PackageDeploymentsResponse)
def mutate_package_deployment(
    package_id: str,
    harness: str,
    body: PackageDeploymentActionRequest,
    container: BackendContainer = Depends(get_container),
):
    _require_package_id(package_id)
    if harness not in PACKAGE_DEPLOYMENT_HARNESSES:
        raise HTTPException(status_code=400, detail=f'unsupported package deployment harness: {harness}')
    try:
        return container.skills_queries.mutate_package_deployment(
            package_id,
            harness,
            body.action,
            container.skills_native_package_adapter_factory,
            replacement_package_id=body.replacementPackageId,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PackageDeploymentError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post('/{skill_ref:path}/manage-package', response_model=ManagedPackageResponse)
def manage_source_package(skill_ref: str, container: BackendContainer = Depends(get_container)):
    return container.skills_mutations.manage_source_package(skill_ref)


@router.get('/{skill_ref:path}/package-context', response_model=SkillPackageContextResponse)
def get_package_context(skill_ref: str, container: BackendContainer = Depends(get_container)):
    payload = container.skills_queries.get_package_context(skill_ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f'unknown skill ref: {skill_ref}')
    return payload


@router.post('/{skill_ref:path}/resolve-package', response_model=SkillPackageContextResponse)
def resolve_package_context(skill_ref: str, container: BackendContainer = Depends(get_container)):
    payload = container.skills_queries.resolve_package_context(skill_ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f'unknown skill ref: {skill_ref}')
    return payload


@router.get("/{skill_ref:path}/source-status", response_model=SkillSourceStatusResponse)
def get_skill_source_status(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    payload = container.skills_queries.get_skill_source_status(skill_ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown skill ref: {skill_ref}")
    return payload


@router.get("/{skill_ref:path}", response_model=SkillDetailResponse)
def get_skill_detail(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    payload = container.skills_queries.get_skill_detail(skill_ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown skill ref: {skill_ref}")
    return payload


@router.post("/{skill_ref:path}/enable", response_model=OkResponse)
def enable_skill(
    skill_ref: str,
    body: EnableSkillRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, bool]:
    return container.skills_mutations.enable_skill(skill_ref, body.harness)


@router.post("/{skill_ref:path}/disable", response_model=OkResponse)
def disable_skill(
    skill_ref: str,
    body: DisableSkillRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, bool]:
    return container.skills_mutations.disable_skill(skill_ref, body.harness)


@router.post("/{skill_ref:path}/set-harnesses", response_model=SetSkillHarnessesResultResponse)
def set_skill_harnesses(
    skill_ref: str,
    body: SetSkillHarnessesRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.skills_mutations.set_skill_all_harnesses(skill_ref, body.target)


@router.post("/{skill_ref:path}/manage", response_model=OkResponse)
def manage_skill(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, bool]:
    return container.skills_mutations.manage_skill(skill_ref)


@router.post("/manage-all", response_model=BulkManageResultResponse)
def manage_all_skills(container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    return container.skills_mutations.manage_all_skills()


@router.post("/{skill_ref:path}/update", response_model=OkResponse)
def update_skill(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, bool]:
    return container.skills_mutations.update_skill(skill_ref)


@router.post("/{skill_ref:path}/unmanage", response_model=OkResponse)
def unmanage_skill(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, bool]:
    return container.skills_mutations.unmanage_skill(skill_ref)


@router.post("/{skill_ref:path}/delete", response_model=OkResponse)
def delete_skill(skill_ref: str, container: BackendContainer = Depends(get_container)) -> dict[str, bool]:
    return container.skills_mutations.delete_skill(skill_ref)


def _require_package_id(package_id: str) -> None:
    if re.fullmatch(r'[0-9a-f]{64}', package_id) is None:
        raise HTTPException(status_code=400, detail='invalid managed package identity')
