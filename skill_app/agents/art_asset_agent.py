from __future__ import annotations

from skill_app.schemas.art import GeneratedImage, VisualBrief
from skill_app.tools.image_generation import get_image_provider


class ArtAssetAgent:
    name = "art_asset_agent"

    def generate(self, brief: VisualBrief) -> list[GeneratedImage]:
        provider = get_image_provider()
        return [
            provider.generate(brief, seed=10_000 + index)
            for index in range(brief.candidate_count)
        ]
