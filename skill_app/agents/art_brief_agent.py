from __future__ import annotations

from skill_app.schemas.art import ArtAssetType, VisualBrief


class ArtBriefAgent:
    name = "art_brief_agent"

    def create_brief(self, request: str) -> VisualBrief:
        lowered = request.casefold()
        if any(hint in lowered for hint in ("ui", "界面", "按钮")):
            asset_type = ArtAssetType.UI_ICON
        elif any(hint in lowered for hint in ("概念", "场景", "concept")):
            asset_type = ArtAssetType.CONCEPT_DRAFT
        else:
            asset_type = ArtAssetType.ITEM_ICON

        if asset_type == ArtAssetType.CONCEPT_DRAFT:
            width, height = 1536, 1024
            use_case = "stylized-concept"
            composition = "Landscape concept-art draft with a clear focal subject and readable silhouette."
            style = "Polished game concept illustration."
        else:
            width = height = 1024
            use_case = "stylized-concept"
            composition = "Centered single icon, generous padding, crisp readable silhouette."
            style = "Polished game asset illustration suitable for UI use."

        require_alpha = any(hint in lowered for hint in ("透明", "alpha", "transparent"))
        prompt = "\n".join(
            [
                f"Use case: {use_case}",
                f"Asset type: {asset_type.value}",
                f"Primary request: {request}",
                f"Style/medium: {style}",
                f"Composition/framing: {composition}",
                "Lighting/mood: Controlled game-art lighting with clear material separation.",
                "Constraints: no watermark; no unintended text; preserve a clean production-ready silhouette.",
            ]
        )
        return VisualBrief(
            asset_type=asset_type,
            title=request[:160],
            primary_request=request,
            use_case=use_case,
            style_medium=style,
            composition=composition,
            lighting_mood="Controlled game-art lighting with clear material separation.",
            prompt=prompt,
            negative_prompt="watermark, signature, unintended text, clutter, cropped subject, low contrast",
            width=width,
            height=height,
            require_alpha=require_alpha,
        )
