"""The ``--crop`` pre-processing step (``ascii_art.crop``).

``--crop`` is source-side: it removes a flat margin so the subject can fill the
grid the caller asked for.  These tests pin the two detectors (alpha first, then
border colour), how far the tolerance reaches, and the three promises that keep
it safe: it never eats subject content, it never empties an image, and a crop
that cannot do its job returns the image untouched with a warning instead of
guessing.
"""

from __future__ import annotations

import random

import pytest
from PIL import Image, ImageDraw

import fixtures

from ascii_art.crop import BORDER_TOLERANCE, apply_crop

pytestmark = pytest.mark.render


def _grain(image: Image.Image, amplitude: int, rows: range, seed: int = 7) -> None:
    """Add deterministic per-pixel noise to ``rows``."""

    pixels = image.load()
    rng = random.Random(seed)
    for y in rows:
        for x in range(image.width):
            red, green, blue = pixels[x, y]
            shift = rng.randint(-amplitude, amplitude)
            pixels[x, y] = tuple(
                min(255, max(0, channel + shift)) for channel in (red, green, blue)
            )


def test_flat_margin_is_removed():
    result = apply_crop(fixtures.plate())

    assert result.box == (40, 40, 200, 120)
    assert result.image.size == (160, 80)
    assert result.cropped
    assert result.warning is None, "a crop that worked is not a warning"


def test_the_margin_colour_does_not_matter():
    """A dark plate is cropped exactly like a light one."""

    result = apply_crop(fixtures.plate(background=(0, 0, 0), ink=(255, 255, 255)))

    assert result.box == (40, 40, 200, 120)


def test_a_real_transparent_margin_is_exact():
    """Alpha is a measurement, not a guess: no tolerance involved."""

    result = apply_crop(fixtures.alpha_probe(300, 100))

    assert result.box == (100, 0, 200, 100)


def test_opaque_rgba_falls_back_to_the_border_colour():
    """An alpha channel is not the same thing as transparency."""

    result = apply_crop(fixtures.plate().convert("RGBA"))

    assert result.box == (40, 40, 200, 120)


def test_transparency_that_is_not_a_margin_still_finds_the_plate():
    """A hole in the middle says nothing about the margin, so the plate decides."""

    image = fixtures.plate().convert("RGBA")
    ImageDraw.Draw(image).rectangle((100, 60, 140, 100), fill=(0, 0, 0, 0))

    result = apply_crop(image)

    assert result.box == (40, 40, 200, 120)


def test_jpeg_grain_on_the_plate_is_tolerated():
    """Real plates are not mathematically flat; the tolerance exists for this."""

    image = fixtures.plate()
    _grain(image, BORDER_TOLERANCE // 2, range(image.height))

    result = apply_crop(image)

    assert result.box == (40, 40, 200, 120)


def test_a_textured_border_is_content_not_whitespace():
    """Past the tolerance the border is no longer a margin: that side stays put.

    The three clean sides are still cropped -- the scan is per side on purpose.
    """

    image = fixtures.plate()
    _grain(image, BORDER_TOLERANCE * 4, range(40))

    result = apply_crop(image)

    assert result.box == (0, 0, 240, 120), "only the clean sides may move"


def test_a_photo_without_a_flat_margin_is_left_alone():
    image = fixtures.photo()

    result = apply_crop(image)

    assert result.image is image, "the caller's image is what comes back"
    assert not result.cropped
    assert result.warning and "crop:" in result.warning


def test_a_flat_image_is_never_cropped_to_nothing():
    """The bug this project already shipped once: a blank canvas."""

    for value in (0, 255):
        result = apply_crop(fixtures.solid(value))

        assert not result.cropped
        assert result.image.size == (100, 50)
        assert result.warning and "flat colour" in result.warning


def test_an_image_below_the_alpha_threshold_has_no_content():
    image = Image.new("RGBA", (40, 20), (10, 20, 30, 0))

    result = apply_crop(image)

    assert not result.cropped
    assert result.warning and "alpha threshold" in result.warning


def test_the_alpha_threshold_follows_the_renderer():
    """Half-transparent content is ink or not ink depending on the threshold."""

    image = Image.new("RGBA", (40, 20), (255, 255, 255, 0))
    ImageDraw.Draw(image).rectangle((10, 0, 29, 19), fill=(255, 255, 255, 200))

    assert apply_crop(image).box == (10, 0, 30, 20), "alpha 200 is ink at 128"
    assert not apply_crop(image, alpha_threshold=250).cropped, "alpha 200 is not ink at 250"
