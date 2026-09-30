from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from skill_manager.api.deps import get_container
from skill_manager.api.schemas.agents import (
    AgentAcknowledgeResultResponse,
    AgentApplyCapabilityResponse,
    AgentApplyRequest,
    AgentApplyResultResponse,
    AgentApplyStatusResponse,
    AgentCreateRequest,
    AgentEditorContextResponse,
    AgentPreviewCreateRequest,
    AgentPreviewUpdateRequest,
    AgentPreviewResponse,
    AgentSaveResponse,
    AgentUpdateRequest,
    AgentVariantOptionsResponse,
    OpenCodeAgentResponse,
    OpenCodeAgentsResponse,
)
from skill_manager.application import BackendContainer
from skill_manager.application.agents import AmbiguousOpenCodeAgentError

router = APIRouter(prefix="/api/agents")


@router.get("/opencode", response_model=OpenCodeAgentsResponse)
def list_opencode_agents(container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    return container.opencode_agent_queries.list_agents()


@router.get("/opencode/editor-context", response_model=AgentEditorContextResponse)
def opencode_agent_editor_context(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_mutations.editor_context()


@router.get("/opencode/variant-options", response_model=AgentVariantOptionsResponse)
def opencode_agent_variant_options(
    model: str,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return {"options": container.opencode_agent_mutations.variant_options(model)}


@router.post("/opencode/preview-create", response_model=AgentPreviewResponse)
def preview_opencode_agent_create(
    body: AgentPreviewCreateRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_mutations.preview_create(
        body.schemaGeneration, body.fields.model_dump(exclude_unset=True))


@router.post("/opencode/preview-update/{name}", response_model=AgentPreviewResponse)
def preview_opencode_agent_update(
    name: str,
    body: AgentPreviewUpdateRequest,
    schemaGeneration: str | None = None,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_mutations.preview_update(
        name, schemaGeneration, body.fields.model_dump(exclude_unset=True))


@router.post("/opencode", response_model=AgentSaveResponse)
def create_opencode_agent(
    body: AgentCreateRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_mutations.create_agent(
        body.schemaGeneration, body.fields.model_dump(exclude_unset=True),
        expected_hash=body.expectedSourceHash)


@router.put("/opencode/{name}", response_model=AgentSaveResponse)
def update_opencode_agent(
    name: str,
    body: AgentUpdateRequest,
    schemaGeneration: str | None = None,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_mutations.update_agent(
        name, schemaGeneration, body.fields.model_dump(exclude_unset=True),
        expected_hash=body.expectedSourceHash)


@router.get("/opencode/apply-capability", response_model=AgentApplyCapabilityResponse)
def opencode_agent_apply_capability(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_apply.capability()


@router.get("/opencode/apply-status", response_model=AgentApplyStatusResponse)
def opencode_agent_apply_status(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_apply.status()


@router.post("/opencode/apply", response_model=AgentApplyResultResponse)
def apply_opencode_agents(
    body: AgentApplyRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_apply.apply(confirm=body.confirm)


@router.post("/opencode/apply/acknowledge-manual", response_model=AgentAcknowledgeResultResponse)
def acknowledge_manual_opencode_apply(
    body: AgentApplyRequest,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_apply.acknowledge_manual(confirm=body.confirm)


@router.get("/opencode/{name}", response_model=OpenCodeAgentResponse)
def get_opencode_agent(
    name: str,
    schemaGeneration: str | None = None,
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    try:
        return container.opencode_agent_queries.get_agent(
            name, schema_generation=schemaGeneration
        )
    except AmbiguousOpenCodeAgentError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        status = 400 if str(error).startswith("invalid schemaGeneration") else 404
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.get("/opencode/model-catalogue")
def opencode_agent_model_catalogue(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    return container.opencode_agent_catalogue.catalogue()


@router.get("/opencode/permission-actions")
def opencode_agent_permission_actions(
    container: BackendContainer = Depends(get_container),
) -> dict[str, object]:
    from skill_manager.opencode.agent_permissions import (
        COMMON_ACTIONS_V1, COMMON_ACTIONS_V2,
    )
    return {
        "v1": list(COMMON_ACTIONS_V1),
        "v2": list(COMMON_ACTIONS_V2),
        "effects": ["allow", "ask", "deny"],
    }
