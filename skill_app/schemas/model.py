from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    base_url: str | None = None
    api_key_env: str | None = Field(default=None, max_length=120)
    temperature: float = Field(default=0, ge=0, le=2)
    context_window: int = Field(default=8192, ge=1)
    supports_tools: bool = False
    supports_vision: bool = False
    supports_structured_outputs: bool = False
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ModelUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderResponse(BaseModel):
    text: str
    usage: ModelUsage = Field(default_factory=ModelUsage)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ModelProvider(Protocol):
    def invoke(self, profile: ModelProfile, messages: list[ModelMessage]) -> ProviderResponse: ...

    def stream(self, profile: ModelProfile, messages: list[ModelMessage]) -> Iterator[str]: ...


class ModelInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    step_run_id: str | None = None
    profile_name: str
    messages: list[ModelMessage] = Field(min_length=1)
    prompt_version: str = Field(default="api-v1", min_length=1, max_length=80)


class ModelInvokeResponse(BaseModel):
    text: str
    model_call_id: str
    profile_name: str
    model: str
    usage: ModelUsage


class ModelCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    step_run_id: str | None
    profile_name: str
    provider: str
    model: str
    prompt_version: str
    input_chars: int
    output_chars: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    status: str
    error_type: str | None
    raw_output_artifact_id: str | None
    created_at: datetime
