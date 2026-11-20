"""Safe, bounded image previews for workspace-agent fs.read.

The file tool already confines paths and caps file bytes before calling us.
This module independently caps decoded pixels and JPEG preview bytes so even a
highly compressed image cannot exhaust memory or flood a model request. GIF
and animated WebP files are represented by their first frame.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 20_000_000
MAX_PREVIEW_SIDE = 1024
MAX_PREVIEW_BYTES = 1_000_000

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF"})


class ImageReadError(ValueError):
    """An image is invalid, unsupported, or too expensive to decode."""


@dataclass(frozen=True)
class ImagePreview:
    description: str
    image_bytes: bytes


def _rgb(image: Image.Image) -> Image.Image:
    """Flatten transparency onto white for a portable, small JPEG preview."""
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, "white")
    canvas.paste(rgba, mask=rgba.getchannel("A"))
    return canvas


def read_image_preview(data: bytes) -> ImagePreview:
    """Inspect one bounded file and render a small, first-frame JPEG.

    The caller enforces its separate 8 MiB *file* cap before this function
    runs; decoding is guarded by a pixel cap before Image.load() executes.
    Content type is determined by Pillow's signature detection, not a name
    supplied by the model.
    """
    try:
        with Image.open(io.BytesIO(data)) as source:
            image_format = source.format
            if image_format not in _ALLOWED_FORMATS:
                raise ImageReadError("Unsupported image format.")
            width, height = source.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ImageReadError(
                    f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel decode limit."
                )
            frames = int(getattr(source, "n_frames", 1))
            source.seek(0)
            # Apply EXIF orientation before reporting dimensions or resizing.
            oriented = ImageOps.exif_transpose(source)
            display_width, display_height = oriented.size

            # A bounded output independent of the original format and frame
            # count. Never serialize original bytes into a model request.
            preview_bytes = None
            preview_size = None
            for side in (MAX_PREVIEW_SIDE, 768, 512):
                thumbnail = oriented.copy()
                thumbnail.thumbnail((side, side), Image.Resampling.LANCZOS)
                rgb = _rgb(thumbnail)
                for quality in (80, 65):
                    output = io.BytesIO()
                    rgb.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= MAX_PREVIEW_BYTES:
                        preview_bytes = output.getvalue()
                        preview_size = thumbnail.size
                        break
                if preview_bytes is not None:
                    break
            if preview_bytes is None or preview_size is None:
                raise ImageReadError("Could not make a preview within the image size limit.")

            description = (
                f"Image: {image_format}, {display_width} x {display_height} pixels, "
                f"{len(data):,} bytes."
            )
            if preview_size != (display_width, display_height):
                description += f" Preview: {preview_size[0]} x {preview_size[1]} pixels."
            if frames > 1:
                description += f" First frame of {frames}-frame animation."
            return ImagePreview(description=description, image_bytes=preview_bytes)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageReadError(f"Could not decode image: {exc}") from exc
