"""Deterministic test images.

The report's §1.2 probe set, rebuilt from code so the regression suite needs no
downloaded binaries and every run measures the same pixels.  Each fixture
isolates one behaviour:

===========================  ==================================================
``photo``                    real photographic content (structure/tone)
``shapes``                   hard-edged flat art (structure)
``circle``                   aspect-ratio correctness
``text_image``               legibility (terminal-style light-on-dark text)
``ramp_gradient``            256-step horizontal gradient (tone / ramp levels)
``checker``                  1px checkerboard (area-average downsampling)
``alpha_probe``              transparent | opaque | transparent (alpha handling)
``solid``                    which background a tool assumes
``big``                      4000x3000 source (performance)
===========================  ==================================================

Note the deliberate ``ramp_gradient`` orientation: the report's appendix warns
that a *vertical* gradient silently measures a constant column.  Ours is
left-to-right by construction and ``test_tone`` pins that down.
"""

from __future__ import annotations

import math
import random
from pathlib import Path


from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = (
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()  # pragma: no cover - environment dependent


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def photo(width: int = 256, height: int = 256) -> Image.Image:
    """A deterministic, photographic-looking image.

    Several spatial frequencies plus seeded grain, so a ramp render has real
    structure and tone to reproduce -- a flat gradient would flatter any tool.
    """

    image = Image.new("RGB", (width, height))
    pixels = image.load()
    rng = random.Random(20240919)
    grain = [[rng.uniform(-12, 12) for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            base = (
                118.0
                + 52.0 * math.sin(x / 29.0)
                + 34.0 * math.cos(y / 17.0)
                + 26.0 * math.sin((x + y) / 11.0)
                + 18.0 * math.cos((x - y) / 7.0)
            )
            vignette = 1.0 - 0.35 * (((x - width / 2) / width) ** 2 + ((y - height / 2) / height) ** 2)
            v = base * vignette + grain[y][x]
            pixels[x, y] = (
                _clamp(v * 1.02),
                _clamp(v * 0.86 + 22),
                _clamp(255.0 - v * 0.72),
            )

    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.22, height * 0.22, width * 0.52, height * 0.52),
                 fill=(232, 226, 214))
    draw.rectangle((width * 0.60, height * 0.18, width * 0.86, height * 0.40),
                   fill=(40, 54, 78))
    draw.polygon(
        [(width * 0.12, height * 0.86), (width * 0.34, height * 0.60),
         (width * 0.56, height * 0.86)],
        fill=(150, 132, 96),
    )
    return image


def shapes(width: int = 256, height: int = 256) -> Image.Image:
    """Flat art with hard edges: circle, rectangle, triangle on black."""

    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.06, height * 0.03, width * 0.44, height * 0.41),
                 fill=(255, 255, 255))
    draw.rectangle((width * 0.52, height * 0.03, width * 0.94, height * 0.41),
                   fill=(190, 190, 190))
    draw.polygon(
        [(width * 0.50, height - 1), (width * 0.18, height * 0.50),
         (width * 0.82, height * 0.50)],
        fill=(140, 140, 140),
    )
    return image


def circle(size: int = 400) -> Image.Image:
    """A filled disc on a square canvas: the aspect-ratio probe (report §2.3)."""

    image = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size * 0.08
    draw.ellipse((margin, margin, size - margin, size - margin), fill=(255, 255, 255))
    return image


TEXT_LINES = (
    "The quick brown fox jumps",
    "over the lazy dog. 0123456789",
    "Legibility matters: RMSE_fit",
    "under 6 beats every incumbent.",
    "pack my box with five dozen",
    "liquor jugs -- 2024 report",
)


SERIF_FONT_CANDIDATES = (
    "/usr/share/fonts/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/TTF/DejaVuSerif.ttf",
)


def _serif(size: int):
    for path in SERIF_FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return _font(size)


def text_image(width: int = 600, height: int = 200) -> Image.Image:
    """Dark text on a light page -- the report's legibility probe.

    Same shape as the report's ``text.png``: 600x200, light page (``ref_mean``
    ~244), rendered text lines large enough that a few cells carry each stroke.
    A light background then becomes dense glyphs and a dark stroke becomes none,
    which is the polarity the metric's default dark-background assumption
    rewards, and it puts ramp-mode ``RMSE_fit`` in the same band the report
    measured (6.3 here versus 6.3 for the report's own probe).
    """

    image = Image.new("RGB", (width, height), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    font = _serif(30)
    draw.text((28, 20), "Handgloves 1984", fill=(22, 22, 26), font=font)
    draw.text((28, 80), "OO Il1 wmM 0123", fill=(22, 22, 26), font=font)
    draw.text((28, 140), "quick brown fox", fill=(22, 22, 26), font=font)
    return image


def ramp_gradient(width: int = 256, height: int = 32) -> Image.Image:
    """Black on the left to white on the right, 256 steps wide."""

    image = Image.new("RGB", (width, height), (0, 0, 0))
    pixels = image.load()
    for x in range(width):
        level = _clamp(255.0 * x / max(1, width - 1))
        for y in range(height):
            pixels[x, y] = (level, level, level)
    return image


def checker(size: int = 800, cell: int = 1) -> Image.Image:
    """A 1px checkerboard: a correct area average renders it uniform grey."""

    image = Image.new("RGB", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            on = ((x // cell) + (y // cell)) % 2 == 0
            pixels[x, y] = (255, 255, 255) if on else (0, 0, 0)
    return image


def alpha_probe(width: int = 300, height: int = 100) -> Image.Image:
    """Transparent red | opaque white | transparent black (report §4.2).

    Ground truth is ``[invisible] [VISIBLE WHITE] [invisible]``: only the middle
    third may produce ink.
    """

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    third = width // 3
    draw.rectangle((0, 0, third, height), fill=(255, 0, 0, 0))
    draw.rectangle((third, 0, third * 2, height), fill=(255, 255, 255, 255))
    draw.rectangle((third * 2, 0, width, height), fill=(0, 0, 0, 0))
    return image


def solid(value: int = 255, width: int = 100, height: int = 50) -> Image.Image:
    return Image.new("RGB", (width, height), (value, value, value))


def _wordmark(
    background: tuple, ink: tuple, width: int, height: int
) -> Image.Image:
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.text((14, 34), "Rabobank", fill=ink, font=_font(46))
    return image


def logo_light(width: int = 320, height: int = 120) -> Image.Image:
    """A light-dominant *opaque* logo: a pale mark on a near-white plate.

    No alpha, and every cell sits above mid-grey.  That is the half of the tone
    range a fixed 0.5 threshold gets wrong: a 1-bit render inked the whole plate
    as a solid block on a dark background, and vanished entirely under
    ``--background light``.  Both halves are asserted in the tests.
    """

    return _wordmark((247, 247, 245), (130, 160, 210), width, height)


def logo_dark(width: int = 320, height: int = 120) -> Image.Image:
    """A dark *opaque* logo with a narrow tonal band: a flat mark on black.

    The opaque pixels all sit near 0.22 luminance, so a fixed 0.5 threshold inks
    nothing at all and the canvas came out empty on the default background.
    This is the shape of the file the empty-canvas bug was reported with: a flat
    brand colour, no tonal range, black surround.
    """

    return _wordmark((0, 0, 0), (40, 55, 120), width, height)


def tricolor_gif(path: Path) -> Path:
    """A three-frame animated GIF, used to prove we terminate on frame one."""

    frames = [
        Image.new("RGB", (160, 160), (220, 30, 30)),
        Image.new("RGB", (160, 160), (30, 200, 60)),
        Image.new("RGB", (160, 160), (40, 60, 220)),
    ]
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return path


def exif_rotated(path: Path) -> Path:
    """A 60x30 JPEG tagged orientation 6 (rotate 90 CW on display)."""

    image = Image.new("RGB", (60, 30), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 20, 29), fill=(255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(path, exif=exif)
    return path


def big(width: int = 4000, height: int = 3000) -> Image.Image:
    """A large low-frequency source for the performance probe."""

    small = photo(256, 192)
    return small.resize((width, height), Image.BICUBIC)


FIXTURES = {
    "photo": photo,
    "shapes": shapes,
    "circle": circle,
    "text": text_image,
    "ramp": ramp_gradient,
    "checker": checker,
    "alpha": alpha_probe,
    "logo_light": logo_light,
    "logo_dark": logo_dark,
}


def write_all(directory: Path, include_big: bool = False) -> dict:
    """Materialise every fixture as a PNG and return ``{name: path}``."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, factory in FIXTURES.items():
        target = directory / f"{name}.png"
        image = factory()
        image.save(target)
        paths[name] = target
    paths["solid_white"] = directory / "solid_white.png"
    solid(255).save(paths["solid_white"])
    paths["solid_black"] = directory / "solid_black.png"
    solid(0).save(paths["solid_black"])
    paths["gif"] = tricolor_gif(directory / "tricolor.gif")
    paths["exif"] = exif_rotated(directory / "exif.jpg")
    if include_big:
        paths["big"] = directory / "big.png"
        big().save(paths["big"])
    return paths


__all__ = [
    "FIXTURES",
    "TEXT_LINES",
    "alpha_probe",
    "big",
    "checker",
    "circle",
    "exif_rotated",
    "logo_dark",
    "logo_light",
    "photo",
    "ramp_gradient",
    "shapes",
    "solid",
    "text_image",
    "tricolor_gif",
    "write_all",
]
