"""Image validation and pre-processing.

Security note
-------------
File extensions are attacker-controlled and mean nothing. Every upload is
validated by sniffing its magic bytes and then round-tripping it through
Pillow's ``verify()``, so a ``.jpg`` that is really a zip bomb or a polyglot
script is rejected before it ever touches the filesystem.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.utils.constants import (
    ALLOWED_IMAGE_EXTENSIONS,
    MAGIC_SIGNATURES,
    WEBP_FORMAT_MARKER,
    WEBP_RIFF_PREFIX,
)

logger = logging.getLogger(__name__)

# Pillow refuses images above this many pixels by default; we lower it further
# because a legitimate phone photo of a kirana bill is never this large.
Image.MAX_IMAGE_PIXELS = 50_000_000

# Long-edge cap applied before sending to an LLM. Vision models tile images and
# bill you per tile, so downscaling a 12 MP phone photo is free accuracy-neutral
# money: handwriting on a 1600 px long edge is already comfortably legible.
MAX_LLM_DIMENSION = 1600

# Progressively harsher (long_edge, jpeg_quality) steps, applied only when a
# provider imposes a payload ceiling. Resolution drops before quality does.
_COMPRESSION_LADDER: tuple[tuple[int, int], ...] = (
    (1400, 85),
    (1200, 80),
    (1024, 75),
    (900, 70),
    (800, 65),
    (700, 60),
    (600, 55),
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ImageValidationError(ValueError):
    """Raised when an upload is not a usable image."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Verified facts about an uploaded image."""

    mime_type: str
    extension: str
    size_bytes: int
    width: int
    height: int


def detect_mime_type(data: bytes) -> str | None:
    """Return the MIME type implied by the file's magic bytes, or ``None``."""
    for signature, mime in MAGIC_SIGNATURES:
        if data.startswith(signature):
            return mime
    if (
        len(data) >= 12
        and data[:4] == WEBP_RIFF_PREFIX
        and data[8:12] == WEBP_FORMAT_MARKER
    ):
        return "image/webp"
    return None


def _extension_for(mime_type: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime_type]


def validate_image_bytes(data: bytes, *, max_bytes: int) -> ImageInfo:
    """Validate raw upload bytes.

    Args:
        data: The complete file contents.
        max_bytes: Hard size ceiling, from ``MAX_IMAGE_SIZE_MB``.

    Returns:
        Verified :class:`ImageInfo`.

    Raises:
        ImageValidationError: Empty, oversized, wrong magic bytes, or not a
            decodable image.
    """
    if not data:
        raise ImageValidationError("Uploaded file is empty.")

    if len(data) > max_bytes:
        raise ImageValidationError(
            f"Image is {len(data) / 1_048_576:.1f} MB, which exceeds the "
            f"{max_bytes / 1_048_576:.0f} MB limit."
        )

    mime_type = detect_mime_type(data)
    if mime_type is None:
        raise ImageValidationError(
            "File does not start with JPEG, PNG or WebP magic bytes. "
            "Renaming a PDF or document to .jpg will not work -- please upload "
            "an actual photo of the bill."
        )

    extension = _extension_for(mime_type)
    if extension not in ALLOWED_IMAGE_EXTENSIONS:  # pragma: no cover - defensive
        raise ImageValidationError(f"Unsupported image type: {mime_type}")

    # Magic bytes prove the header; verify() proves the whole file decodes.
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(f"Image could not be decoded: {exc}") from exc

    if width < 64 or height < 64:
        raise ImageValidationError(
            f"Image is only {width}x{height}px. Handwriting will not be legible "
            "to any model below roughly 640x640."
        )

    return ImageInfo(
        mime_type=mime_type,
        extension=extension,
        size_bytes=len(data),
        width=width,
        height=height,
    )


def safe_filename(original: str, *, extension: str) -> str:
    """Build a collision-proof, path-traversal-proof storage filename.

    ``../../etc/passwd`` and ``bill 01 (कीरana).jpg`` both come out as a tame
    ASCII slug with a UUID prefix.
    """
    stem = Path(original or "bill").stem
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    stem = _SAFE_NAME.sub("_", stem).strip("._-") or "bill"
    return f"{uuid.uuid4().hex[:12]}_{stem[:60]}{extension}"


def _encode_jpeg(image: Image.Image, *, long_edge: int, quality: int) -> bytes:
    """Downscale to ``long_edge`` and encode as JPEG at ``quality``."""
    copy = image.copy()
    copy.thumbnail((long_edge, long_edge), Image.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def load_image_for_llm(
    image_path: str | Path, *, max_encoded_bytes: int | None = None
) -> tuple[str, str]:
    """Read an image from disk and return ``(base64_data, mime_type)``.

    Large images are downscaled and re-encoded as JPEG to cut token cost. If
    Pillow cannot re-encode for any reason we fall back to shipping the original
    bytes -- a slightly more expensive call beats a failed extraction.

    Args:
        image_path: Path to the stored bill image.
        max_encoded_bytes: Optional ceiling on the *base64* payload size. Some
            providers cap the size of an inline data URL and reject anything
            larger outright, so a client can ask for a smaller image rather than
            having the request fail. We step down resolution and JPEG quality
            together, because on handwriting, resolution is worth more than
            compression fidelity -- legible strokes at 1024px beat a blocky
            1600px.

    Raises:
        ImageValidationError: If the path does not exist or is not an image.
    """
    path = Path(image_path)
    if not path.is_file():
        raise ImageValidationError(f"Image not found on disk: {path}")

    data = path.read_bytes()
    mime_type = detect_mime_type(data)
    if mime_type is None:
        raise ImageValidationError(f"Stored file is not a recognised image: {path.name}")

    def encoded_size(raw: bytes) -> int:
        """Base64 inflates by 4/3, rounded up to a 4-character boundary."""
        return ((len(raw) + 2) // 3) * 4

    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")

            within_dimension = max(opened.size) <= MAX_LLM_DIMENSION
            within_budget = max_encoded_bytes is None or encoded_size(data) <= max_encoded_bytes
            if within_dimension and within_budget:
                return base64.b64encode(data).decode("ascii"), mime_type

            payload = _encode_jpeg(image, long_edge=MAX_LLM_DIMENSION, quality=90)

            if max_encoded_bytes is not None:
                for long_edge, quality in _COMPRESSION_LADDER:
                    if encoded_size(payload) <= max_encoded_bytes:
                        break
                    payload = _encode_jpeg(image, long_edge=long_edge, quality=quality)
                else:
                    if encoded_size(payload) > max_encoded_bytes:
                        logger.warning(
                            "%s is still %d encoded bytes after maximum compression "
                            "(budget %d). Sending anyway; the provider may reject it.",
                            path.name,
                            encoded_size(payload),
                            max_encoded_bytes,
                        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Downscale failed for %s (%s); sending original bytes.", path.name, exc)
        return base64.b64encode(data).decode("ascii"), mime_type

    logger.debug(
        "Prepared %s: %d bytes on disk -> %d bytes encoded.",
        path.name,
        len(data),
        encoded_size(payload),
    )
    return base64.b64encode(payload).decode("ascii"), "image/jpeg"
