from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from skill_manager.api.deps import get_container
from skill_manager.api.schemas.agents import OpenCodeAgentResponse, OpenCodeAgentsResponse
from skill_manager.application import BackendContainer
from skill_manager.application.agents import AmbiguousOpenCodeAgentError

router = APIRouter(prefix="/api/agents")


@router.get("/opencode", response_model=OpenCodeAgentsResponse)
def list_opencode_agents(container: BackendContainer = Depends(get_container)) -> dict[str, object]:
    return container.opencode_agent_queries.list_agents()


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
