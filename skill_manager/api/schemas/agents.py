from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OpenCodeAgentSourceResponse(BaseModel):
    file: str
    path: str
    format: Literal["json", "jsonc"]
    isWriteTarget: bool


class OpenCodeAgentResponse(BaseModel):
    name: str
    schemaGeneration: Literal["v1", "v2"]
    description: str | None = None
    # Canonical instructions (V1 prompt / V2 system); prompt is a legacy alias.
    instructions: str | None = None
    prompt: str | None = None
    model: str | None = None
    variant: str | None = None
    modelRaw: Any = None
    mode: str | None = None
    # Canonical permissions (V1 permission object / V2 rule list); raw fidelity.
    permissions: dict[str, Any] | list[Any] | None = None
    permission: dict[str, Any] | list[Any] | None = None
    disabled: bool | None = None
    hidden: bool | None = None
    color: str | None = None
    temperature: float | None = None
    topP: float | None = None
    steps: int | None = None
    tools: dict[str, Any] | None = None
    additionalOptions: dict[str, Any] = Field(default_factory=dict)
    source: OpenCodeAgentSourceResponse | None = None
    editability: Literal["config", "read-only"]
    readOnlyReasons: list[str] = Field(default_factory=list)
    readOnly: bool
    valid: bool = True
    diagnostic: str | None = None


class OpenCodeConfigSourceResponse(BaseModel):
    name: str
    path: str
    format: Literal["json", "jsonc"]
    precedence: int
    status: Literal["loaded", "missing", "invalid", "unreadable"]
    diagnostic: str | None = None


class OpenCodeAgentsResponse(BaseModel):
    agents: list[OpenCodeAgentResponse]
    writeTarget: str
    sources: list[OpenCodeConfigSourceResponse]
    diagnostics: list[str]
    limitation: str


__all__ = [
    "OpenCodeAgentResponse",
    "OpenCodeAgentSourceResponse",
    "OpenCodeAgentsResponse",
    "OpenCodeConfigSourceResponse",
]


class AgentFieldsRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    model: str | None = None
    variant: str | None = None
    mode: str | None = None
    renameTo: str | None = None


class AgentPreviewCreateRequest(BaseModel):
    schemaGeneration: Literal["v1", "v2"]
    fields: AgentFieldsRequest


class AgentPreviewUpdateRequest(BaseModel):
    fields: AgentFieldsRequest


class AgentCreateRequest(BaseModel):
    schemaGeneration: Literal["v1", "v2"]
    fields: AgentFieldsRequest
    expectedSourceHash: str


class AgentUpdateRequest(BaseModel):
    fields: AgentFieldsRequest
    expectedSourceHash: str


class AgentBackupResponse(BaseModel):
    file: str
    path: str


class AgentPreviewResponse(BaseModel):
    mode: Literal["create", "update"]
    generation: Literal["v1", "v2"]
    targetFile: str
    sourceHash: str
    old: dict[str, Any] | None = None
    new: dict[str, Any]
    renameTo: str | None = None
    textDiff: list[str]


class AgentSaveResponse(BaseModel):
    agent: OpenCodeAgentResponse
    changed: bool
    pendingApply: bool
    backup: AgentBackupResponse | None = None


class AgentEditorContextResponse(BaseModel):
    writeTarget: str
    create: dict[str, Any]
    sourceHash: str
    readOnly: bool


class AgentVariantOptionsResponse(BaseModel):
    options: list[str]


__all__ = [
    "OpenCodeAgentResponse",
    "OpenCodeAgentSourceResponse",
    "OpenCodeAgentsResponse",
    "OpenCodeConfigSourceResponse",
    "AgentFieldsRequest",
    "AgentPreviewCreateRequest",
    "AgentPreviewUpdateRequest",
    "AgentCreateRequest",
    "AgentUpdateRequest",
    "AgentBackupResponse",
    "AgentPreviewResponse",
    "AgentSaveResponse",
    "AgentEditorContextResponse",
    "AgentVariantOptionsResponse",
]
