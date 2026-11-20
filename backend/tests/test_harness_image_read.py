"""Workspace image reads: accepted formats, size limits and safe previews."""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

import api_provider
from backend.harness.image_read import MAX_PREVIEW_BYTES, ImageReadError, read_image_preview
from backend.harness.loop import HarnessRunContext
from backend.harness.tools_fs import register_harness_fs_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, ToolRegistry


@pytest.fixture
def fs(tmp_path):
    registry = ToolRegistry()
    register_harness_fs_tools(registry)

    async def deny(_call):
        return False

    ctx = HarnessRunContext(
        granted_scopes=frozenset({FS_READ}),
        request_approval=deny,
        harness_workspace_dir=tmp_path,
    )

    def read(path: str, **kwargs):
        return asyncio.run(registry.invoke(
            ToolCall(id="1", name="fs.read", arguments={"path": path, **kwargs}), ctx,
        ))

    return tmp_path, read


def _image_bytes(fmt: str, size=(96, 48)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, "navy").save(stream, format=fmt)
    return stream.getvalue()


@pytest.mark.parametrize(
    ("extension", "image_format"),
    [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP"), ("gif", "GIF")],
)
def test_fs_read_images_return_dimensions_and_bounded_visual_previews(fs, extension, image_format):
    workspace, read = fs
    (workspace / f"photo.{extension}").write_bytes(_image_bytes(image_format))
    result = read(f"photo.{extension}")

    assert not result.is_error, result.content
    assert f"Image: {image_format}, 96 x 48 pixels," in result.content
    assert result.image_bytes is not None
    assert len(result.image_bytes) <= MAX_PREVIEW_BYTES
    with Image.open(io.BytesIO(result.image_bytes)) as preview:
        assert preview.format == "JPEG"
        assert preview.size == (96, 48)


def test_large_image_is_downscaled_not_relayed_at_original_resolution(fs):
    workspace, read = fs
    (workspace / "large.png").write_bytes(_image_bytes("PNG", (2400, 1200)))
    result = read("large.png")
    assert not result.is_error, result.content
    assert "2400 x 1200 pixels" in result.content
    assert "Preview: 1024 x 512 pixels" in result.content
    with Image.open(io.BytesIO(result.image_bytes)) as preview:
        assert preview.size == (1024, 512)


def test_large_image_is_reduced_before_exif_copy(monkeypatch):
    # Regression for #799: EXIF transposition used to copy all 20 MP pixels,
    # followed by another full-resolution copy for every preview size.
    from backend.harness import image_read

    original = image_read.ImageOps.exif_transpose
    inspected = []

    def checked_transpose(image):
        inspected.append(image.size)
        assert max(image.size) <= image_read.MAX_PREVIEW_SIDE
        return original(image)

    monkeypatch.setattr(image_read.ImageOps, "exif_transpose", checked_transpose)
    preview = read_image_preview(_image_bytes("PNG", (4000, 5000)))
    assert inspected and max(inspected[0]) <= 1024
    assert "4000 x 5000 pixels" in preview.description
    with Image.open(io.BytesIO(preview.image_bytes)) as output:
        assert output.size == (819, 1024)


def test_exif_orientation_still_reports_original_display_dimensions():
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise
    stream = io.BytesIO()
    Image.new("RGB", (2400, 1200), "navy").save(stream, format="JPEG", exif=exif)
    result = read_image_preview(stream.getvalue())
    assert "1200 x 2400 pixels" in result.description
    assert "Preview: 512 x 1024 pixels" in result.description
    with Image.open(io.BytesIO(result.image_bytes)) as output:
        assert output.size == (512, 1024)


def test_animated_gif_uses_first_frame(fs):
    workspace, read = fs
    stream = io.BytesIO()
    images = [Image.new("RGB", (40, 25), color) for color in ("red", "blue")]
    images[0].save(stream, format="GIF", save_all=True, append_images=images[1:], duration=100)
    (workspace / "animated.gif").write_bytes(stream.getvalue())
    result = read("animated.gif")
    assert not result.is_error, result.content
    assert "First frame of 2-frame animation." in result.content
    with Image.open(io.BytesIO(result.image_bytes)) as preview:
        red, green, blue = preview.convert("RGB").getpixel((20, 12))
        assert red > 200 and green < 80 and blue < 80


def test_decode_pixel_limit_rejects_compressed_bombs_before_loading(monkeypatch):
    from backend.harness import image_read

    monkeypatch.setattr(image_read, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ImageReadError, match="pixel decode limit"):
        read_image_preview(_image_bytes("PNG", (20, 20)))


def test_invalid_images_and_selectors_are_errors_not_model_data(fs):
    workspace, read = fs
    (workspace / "broken.webp").write_bytes(b"not an image")
    result = read("broken.webp")
    assert result.is_error and result.image_bytes is None
    assert "Could not read file" in result.content

    (workspace / "valid.png").write_bytes(_image_bytes("PNG"))
    for arguments in ({"offset": 1}, {"limit": 2}, {"page_start": 1}):
        selected = read("valid.png", **arguments)
        assert selected.is_error and selected.image_bytes is None


def test_file_size_limit_still_applies_to_images(fs, monkeypatch):
    from backend.harness import tools_fs

    workspace, read = fs
    (workspace / "photo.png").write_bytes(_image_bytes("PNG"))
    monkeypatch.setattr(tools_fs, "_MAX_FS_FILE_BYTES", 32)
    result = read("photo.png")
    assert result.is_error and result.image_bytes is None
    assert "32-byte harness read limit" in result.content


def test_vision_gate_keeps_only_the_existing_text_result_for_text_models():
    messages = [
        {"role": "tool", "name": "fs.read", "tool_call_id": "t1",
         "content": "Image: PNG, 20 x 10 pixels, 80 bytes."},
        {"role": "user", "_harness_image_observation": True,
         "content": [
             {"type": "text", "text": "workspace image"},
             {"type": "image_bytes", "data": b"preview"},
         ]},
    ]
    routed = api_provider._route_harness_image_observations(messages, vision_enabled=False)
    assert routed == messages[:1]
    routed = api_provider._route_harness_image_observations(messages, vision_enabled=True)
    assert routed == messages
