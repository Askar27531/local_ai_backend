from __future__ import annotations

from pydantic import BaseModel, Field


class AgentPolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)


class ToolPolicy(BaseModel):
    agents: dict[str, AgentPolicy] = Field(default_factory=dict)
