"""Crop the flat margin off a source image: the ``--crop`` pre-processing step.

``--crop`` is not a render mode and not a post-process.  It is the first step of
the pipeline, before the tone filters and before the geometry: the margin goes
away and the subject is then sized into the *same* cell grid, which is what
makes the subject bigger on screen rather than merely deleting rows.

Two detectors, in order:

* **alpha**, when the image really carries transparency -- some pixel below
  ``alpha_threshold``, the same cut-off the renderer uses to decide whether a
  pixel can become ink -- is cropped to the box of its inked pixels.  Exact, no
  heuristic, no tolerance.
* **border colour** otherwise: each side measures its own border colour (the
  per-channel median of that edge) and the scan moves inward only while the
  *whole* row or column still matches it.  A single pixel of anything else stops
  that side, so the scan can never eat subject content; the worst case is "no
  crop".

Both are deliberately conservative, per the project's narrow-recovery rule.  An
image with no flat margin comes back unchanged with a warning rather than
cropped to something surprising.  So does a fully flat image: it has no content
to keep, and cropping it to nothing would be a blank canvas, which is a bug this
project has already shipped once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image, ImageChops

RGB = Tuple[int, int, int]
Box = Tuple[int, int, int, int]

#: Per-channel distance (0-255) within which a pixel still counts as the edge
#: colour it was measured against.  JPEG grain on a flat plate sits a long way
#: inside this; a gradient, a vignette or a textured border sits outside it, so
#: the scan stops there and the crop stays honest.  Not configurable on purpose:
#: `--crop` is one decision, and a tolerance knob would be a second one nobody
#: knows how to set.
BORDER_TOLERANCE = 16


@dataclass(frozen=True)
class CropResult:
    """The (possibly) cropped image, the box used, and why nothing happened."""

    image: Image.Image
    box: Optional[Box]
    warning: Optional[str] = None

    @property
    def cropped(self) -> bool:
        return self.box is not None


def _median_colour(strip: Image.Image) -> RGB:
    """Per-channel median of a one-pixel-wide edge strip."""

    values: List[int] = []
    for channel in range(3):
        histogram = strip.getchannel(channel).histogram()
        half = sum(histogram) // 2
        running = 0
        for value, count in enumerate(histogram):
            running += count
            if running > half:
                values.append(value)
                break
        else:  # pragma: no cover - a histogram always sums to its pixel count
            values.append(0)
    return (values[0], values[1], values[2])


def _peak_difference(image: Image.Image) -> int:
    """The largest absolute channel difference in an RGB difference image."""

    peak = 0
    for channel in range(3):
        peak = max(peak, max(image.getchannel(channel).tobytes(), default=0))
    return peak


def _all_within(strip: Image.Image, colour: RGB, tolerance: int) -> bool:
    """Whether every pixel of ``strip`` lies within ``tolerance`` of ``colour``."""

    reference = Image.new("RGB", strip.size, colour)
    return _peak_difference(ImageChops.difference(strip, reference)) <= tolerance


def _border_box(image: Image.Image, tolerance: int) -> Optional[Box]:
    """The box that removes the flat margin, or ``None`` if the image is flat.

    Rows and columns are only ever dropped from the outside in, and only while
    they are entirely the colour of the edge they came from.
    """

    rgb = image.convert("RGB")
    width, height = rgb.size

    top_colour = _median_colour(rgb.crop((0, 0, width, 1)))
    bottom_colour = _median_colour(rgb.crop((0, height - 1, width, height)))
    left_colour = _median_colour(rgb.crop((0, 0, 1, height)))
    right_colour = _median_colour(rgb.crop((width - 1, 0, width, height)))

    top = 0
    while top < height and _all_within(
        rgb.crop((0, top, width, top + 1)), top_colour, tolerance
    ):
        top += 1

    bottom = height
    while bottom > top and _all_within(
        rgb.crop((0, bottom - 1, width, bottom)), bottom_colour, tolerance
    ):
        bottom -= 1

    left = 0
    while left < width and _all_within(
        rgb.crop((left, 0, left + 1, height)), left_colour, tolerance
    ):
        left += 1

    right = width
    while right > left and _all_within(
        rgb.crop((right - 1, 0, right, height)), right_colour, tolerance
    ):
        right -= 1

    if right <= left or bottom <= top:
        # Every row or every column was the edge colour: a flat image, with no
        # content to keep.  Never crop it to nothing.
        return None
    return (left, top, right, bottom)


def _alpha_box(image: Image.Image, threshold: int) -> Optional[Box]:
    """Box of the pixels that can become ink, or ``None`` when there are none."""

    table = [0] * threshold + [255] * (256 - threshold)
    return image.getchannel("A").point(table).getbbox()


def _carries_real_transparency(image: Image.Image, threshold: int) -> bool:
    """Whether alpha says something: some pixel the renderer would not ink."""

    if "A" not in image.getbands():
        return False
    return min(image.getchannel("A").tobytes(), default=255) < threshold


def apply_crop(
    image: Image.Image,
    *,
    tolerance: int = BORDER_TOLERANCE,
    alpha_threshold: int = 128,
) -> CropResult:
    """Remove ``image``'s flat margin, or explain why it could not.

    ``image`` is returned untouched in :attr:`CropResult.image` whenever the
    result would be empty or nothing could be removed; :attr:`CropResult.warning`
    then says so, for the CLI's stderr and the web app's status line.  A cropped
    result carries no warning.
    """

    width, height = image.size
    full: Box = (0, 0, width, height)
    threshold = min(255, max(0, alpha_threshold))

    if _carries_real_transparency(image, threshold):
        box = _alpha_box(image, threshold)
        if box is None:
            return CropResult(
                image,
                None,
                "crop: nothing to keep -- every pixel is below the alpha threshold",
            )
        if box != full:
            return CropResult(image.crop(box), box)
        # Transparency somewhere, but not as a margin (a hole, or opaque content
        # touching every edge).  The border colour may still find a flat margin,
        # so fall through rather than giving up.

    box = _border_box(image, tolerance)
    if box is None:
        return CropResult(
            image, None, "crop: nothing to keep -- the image is a single flat colour"
        )
    if box == full:
        return CropResult(image, None, "crop: no flat border found, image left as is")
    return CropResult(image.crop(box), box)


__all__ = ["BORDER_TOLERANCE", "Box", "CropResult", "apply_crop"]
