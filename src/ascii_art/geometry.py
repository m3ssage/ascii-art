"""Sizing and geometry.

A terminal cell is roughly twice as tall as it is wide, so the aspect
correction is the thing that makes circles round (report section 2.3).  It is
a *parameter* (``--font-ratio``, default ``1/2``), not a constant.

Given a cell grid of ``cols`` by ``rows`` cells, the displayed shape has
physical aspect ``cols * font_ratio / rows``.  Setting that equal to the source
aspect ``A`` gives ``rows = cols * font_ratio / A``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional, Tuple

from .errors import InputError, UsageError

DEFAULT_FONT_RATIO = 0.5
DEFAULT_PIPED_WIDTH = 80
MAX_CELLS = 20000


@dataclass(frozen=True)
class Geometry:
    cols: int
    rows: int
    font_ratio: float


def parse_font_ratio(text: str) -> float:
    """Accept ``1/2``, ``0.5`` and ``2:1``."""

    raw = text.strip()
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            value = float(num) / float(den)
        elif ":" in raw:
            num, den = raw.split(":", 1)
            value = float(num) / float(den)
        else:
            value = float(raw)
    except (ValueError, ZeroDivisionError):
        raise UsageError(f"invalid --font-ratio {text!r} (try 1/2 or 0.5)") from None
    if not 0.05 <= value <= 20.0:
        raise UsageError(f"--font-ratio {text!r} is out of range (0.05 to 20)")
    return value


def parse_size(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse ``WxH``; either side may be empty (``x40`` or ``80x``)."""

    raw = text.strip().lower().replace(",", "x")
    if "x" not in raw:
        raise UsageError(f"invalid --size {text!r} (expected WxH, e.g. 80x40)")
    left, right = raw.split("x", 1)
    try:
        width = int(left) if left else None
        height = int(right) if right else None
    except ValueError:
        raise UsageError(f"invalid --size {text!r} (expected WxH, e.g. 80x40)") from None
    if width is None and height is None:
        raise UsageError(f"invalid --size {text!r} (expected WxH, e.g. 80x40)")
    for name, value in (("width", width), ("height", height)):
        if value is not None and value < 1:
            raise UsageError(f"--size {name} must be at least 1")
    return width, height


def terminal_box(env: Optional[dict] = None) -> Tuple[int, int]:
    """Best-effort terminal size in character cells."""

    env = os.environ if env is None else env
    columns = env.get("COLUMNS")
    lines = env.get("LINES")
    try:
        if columns and lines:
            return max(1, int(columns)), max(1, int(lines))
    except ValueError:
        pass
    size = shutil.get_terminal_size(fallback=(DEFAULT_PIPED_WIDTH, 24))
    return max(1, size.columns), max(1, size.lines)


def compute_geometry(
    src_width: int,
    src_height: int,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
    size: Optional[str] = None,
    scale: Optional[str] = None,
    fit: bool = False,
    stretch: bool = False,
    font_ratio: float = DEFAULT_FONT_RATIO,
    term: Optional[Tuple[int, int]] = None,
    is_tty: bool = True,
) -> Geometry:
    """Work out the output cell grid."""

    if src_width <= 0 or src_height <= 0:
        raise InputError("image has zero width or height")
    if stretch and fit:
        raise UsageError("--fit and --stretch are mutually exclusive")

    if size is not None:
        size_w, size_h = parse_size(size)
        if width is not None or height is not None:
            raise UsageError("--size cannot be combined with --width/--height")
    else:
        size_w, size_h = None, None

    if width is not None and width < 1:
        raise UsageError("--width must be at least 1")
    if height is not None and height < 1:
        raise UsageError("--height must be at least 1")

    aspect = src_width / src_height
    aspect = aspect if aspect > 0 else 1.0

    box_w = size_w if size_w is not None else width
    box_h = size_h if size_h is not None else height

    if scale == "max":
        term = term or terminal_box()
        box_w, box_h = term
        stretch = False

    if box_w is None and box_h is None:
        if not is_tty or term is None:
            box_w = DEFAULT_PIPED_WIDTH
        else:
            box_w = term[0]

    if box_w is not None and box_h is not None and not stretch:
        cols = max(1, int(box_w))
        rows = max(1, int(round(cols * font_ratio / aspect)))
        if rows > box_h:
            rows = max(1, int(box_h))
            cols = max(1, int(round(rows * aspect / font_ratio)))
    elif box_w is not None and box_h is not None and stretch:
        cols = max(1, int(box_w))
        rows = max(1, int(box_h))
    elif box_w is not None:
        cols = max(1, int(box_w))
        rows = max(1, int(round(cols * font_ratio / aspect)))
    else:
        rows = max(1, int(box_h))  # type: ignore[arg-type]
        cols = max(1, int(round(rows * aspect / font_ratio)))

    if scale and scale != "max":
        try:
            factor = float(scale)
        except ValueError:
            raise UsageError(
                f"invalid --scale {scale!r} (expected a number or max)"
            ) from None
        if factor <= 0:
            raise UsageError("--scale must be greater than zero")
        cols = max(1, int(round(cols * factor)))
        rows = max(1, int(round(rows * factor)))

    if cols > MAX_CELLS or rows > MAX_CELLS:
        raise UsageError(f"refusing to render more than {MAX_CELLS} cells per side")

    return Geometry(cols=cols, rows=rows, font_ratio=font_ratio)


def sample_grid(geometry: Geometry, sub_x: int, sub_y: int) -> Tuple[int, int]:
    """Sampling resolution for a mode with ``sub_x`` x ``sub_y`` samples per cell."""

    return geometry.cols * sub_x, geometry.rows * sub_y


__all__ = [
    "DEFAULT_FONT_RATIO",
    "DEFAULT_PIPED_WIDTH",
    "Geometry",
    "compute_geometry",
    "parse_font_ratio",
    "parse_size",
    "sample_grid",
    "terminal_box",
]
