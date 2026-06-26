from __future__ import annotations

from skill_app.agents.art_brief_agent import ArtBriefAgent
from skill_app.schemas.art import ArtAssetType
from skill_app.tools.image_generation import FakeImageProvider, build_solid_png
from skill_app.tools.image_validation import build_contact_sheet, validate_image


def test_art_brief_classifies_supported_assets_and_transparency():
    brief = ArtBriefAgent().create_brief("制作一个透明背景的古老钥匙道具图标")

    assert brief.asset_type == ArtAssetType.ITEM_ICON
    assert brief.require_alpha
    assert brief.width == brief.height == 1024
    assert brief.candidate_count == 3
    assert "古老钥匙" in brief.prompt


def test_fake_provider_is_traceable_and_image_validation_checks_spec():
    brief = ArtBriefAgent().create_brief("制作 UI 按钮图标")
    generated = FakeImageProvider().generate(brief, seed=12345)
    result = validate_image(generated.content, brief)

    assert result.valid
    assert result.width == brief.width
    assert result.height == brief.height
    assert result.alpha
    assert generated.model == "fake-image-v1"
    assert generated.prompt == brief.prompt
    assert generated.seed == 12345

    wrong = build_solid_png(64, 64, (10, 20, 30, 255))
    invalid = validate_image(wrong, brief)
    assert not invalid.valid
    assert "Expected 1024x1024" in invalid.errors[0]


def test_contact_sheet_is_valid_png_with_expected_dimensions():
    brief = ArtBriefAgent().create_brief("制作道具图标")
    brief = brief.model_copy(update={"width": 768, "height": 256})
    result = validate_image(build_contact_sheet([10000, 10001, 10002]), brief)

    assert result.valid
    assert result.width == 768
    assert result.height == 256
