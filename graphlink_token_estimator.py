"""Token counting for chat/context payloads.

Domain logic, not UI - moved out of graphlink_widgets/tokens.py (Phase 2,
TokenCounterWidget island migration), which is where it lived only because
it was originally introduced alongside the widget that displays its output.
Nothing here has ever imported Qt.
"""

import base64
import binascii
import io
import logging
import math

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

logger = logging.getLogger(__name__)

# Vision-token cost of one image content part. A text tokenizer has nothing
# meaningful to say about an image: the pixels reach the model as a fixed
# per-image cost the provider derives from the image's DIMENSIONS, not from
# its encoded byte length. The formula is Anthropic's published one (Claude
# vision docs, "Evaluate image size" / "Calculate image costs"): an image is
# first scaled down, preserving aspect ratio, until its long edge is at most
# 1568px and it costs at most ~1,600 tokens (about 1.15-1.2 megapixels -
# 1092x1092 is the documented 1:1 maximum), then costs
# width * height / 750 tokens. It is the one shared estimate for every
# provider; the other backends' own tile/patch schemes are not modelled.
# For the hosted APIs it sits at or above their own figures, which is the
# right side to miss on: an over-estimate trims one turn early, an
# under-estimate overflows the window. A local vision model that keeps far
# more of a large image (Qwen2.5-VL's 28px patches, for one) can cost
# several times this, so there it is a floor rather than a ceiling.
IMAGE_MAX_LONG_EDGE_PX = 1568
IMAGE_PIXELS_PER_TOKEN = 750
IMAGE_TOKENS_MAX = 1600
# Used whenever the dimensions cannot be read (a corrupt or unrecognised
# payload). Deliberately the formula's own ceiling, so an unreadable image
# is never budgeted BELOW what it might really cost.
IMAGE_TOKENS_FALLBACK = IMAGE_TOKENS_MAX


def _image_payload_bytes(data):
    """The raw encoded image behind an image_bytes part's "data": the
    runtime shape is raw bytes (attachments.py / content_codec's decode),
    but the Anthropic transport also accepts a base64 string, so both are
    read here. None when there is nothing decodable."""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data) or None
    if isinstance(data, str) and data:
        try:
            return base64.b64decode(data) or None
        except (binascii.Error, ValueError):
            return None
    return None


def estimate_image_tokens(data) -> int:
    """Vision-token estimate for one image payload (raw bytes or a base64
    string) - see the vision-token comment above IMAGE_MAX_LONG_EDGE_PX for
    the formula and its source. Only the image HEADER is parsed (PIL's open() is lazy and never
    decodes pixel data for .size), so this stays cheap on multi-megabyte
    attachments; any failure to read dimensions degrades to the
    conservative IMAGE_TOKENS_FALLBACK rather than raising."""
    raw = _image_payload_bytes(data)
    if raw is None:
        return IMAGE_TOKENS_FALLBACK
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
    except Exception:
        return IMAGE_TOKENS_FALLBACK
    if width <= 0 or height <= 0:
        return IMAGE_TOKENS_FALLBACK
    long_edge_scale = min(1.0, IMAGE_MAX_LONG_EDGE_PX / max(width, height))
    pixels = (width * long_edge_scale) * (height * long_edge_scale)
    return min(IMAGE_TOKENS_MAX, max(1, math.ceil(pixels / IMAGE_PIXELS_PER_TOKEN)))


class TokenEstimator:
    _instance = None
    _encoding = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenEstimator, cls).__new__(cls)
            if TIKTOKEN_AVAILABLE:
                try:
                    cls._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    cls._encoding = None
                    logger.warning(
                        "tiktoken installed, but failed to get encoding. Falling back to character count."
                    )
        return cls._instance

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding:
            return len(self._encoding.encode(text))
        else:
            return len(text) // 4
