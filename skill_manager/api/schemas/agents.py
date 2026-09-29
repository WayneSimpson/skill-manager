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
