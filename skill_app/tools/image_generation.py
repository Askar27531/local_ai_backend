from __future__ import annotations

import struct
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from skill_app.schemas.art import GeneratedImage, VisualBrief
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import GenerateImageCandidatesInput
from skill_app.tools.paths import safe_path


class ImageProvider(Protocol):
    def generate(self, brief: VisualBrief, *, seed: int) -> GeneratedImage: ...


class FakeImageProvider:
    name = "fake-image-v1"

    def generate(self, brief: VisualBrief, *, seed: int) -> GeneratedImage:
        color = (
            40 + seed % 170,
            40 + (seed // 3) % 170,
            40 + (seed // 7) % 170,
            255,
        )
        content = build_solid_png(brief.width, brief.height, color)
        return GeneratedImage(
            content=content,
            model=self.name,
            prompt=brief.prompt,
            negative_prompt=brief.negative_prompt,
            seed=seed,
            width=brief.width,
            height=brief.height,
            mime_type="image/png",
            alpha=True,
            provider_metadata={"fake": True, "color": list(color)},
        )


_provider: ImageProvider = FakeImageProvider()


def register_image_provider(provider: ImageProvider) -> None:
    global _provider
    _provider = provider


def get_image_provider() -> ImageProvider:
    return _provider


def generate_image_candidates(
    context: ToolContext,
    arguments: GenerateImageCandidatesInput,
) -> ToolResult:
    root = Path(context.workspace_root).resolve()
    output_dir = safe_path(root, arguments.output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    candidates: list[dict[str, object]] = []
    provider = get_image_provider()
    for index in range(arguments.brief.candidate_count):
        generated = provider.generate(arguments.brief, seed=10_000 + index)
        path = output_dir / f"{arguments.brief.asset_type.value}-candidate-{index + 1}.png"
        path.write_bytes(generated.content)
        paths.append(path.relative_to(root).as_posix())
        candidates.append(
            {
                "candidate_index": index + 1,
                "path": paths[-1],
                "model": generated.model,
                "prompt": generated.prompt,
                "negative_prompt": generated.negative_prompt,
                "seed": generated.seed,
                "width": generated.width,
                "height": generated.height,
                "mime_type": generated.mime_type,
                "alpha": generated.alpha,
                "provider_metadata": generated.provider_metadata,
            }
        )
    return ToolResult(
        ok=True,
        output={"candidate_count": len(paths), "paths": paths, "candidates": candidates},
        artifacts=paths,
    )


def build_solid_png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    pixel = bytes(color)
    scanline = b"\x00" + pixel * width
    raw = scanline * height
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(raw, level=6)),
            _chunk(b"IEND", b""),
        ]
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


@lru_cache(maxsize=128)
def thumbnail_color(seed: int) -> tuple[int, int, int, int]:
    return (
        40 + seed % 170,
        40 + (seed // 3) % 170,
        40 + (seed // 7) % 170,
        255,
    )
