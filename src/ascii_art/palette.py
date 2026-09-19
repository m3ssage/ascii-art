"""Colour palettes and RGB quantisation.

Depths follow the design: ``none | 2 | 8 | 16 | 256 | truecolor | auto``.
Quantisation is where ``--dither`` earns its keep for colour output: error is
diffused in RGB space before the palette lookup.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

#: Classic xterm 16-colour palette.
PALETTE_16: Tuple[RGB, ...] = (
    (0, 0, 0),
    (128, 0, 0),
    (0, 128, 0),
    (128, 128, 0),
    (0, 0, 128),
    (128, 0, 128),
    (0, 128, 128),
    (192, 192, 192),
    (128, 128, 128),
    (255, 0, 0),
    (0, 255, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 255),
)

_CUBE_STEPS = (0, 95, 135, 175, 215, 255)

INK_ON_DARK: RGB = (255, 255, 255)
INK_ON_LIGHT: RGB = (0, 0, 0)

#: The colours a terminal actually paints behind the text.  Semi-transparent
#: pixels fade toward whichever of these the user declared with --background.
TERMINAL_DARK: RGB = (0, 0, 0)
TERMINAL_LIGHT: RGB = (255, 255, 255)


@lru_cache(maxsize=1)
def palette_256() -> Tuple[RGB, ...]:
    entries: List[RGB] = list(PALETTE_16)
    for r in _CUBE_STEPS:
        for g in _CUBE_STEPS:
            for b in _CUBE_STEPS:
                entries.append((r, g, b))
    for i in range(24):
        level = 8 + i * 10
        entries.append((level, level, level))
    return tuple(entries)


def palette_for_depth(depth: str) -> Optional[Tuple[RGB, ...]]:
    if depth == "8":
        return PALETTE_16[:8]
    if depth == "16":
        return PALETTE_16
    if depth == "256":
        return palette_256()
    return None


@lru_cache(maxsize=4)
def _reverse(palette: Tuple[RGB, ...]) -> Dict[RGB, int]:
    return {rgb: i for i, rgb in enumerate(palette)}


def nearest_index(palette: Tuple[RGB, ...], rgb: RGB) -> int:
    lookup = _reverse(palette)
    hit = lookup.get(rgb)
    if hit is not None:
        return hit
    r, g, b = rgb
    best = 0
    best_d = 1 << 30
    for i, (pr, pg, pb) in enumerate(palette):
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if d < best_d:
            best_d = d
            best = i
            if d == 0:
                break
    return best


def luminance(rgb: RGB) -> float:
    """Rec. 709 relative luminance, 0-255."""

    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def quantize_rgb(
    grid: Sequence[Sequence[RGB]],
    palette: Tuple[RGB, ...],
    dither: str = "none",
    seed: Optional[int] = None,
    valid: Optional[Sequence[Sequence[bool]]] = None,
) -> List[List[RGB]]:
    """Snap every pixel to ``palette``, optionally with error diffusion.

    Returns RGB triples (not indices) so that the renderer/output layers stay
    palette-agnostic; the inverse lookup for escape generation is cached.
    """

    height = len(grid)
    if height == 0:
        return []
    width = len(grid[0])
    if valid is None:
        valid = [[True] * width for _ in range(height)]

    if dither == "none":
        return [
            [
                palette[nearest_index(palette, grid[y][x])] if valid[y][x] else grid[y][x]
                for x in range(width)
            ]
            for y in range(height)
        ]

    if dither == "ordered":
        from .dither import _BAYER8, _BAYER8_SIZE  # local import: shared matrix

        out: List[List[RGB]] = []
        for y in range(height):
            row: List[RGB] = []
            brow = _BAYER8[y % 8]
            for x in range(width):
                if not valid[y][x]:
                    row.append(grid[y][x])
                    continue
                # Ordered colour dithering: nudge each channel by a fraction of
                # the mean palette spacing before the nearest lookup.
                frac = ((brow[x % 8] + 0.5) / _BAYER8_SIZE - 0.5) * 32.0
                r, g, b = grid[y][x]
                nudged = (
                    _clamp(r + frac),
                    _clamp(g + frac),
                    _clamp(b + frac),
                )
                row.append(palette[nearest_index(palette, nudged)])
            out.append(row)
        return out

    if dither == "noise":
        import random

        rng = random.Random(seed)
        out = []
        for y in range(height):
            row = []
            for x in range(width):
                if not valid[y][x]:
                    row.append(grid[y][x])
                    continue
                r, g, b = grid[y][x]
                nudged = (
                    _clamp(r + (rng.random() - 0.5) * 32.0),
                    _clamp(g + (rng.random() - 0.5) * 32.0),
                    _clamp(b + (rng.random() - 0.5) * 32.0),
                )
                row.append(palette[nearest_index(palette, nudged)])
            out.append(row)
        return out

    if dither != "diffusion":
        from .errors import UsageError

        raise UsageError(f"unknown --dither {dither!r}")

    # Floyd-Steinberg in RGB.
    work: List[List[List[float]]] = [
        [[float(c) for c in grid[y][x]] for x in range(width)] for y in range(height)
    ]
    out = [[(0, 0, 0)] * width for _ in range(height)]

    def push(y: int, x: int, err: Sequence[float], factor: float) -> None:
        if 0 <= y < height and 0 <= x < width and valid[y][x]:
            cell = work[y][x]
            cell[0] += err[0] * factor
            cell[1] += err[1] * factor
            cell[2] += err[2] * factor

    for y in range(height):
        for x in range(width):
            if not valid[y][x]:
                out[y][x] = (0, 0, 0)
                continue
            cell = work[y][x]
            current = (_clamp(cell[0]), _clamp(cell[1]), _clamp(cell[2]))
            chosen = palette[nearest_index(palette, current)]
            out[y][x] = chosen
            err = (current[0] - chosen[0], current[1] - chosen[1], current[2] - chosen[2])
            if err == (0.0, 0.0, 0.0):
                continue
            push(y, x + 1, err, 7.0 / 16.0)
            push(y + 1, x - 1, err, 3.0 / 16.0)
            push(y + 1, x, err, 5.0 / 16.0)
            push(y + 1, x + 1, err, 1.0 / 16.0)
    return out


def _clamp(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value)


def resolve_depth(
    requested: str,
    *,
    is_tty: bool,
    fmt: str,
    env: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve ``--color auto`` to a concrete depth.

    Conservative by design (report section 4.4 is what happens when you are
    not): ``auto`` only ever *upgrades* on a TTY, and html output is the one
    case where colour is assumed because a colourless HTML file is useless.
    """

    env = os.environ if env is None else env
    if requested in ("none", "2", "8", "16", "256", "truecolor"):
        return requested
    if requested != "auto":
        from .errors import UsageError

        raise UsageError(f"unknown --color {requested!r}")

    if fmt == "html":
        return "truecolor"
    if env.get("NO_COLOR"):
        return "none"
    if not is_tty:
        return "none"
    colorterm = env.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"
    term = env.get("TERM", "").lower()
    if "256color" in term:
        return "256"
    if term in ("dumb", ""):
        return "none"
    if "color" in term or term.startswith(("xterm", "screen", "tmux", "linux", "vt")):
        return "16"
    return "none"


def stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        return False


__all__ = [
    "INK_ON_DARK",
    "INK_ON_LIGHT",
    "PALETTE_16",
    "RGB",
    "TERMINAL_DARK",
    "TERMINAL_LIGHT",
    "luminance",
    "nearest_index",
    "palette_256",
    "palette_for_depth",
    "quantize_rgb",
    "resolve_depth",
    "stdout_is_tty",
]
