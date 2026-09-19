"""Pre-processing filters.

The web tools' advantage (report section 3.6) is that you can tune the image
before conversion; among CLIs only ``img2txt`` has any of this.  Six scalar
knobs, applied to RGB while the alpha channel passes through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageEnhance, ImageOps

from .errors import UsageError

#: ``(name, minimum, maximum)`` for the scalar filters.
SCALAR_RANGES = {
    "brightness": (0.0, 10.0),
    "contrast": (0.0, 10.0),
    "gamma": (0.01, 10.0),
}


@dataclass(frozen=True)
class FilterSpec:
    brightness: float = 1.0
    contrast: float = 1.0
    gamma: float = 1.0
    invert: bool = False
    rotate: int = 0
    flip_x: bool = False
    flip_y: bool = False

    @property
    def is_identity(self) -> bool:
        return (
            self.brightness == 1.0
            and self.contrast == 1.0
            and self.gamma == 1.0
            and not self.invert
            and self.rotate == 0
            and not self.flip_x
            and not self.flip_y
        )


def validate(spec: FilterSpec) -> None:
    for name, (low, high) in SCALAR_RANGES.items():
        value = getattr(spec, name)
        if not low <= value <= high:
            raise UsageError(f"--{name} must be between {low} and {high}")
    if spec.rotate not in (0, 90, 180, 270):
        raise UsageError("--rotate must be one of 90, 180, 270")


def _gamma_lut(gamma: float) -> list:
    inverse = 1.0 / gamma
    return [min(255, max(0, int(round(255.0 * ((i / 255.0) ** inverse))))) for i in range(256)]


def apply_filters(image: Image.Image, spec: FilterSpec) -> Image.Image:
    """Apply geometric then tonal filters, keeping alpha intact."""

    if spec.is_identity:
        return image

    validate(spec)

    # Geometry first: the output grid is derived from the final dimensions.
    if spec.rotate:
        image = image.rotate(-spec.rotate, expand=True)
    if spec.flip_x:
        image = ImageOps.mirror(image)
    if spec.flip_y:
        image = ImageOps.flip(image)

    if spec.brightness == 1.0 and spec.contrast == 1.0 and spec.gamma == 1.0 and not spec.invert:
        return image

    alpha: Optional[Image.Image] = None
    if image.mode == "RGBA":
        alpha = image.getchannel("A")
    rgb = image.convert("RGB")

    if spec.brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(spec.brightness)
    if spec.contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(spec.contrast)
    if spec.gamma != 1.0:
        rgb = rgb.point(_gamma_lut(spec.gamma) * 3)
    if spec.invert:
        rgb = ImageOps.invert(rgb)

    if alpha is None:
        return rgb
    return Image.merge("RGBA", (*rgb.split(), alpha))


__all__ = ["FilterSpec", "SCALAR_RANGES", "apply_filters", "validate"]
