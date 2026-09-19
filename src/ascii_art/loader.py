"""Input loading: local paths, stdin, EXIF orientation, format reporting."""

from __future__ import annotations

import io
import os
import sys
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import InputError

#: The required set from the design.  ``core`` entries must always work;
#: ``optional`` ones are reported by ``--formats`` when the codec is present
#: (the "feature flag" of section 6.1: the build never *requires* them).
CORE_FORMATS: Tuple[str, ...] = ("PNG", "JPEG", "GIF", "WEBP", "BMP")
OPTIONAL_FORMATS: Tuple[str, ...] = ("TIFF", "AVIF", "QOI", "ICO", "TGA", "PPM")

_FEATURE_NAMES = {
    "AVIF": "avif",
    "WEBP": "webp",
    "JPEG": "jpg",
    "TIFF": "libtiff",
    "QOI": "qoi",
}

FORMAT_EXTENSIONS = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "GIF": ".gif",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tif",
    "AVIF": ".avif",
    "QOI": ".qoi",
    "ICO": ".ico",
    "TGA": ".tga",
    "PPM": ".ppm",
}


def _pillow_supports(name: str) -> bool:
    extension = FORMAT_EXTENSIONS.get(name)
    if extension is None or extension not in Image.registered_extensions():
        return False
    feature = _FEATURE_NAMES.get(name)
    if feature:
        try:
            import warnings

            from PIL import features

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if features.check(feature) is False:
                    return False
        except (ImportError, ValueError):  # pragma: no cover - defensive
            pass
    return True


def available_formats() -> Tuple[List[str], List[str]]:
    """Return ``(core, optional)`` lists of decoders actually compiled in."""

    core = [name for name in CORE_FORMATS if _pillow_supports(name)]
    optional = [name for name in OPTIONAL_FORMATS if _pillow_supports(name)]
    return core, optional


def formats_report() -> str:
    core, optional = available_formats()
    pillar = Image.__version__
    lines = [
        f"ascii-art input formats (Pillow {pillar}):",
        "  " + ", ".join(core) if core else "  (none)",
    ]
    if optional:
        lines.append("optional (present in this build, never required):")
        lines.append("  " + ", ".join(optional))
    missing = [name for name in CORE_FORMATS if name not in core]
    if missing:
        lines.append("missing from this build: " + ", ".join(missing))
    return "\n".join(lines)


def load_image(
    source: str,
    *,
    stdin: Optional[io.BufferedReader] = None,
    apply_exif: bool = True,
) -> Image.Image:
    """Load one image into RGBA, applying EXIF orientation.

    ``source`` is a path, or ``"-"`` for stdin.  Animated inputs contribute
    their first frame only -- animation is explicitly a non-goal.
    """

    if source == "-":
        stream = stdin if stdin is not None else sys.stdin.buffer
        try:
            payload = stream.read()
        except OSError as exc:
            raise InputError(f"cannot read image from stdin: {exc}") from exc
        if not payload:
            raise InputError("no image data on stdin")
        return _decode(payload, "<stdin>", apply_exif)

    if not os.path.exists(source):
        raise InputError(f"{source}: no such file or directory")
    if os.path.isdir(source):
        raise InputError(f"{source}: is a directory")
    try:
        with open(source, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        raise InputError(f"{source}: {exc.strerror or exc}") from exc
    if not payload:
        raise InputError(f"{source}: empty file")
    return _decode(payload, source, apply_exif)


def _decode(payload: bytes, label: str, apply_exif: bool) -> Image.Image:
    try:
        handle = Image.open(io.BytesIO(payload))
        handle.seek(0)  # first frame only, for animated inputs
        if apply_exif:
            handle = ImageOps.exif_transpose(handle) or handle
        image = handle.convert("RGBA")
    except UnidentifiedImageError as exc:
        raise InputError(f"{label}: not a recognised image format") from exc
    except (OSError, ValueError) as exc:
        raise InputError(f"{label}: cannot decode image: {exc}") from exc
    if image.width == 0 or image.height == 0:
        raise InputError(f"{label}: image has zero width or height")
    return image


__all__ = [
    "CORE_FORMATS",
    "OPTIONAL_FORMATS",
    "available_formats",
    "formats_report",
    "load_image",
]
