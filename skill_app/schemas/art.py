from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtAssetType(StrEnum):
    ITEM_ICON = "item_icon"
    UI_ICON = "ui_icon"
    CONCEPT_DRAFT = "concept_draft"


class VisualBrief(BaseModel):
    schema_version: int = 1
    asset_type: ArtAssetType
    title: str = Field(min_length=1, max_length=160)
    primary_request: str = Field(min_length=1, max_length=4000)
    use_case: str = Field(min_length=1, max_length=80)
    style_medium: str = Field(min_length=1, max_length=240)
    composition: str = Field(min_length=1, max_length=500)
    lighting_mood: str = Field(min_length=1, max_length=500)
    color_palette: str = Field(default="", max_length=500)
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=4000)
    width: int = Field(ge=64, le=3840)
    height: int = Field(ge=64, le=3840)
    output_format: str = Field(default="png", pattern=r"^(png|webp|jpeg)$")
    require_alpha: bool = False
    candidate_count: int = Field(default=3, ge=2, le=6)
    reference_artifact_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_dimensions(self) -> VisualBrief:
        if self.width * self.height > 8_294_400:
            raise ValueError("Image dimensions exceed the maximum pixel count.")
        return self


class GeneratedImage(BaseModel):
    content: bytes = Field(exclude=True)
    model: str
    prompt: str
    negative_prompt: str = ""
    seed: int
    width: int
    height: int
    mime_type: str
    alpha: bool
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class ImageValidationResult(BaseModel):
    valid: bool
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    alpha: bool | None = None
    errors: list[str] = Field(default_factory=list)


class ArtManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    workflow_run_id: str
    visual_brief_artifact_id: str
    candidate_artifact_ids: list[str]
    validation_report_artifact_id: str
    contact_sheet_artifact_id: str
    selected_artifact_id: str
    source_project_modified: bool = False
