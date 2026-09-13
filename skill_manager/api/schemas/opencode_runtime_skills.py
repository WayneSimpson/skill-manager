from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpenCodeRuntimeSkillsRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    consent: Literal[True] = Field(
        ...,
        description="Explicit permission to contact the selected local OpenCode server",
    )
    server_url: str = Field(..., alias="serverUrl", min_length=1, max_length=2_000)
    directory: str = Field(..., min_length=1, max_length=4_000)
    username: str | None = Field(default=None, min_length=1, max_length=1_000)
    password: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_basic_auth_pair(self) -> "OpenCodeRuntimeSkillsRefreshRequest":
        if (self.username is None) != (self.password is None):
            raise ValueError("username and password must be supplied together")
        return self


RuntimeSkillsStatus = Literal["disconnected", "ready", "error"]


class OpenCodeRuntimeSkillsStatusResponse(BaseModel):
    status: RuntimeSkillsStatus
    serverUrl: str | None
    directory: str | None
    skillCount: int
    error: str | None


__all__ = [
    "OpenCodeRuntimeSkillsRefreshRequest",
    "OpenCodeRuntimeSkillsStatusResponse",
    "RuntimeSkillsStatus",
]
