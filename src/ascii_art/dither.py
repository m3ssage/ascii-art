"""Dithering.

The report's highest-value finding (section 4.8): dithering is the single
biggest quality lever in monochrome -- 25% better structure on a photograph and
76% on text -- and no incumbent treats it as a first-class feature.  So it is
implemented once, generically, and used by every mode.

Everything works in *target space*: a grid of desired coverage values in
``[0, 1]`` is mapped onto a sorted list of achievable targets (the ramp's
measured coverages, or ``[0, 1]`` for a two-level mode such as braille).  That
keeps :func:`quantize` identical for ramps of 70 glyphs and for 1-bit dots.
"""

from __future__ import annotations

import random
from typing import List, Optional, Sequence

from .errors import UsageError

DITHER_MODES = ("none", "ordered", "diffusion", "noise")

Grid = List[List[float]]


def _bayer(size: int = 8) -> List[List[int]]:
    if size == 1:
        return [[0]]
    half = _bayer(size // 2)
    span = len(half)
    out = [[0] * (span * 2) for _ in range(span * 2)]
    for y in range(span):
        for x in range(span):
            base = half[y][x] * 4
            out[y][x] = base
            out[y][x + span] = base + 2
            out[y + span][x] = base + 3
            out[y + span][x + span] = base + 1
    return out


_BAYER8 = _bayer(8)
_BAYER8_SIZE = 64


def _nearest(targets: Sequence[float], value: float) -> int:
    if value <= targets[0]:
        return 0
    if value >= targets[-1]:
        return len(targets) - 1
    lo, hi = 0, len(targets) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if value < targets[mid]:
            hi = mid
        else:
            lo = mid
    if value - targets[lo] <= targets[hi] - value:
        return lo
    return hi


def _mean_step(targets: Sequence[float]) -> float:
    if len(targets) < 2:
        return 1.0
    return (targets[-1] - targets[0]) / (len(targets) - 1)


def quantize(
    values: Grid,
    targets: Sequence[float],
    mode: str = "none",
    seed: Optional[int] = None,
    valid: Optional[List[List[bool]]] = None,
) -> List[List[int]]:
    """Quantise a coverage grid onto ``targets``.

    ``values[y][x]`` is the desired coverage in ``[0, 1]``.  ``valid`` marks
    samples that may receive ink; invalid samples are rendered as the lowest
    target and never absorb or emit diffusion error (this is how transparent
    pixels avoid smearing dithered noise into their surroundings).
    """

    if mode not in DITHER_MODES:
        raise UsageError(
            f"unknown --dither {mode!r} (choose from {', '.join(DITHER_MODES)})"
        )
    if not targets:
        raise UsageError("internal error: empty quantisation targets")

    height = len(values)
    if height == 0:
        return []
    width = len(values[0])
    if valid is None:
        valid = [[True] * width for _ in range(height)]

    out = [[0] * width for _ in range(height)]

    if mode == "none":
        for y in range(height):
            row = values[y]
            vrow = valid[y]
            orow = out[y]
            for x in range(width):
                orow[x] = _nearest(targets, row[x]) if vrow[x] else 0
        return out

    if len(targets) == 1:
        return out

    if mode == "ordered":
        step = _mean_step(targets)
        for y in range(height):
            brow = _BAYER8[y % 8]
            for x in range(width):
                if not valid[y][x]:
                    continue
                offset = ((brow[x % 8] + 0.5) / _BAYER8_SIZE - 0.5) * step
                out[y][x] = _nearest(targets, values[y][x] + offset)
        return out

    if mode == "noise":
        rng = random.Random(seed)
        step = _mean_step(targets)
        for y in range(height):
            for x in range(width):
                if not valid[y][x]:
                    continue
                offset = (rng.random() - 0.5) * step
                out[y][x] = _nearest(targets, values[y][x] + offset)
        return out

    # Floyd-Steinberg error diffusion.
    work = [list(row) for row in values]
    for y in range(height):
        for x in range(width):
            if not valid[y][x]:
                continue
            value = work[y][x]
            if value < 0.0:
                value = 0.0
            elif value > 1.0:
                value = 1.0
            idx = _nearest(targets, value)
            out[y][x] = idx
            err = value - targets[idx]
            if err:
                if x + 1 < width and valid[y][x + 1]:
                    work[y][x + 1] += err * 7.0 / 16.0
                if y + 1 < height:
                    if x > 0 and valid[y + 1][x - 1]:
                        work[y + 1][x - 1] += err * 3.0 / 16.0
                    if valid[y + 1][x]:
                        work[y + 1][x] += err * 5.0 / 16.0
                    if x + 1 < width and valid[y + 1][x + 1]:
                        work[y + 1][x + 1] += err * 1.0 / 16.0
    return out


def quantize_indices(indices: List[List[int]], targets: Sequence[float], mode: str,
                     seed: Optional[int] = None,
                     valid: Optional[List[List[bool]]] = None) -> List[List[int]]:
    """Convenience wrapper: dither an already-quantised index grid by coverage."""

    values = [[targets[i] for i in row] for row in indices]
    return quantize(values, targets, mode=mode, seed=seed, valid=valid)


__all__ = ["DITHER_MODES", "Grid", "quantize", "quantize_indices"]
