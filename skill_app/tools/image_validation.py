from __future__ import annotations

import struct
from pathlib import Path

from skill_app.schemas.art import ImageValidationResult, VisualBrief
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.image_generation import thumbnail_color
from skill_app.tools.inputs import ValidateImageInput
from skill_app.tools.paths import safe_path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_image(content: bytes, brief: VisualBrief) -> ImageValidationResult:
    errors: list[str] = []
    if not content.startswith(PNG_SIGNATURE) or len(content) < 33:
        return ImageValidationResult(valid=False, errors=["Unsupported or invalid image format."])
    width, height, bit_depth, color_type = struct.unpack(">IIBB", content[16:26])
    alpha = color_type in {4, 6}
    if width != brief.width or height != brief.height:
        errors.append(f"Expected {brief.width}x{brief.height}, got {width}x{height}.")
    if bit_depth != 8:
        errors.append(f"Expected 8-bit PNG, got {bit_depth}-bit.")
    if brief.require_alpha and not alpha:
        errors.append("Image requires an alpha channel.")
    return ImageValidationResult(
        valid=not errors,
        width=width,
        height=height,
        mime_type="image/png",
        alpha=alpha,
        errors=errors,
    )


def validate_image_file(context: ToolContext, arguments: ValidateImageInput) -> ToolResult:
    path = safe_path(Path(context.workspace_root).resolve(), arguments.path)
    if not path.is_file():
        return ToolResult(
            ok=False,
            output={"path": arguments.path, "errors": ["Image file does not exist."]},
            error_type="image_not_found",
        )
    result = validate_image(path.read_bytes(), arguments.brief)
    return ToolResult(ok=result.valid, output={"path": arguments.path, **result.model_dump(mode="json")})


def build_contact_sheet(seeds: list[int]) -> bytes:
    width = max(1, len(seeds)) * 256
    height = 256
    # The deterministic provider uses flat candidate colors, so this is a faithful
    # lightweight contact sheet without adding a heavyweight imaging dependency.
    rows: list[bytes] = []
    colors = [thumbnail_color(seed) for seed in seeds]
    for _ in range(height):
        row = bytearray()
        for color in colors:
            row.extend(bytes(color) * 256)
        rows.append(b"\x00" + bytes(row))
    import zlib

    import skill_app.tools.image_generation as generation

    raw = b"".join(rows)
    return b"".join(
        [
            PNG_SIGNATURE,
            generation._chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            generation._chunk(b"IDAT", zlib.compress(raw, level=6)),
            generation._chunk(b"IEND", b""),
        ]
    )
