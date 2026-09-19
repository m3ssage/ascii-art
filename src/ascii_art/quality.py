"""The quality metric of report section 7.1.

An ASCII renderer's job is to make its glyph grid *look like* the source.  So:

1. capture the tool's text output (ANSI stripped),
2. rasterise it into an image, one glyph per cell, monospace font on black,
3. box-downscale that raster back to the cell grid -> a luminance per cell,
4. compare against the source image box-downscaled to the same grid.

``rmse`` is in 0-255 luminance units (lower is better).  ``rmse_struct`` is the
same comparison after removing mean and contrast, so it isolates structure.
``rmse_fit`` applies a best-fit affine brightness/contrast match first, which is
the fairest single number: "up to overall brightness and contrast, how well
does this reproduce the source?"

Two deliberate adaptations from the report's script, both noted here because the
report warns the metric must be sanity-checked rather than trusted:

* it is implemented with Pillow instead of ImageMagick, so the test suite needs
  no external tools.  Two details keep it faithful to the report's script: the
  cell size is derived from the font's *measured* monospace advance width (which
  is what makes block glyphs measured fairly), and images are downscaled with
  Lanczos, which is what ImageMagick's default resize filter does.  Validated
  against the report's own script on the report's own ``photo.png``:
  magick ``rmse=113.44 rmse_struct=0.1498 rmse_fit=10.36`` versus this module's
  ``113.21 / 0.1501 / 10.24``.

``qual/metric_magick.py`` reproduces the report's original script verbatim in
methodology for cross-checking against its published numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .fonts import find_mono_font_path

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

#: Cell width in the raster, in pixels.  Cell height is twice this, because a
#: real terminal cell is roughly 1:2.
CELL_WIDTH = 16


@dataclass(frozen=True)
class Metrics:
    cols: int
    rows: int
    rmse: float
    rmse_struct: float
    rmse_fit: float
    out_mean: float
    ref_mean: float
    distinct_glyphs: int

    def as_row(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"{self.cols}x{self.rows:<6} {self.rmse:7.2f} {self.rmse_struct:12.4f} "
            f"{self.rmse_fit:9.2f} {self.out_mean:9.1f} {self.ref_mean:9.1f}"
        )


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def grid_lines(text: str, trim: bool = True) -> List[str]:
    """Split into a rectangular grid.

    ``trim`` (the default) drops trailing blank lines, which is what the
    report's script does and what makes the metric usable against third-party
    tools that may or may not emit a trailing newline.  Pass ``trim=False`` when
    the grid is already known exactly -- trailing blank rows are real geometry,
    and stripping them silently rescales the reference image.
    """

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if trim:
        while lines and not lines[-1].strip():
            lines.pop()
    if not lines:
        return []
    width = max(len(line) for line in lines)
    return [line.ljust(width) for line in lines]


@lru_cache(maxsize=8)
def font_for_cell(path: str, cell_width: int) -> ImageFont.FreeTypeFont:
    probe = ImageFont.truetype(path, 100)
    advance = probe.getlength("M" * 10) / 10.0 or 1.0
    pointsize = max(1, int(round(cell_width * 100.0 / advance)))
    return ImageFont.truetype(path, pointsize)


def rasterise(
    lines: Sequence[str],
    font_path: Optional[str] = None,
    cell_width: int = CELL_WIDTH,
) -> Optional[Image.Image]:
    """Draw ``lines`` one monospace glyph per cell, white on black."""

    if not lines:
        return None
    path = find_mono_font_path(font_path)
    if path is None:  # pragma: no cover - environment dependent
        raise RuntimeError("no monospace font available to rasterise the metric")
    font = font_for_cell(path, cell_width)
    cell_height = cell_width * 2
    rows = len(lines)
    cols = len(lines[0])
    image = Image.new("RGB", (cols * cell_width, rows * cell_height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    baseline = int(cell_height * 0.80)
    for r, line in enumerate(lines):
        y = r * cell_height + baseline
        for c, ch in enumerate(line):
            if ch in " \t":
                continue
            draw.text((c * cell_width, y), ch, fill=(255, 255, 255), font=font, anchor="ls")
    return image


def _luminance_bytes(image: Image.Image, cols: int, rows: int) -> bytes:
    # Lanczos, not BOX: ImageMagick's default resize filter (which the report's
    # script used) behaves like Lanczos, and the published incumbent numbers are
    # only comparable if the metric downsamples the same way.  Measured against
    # the report's own script on its own test image the two agree to ~0.1 RMSE.
    small = image.convert("L").resize((cols, rows), Image.LANCZOS)
    return small.tobytes()


def _stats(got: bytes, ref: bytes) -> Tuple[float, float, float, float, float]:
    n = min(len(got), len(ref))
    if n == 0:
        raise ValueError("nothing to compare")
    got = got[:n]
    ref = ref[:n]

    total_sq = 0
    sum_got = 0
    sum_ref = 0
    for i in range(n):
        g = got[i]
        r = ref[i]
        total_sq += (g - r) * (g - r)
        sum_got += g
        sum_ref += r
    rmse = (total_sq / n) ** 0.5
    mean_got = sum_got / n
    mean_ref = sum_ref / n

    var_got = sum((g - mean_got) ** 2 for g in got)
    var_ref = sum((r - mean_ref) ** 2 for r in ref)
    std_got = (var_got / n) ** 0.5 or 1.0
    std_ref = (var_ref / n) ** 0.5 or 1.0
    struct = 0.0
    for i in range(n):
        struct += ((got[i] - mean_got) / std_got - (ref[i] - mean_ref) / std_ref) ** 2
    rmse_struct = (struct / n) ** 0.5

    # Best affine brightness/contrast match: a*got + b closest to ref.
    a = 0.0
    if var_got:
        cov = 0.0
        for i in range(n):
            cov += (got[i] - mean_got) * (ref[i] - mean_ref)
        a = cov / var_got
    b = mean_ref - a * mean_got
    fit = 0.0
    for i in range(n):
        d = a * got[i] + b - ref[i]
        fit += d * d
    rmse_fit = (fit / n) ** 0.5
    return rmse, rmse_struct, rmse_fit, mean_got, mean_ref


def measure_lines(
    lines: Sequence[str],
    source: Image.Image,
    font_path: Optional[str] = None,
    cell_width: int = CELL_WIDTH,
) -> Metrics:
    """Score an explicit glyph grid against ``source``.

    No trimming, no guessing: ``lines`` is exactly the grid the renderer
    produced, so ``cols``/``rows`` are the real geometry.
    """

    lines = [line for line in lines]
    if not lines:
        raise ValueError("no output to measure")
    width = max(len(line) for line in lines)
    lines = [line.ljust(width) for line in lines]
    return _measure(lines, source, font_path, cell_width)


def measure_canvas(
    canvas,
    source: Image.Image,
    font_path: Optional[str] = None,
    cell_width: int = CELL_WIDTH,
) -> Metrics:
    """Score a :class:`~ascii_art.canvas.Canvas` against ``source``."""

    return measure_lines(
        [canvas.line(y) for y in range(canvas.rows)], source, font_path, cell_width
    )


def _measure(
    lines: Sequence[str],
    source: Image.Image,
    font_path: Optional[str],
    cell_width: int,
) -> Metrics:
    rows = len(lines)
    cols = len(lines[0])
    raster = rasterise(lines, font_path, cell_width)
    assert raster is not None
    got = _luminance_bytes(raster, cols, rows)
    ref = _luminance_bytes(source, cols, rows)
    rmse, rmse_struct, rmse_fit, out_mean, ref_mean = _stats(got, ref)
    distinct = len({ch for line in lines for ch in line if ch.strip()})
    return Metrics(
        cols=cols,
        rows=rows,
        rmse=round(rmse, 2),
        rmse_struct=round(rmse_struct, 4),
        rmse_fit=round(rmse_fit, 2),
        out_mean=round(out_mean, 1),
        ref_mean=round(ref_mean, 1),
        distinct_glyphs=distinct,
    )


def measure_output(
    text: str,
    source: Image.Image,
    font_path: Optional[str] = None,
    cell_width: int = CELL_WIDTH,
) -> Metrics:
    """Score rendered ``text`` against ``source`` (report-faithful trimming)."""

    lines = grid_lines(strip_ansi(text))
    if not lines:
        raise ValueError("no output to measure")
    return _measure(lines, source, font_path, cell_width)


def render_metrics(
    source: Image.Image,
    options,
    font_path: Optional[str] = None,
    *,
    color_depth: Optional[str] = None,
) -> Metrics:
    """Render ``source`` with ``options`` and immediately score the result."""

    from .render import render

    canvas = render(source, options, color_depth=color_depth)
    return measure_canvas(canvas, source, font_path)


def contact_sheet(
    outputs: Sequence[Tuple[str, str]],
    source: Image.Image,
    path: str,
    font_path: Optional[str] = None,
    cell_width: int = CELL_WIDTH,
) -> None:
    """Write a reference-vs-tools contact sheet PNG (the report's figures.py).

    ``outputs`` is a sequence of ``(label, text)``.  Always look at the result:
    the report's own metric was mis-measured twice until a human eyeballed it.
    """

    tiles: List[Tuple[str, Image.Image]] = []
    lines = grid_lines(strip_ansi(outputs[0][1])) if outputs else []
    if lines:
        reference = source.convert("RGB")
        tiles.append(("source", reference))
    for label, text in outputs:
        raster = rasterise(grid_lines(strip_ansi(text)), font_path, cell_width)
        if raster is not None:
            tiles.append((label, raster))
    if not tiles:
        raise ValueError("nothing to draw")

    width = max(t.width for _, t in tiles)
    total_height = sum(t.height + 18 for _, t in tiles)
    sheet = Image.new("RGB", (width, total_height), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    try:
        label_font = ImageFont.truetype(find_mono_font_path(font_path) or "", 12)
    except (OSError, TypeError):  # pragma: no cover - environment dependent
        label_font = ImageFont.load_default()
    y = 0
    for label, tile in tiles:
        draw.text((2, y + 2), label, fill=(255, 220, 120), font=label_font)
        sheet.paste(tile, (0, y + 16))
        y += tile.height + 18
    sheet.save(path)


__all__ = [
    "ANSI_RE",
    "CELL_WIDTH",
    "Metrics",
    "contact_sheet",
    "font_for_cell",
    "grid_lines",
    "measure_canvas",
    "measure_lines",
    "measure_output",
    "rasterise",
    "render_metrics",
    "strip_ansi",
]
