from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from skill_manager.api.deps import get_container
from skill_manager.api.schemas import (
    OpenCodeRuntimeSkillsRefreshRequest,
    OpenCodeRuntimeSkillsStatusResponse,
)
from skill_manager.application import BackendContainer
from skill_manager.application.skills.runtime import RuntimeSkillClientError
from skill_manager.errors import MutationError


router = APIRouter(prefix="/api/opencode/runtime-skills")


@router.get("/status", response_model=OpenCodeRuntimeSkillsStatusResponse)
def runtime_skills_status(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_runtime_skills.status()


@router.post("/refresh", response_model=OpenCodeRuntimeSkillsStatusResponse)
def refresh_runtime_skills(
    body: OpenCodeRuntimeSkillsRefreshRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    try:
        result = container.opencode_runtime_skills.refresh(
            server_url=body.server_url,
            directory=Path(body.directory),
            username=body.username,
            password=body.password,
        )
    except RuntimeSkillClientError as error:
        raise MutationError(str(error), status=502) from error
    container.skills_read_models.invalidate()
    return result


@router.post("/disconnect", response_model=OpenCodeRuntimeSkillsStatusResponse)
def disconnect_runtime_skills(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    result = container.opencode_runtime_skills.disconnect()
    container.skills_read_models.invalidate()
    return result
