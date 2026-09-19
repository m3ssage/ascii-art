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


def _valid_range(
    values: Grid, valid: List[List[bool]]
) -> Optional[tuple]:
    """``(min, max)`` over the valid samples, or ``None`` if there are none."""

    low = high = None
    for y, row in enumerate(values):
        ok = valid[y]
        for x, value in enumerate(row):
            if not ok[x]:
                continue
            if low is None:
                low = high = value
            elif value < low:
                low = value
            elif value > high:
                high = value
    if low is None:
        return None
    return low, high


def _collapsed(
    values: Grid, out: List[List[int]], valid: List[List[bool]], levels: int
) -> bool:
    """True when the canvas came out all-or-nothing for non-uniform content.

    Only the *extreme* levels count.  A constant canvas that sits at an
    intermediate level is a faithful statement -- "this image is flat at
    mid-grey" -- and must be left alone: a checkerboard whose cells average
    0.498 to 0.502 has no structure to recover, and stretching it across the
    ramp would turn 1% of numerical variation into a black-and-white pattern.
    An all-blank or all-saturated canvas, by contrast, carries no information
    at all, and that is what has to be rescued.  Uniform input is never
    rescued: a flat colour rendering flat is the right answer.
    """

    span = _valid_range(values, valid)
    if span is None or span[1] - span[0] <= 1e-9:
        return False
    first = None
    for y, row in enumerate(out):
        ok = valid[y]
        for x, index in enumerate(row):
            if not ok[x]:
                continue
            if first is None:
                first = index
            elif index != first:
                return False
    if first is None:
        return False
    return first == 0 or first == levels - 1


def _spread_to_targets(
    values: Grid, targets: Sequence[float], valid: List[List[bool]]
) -> Grid:
    """Re-map the valid range onto the quantiser's own range.

    Only ever called when the direct quantisation collapsed, so ordinary images
    keep their absolute tone mapping and are byte-for-byte unaffected.
    """

    low, high = _valid_range(values, valid)  # type: ignore[misc]
    span = high - low
    if span <= 1e-9:  # pragma: no cover - guarded by _collapsed
        return values
    target_low = targets[0]
    target_span = targets[-1] - targets[0]
    scale = target_span / span if span else 1.0
    return [
        [
            target_low + (value - low) * scale if valid[y][x] else value
            for x, value in enumerate(row)
        ]
        for y, row in enumerate(values)
    ]


def quantize(
    values: Grid,
    targets: Sequence[float],
    mode: str = "none",
    seed: Optional[int] = None,
    valid: Optional[List[List[bool]]] = None,
    *,
    recover_degenerate: bool = True,
) -> List[List[int]]:
    """Quantise a coverage grid onto ``targets``.

    ``values[y][x]`` is the desired coverage in ``[0, 1]``.  ``valid`` marks
    samples that may receive ink; invalid samples are rendered as the lowest
    target and never absorb or emit diffusion error (this is how transparent
    pixels avoid smearing dithered noise into their surroundings).

    A fixed absolute threshold is not a valid quantiser for an arbitrary image:
    content whose whole tonal band sits on one side of it collapses to an
    all-or-nothing canvas, which for a 1-bit mode means an empty one.  So when
    -- and only when -- the direct quantisation would return a constant extreme
    level for non-uniform content, the valid range is re-mapped across the
    quantiser's own range and the grid is quantised again.  Anything that
    quantises to more than one level is untouched, which keeps every measured
    baseline byte-identical.
    """

    out = _quantize_once(values, targets, mode, seed, valid)
    if recover_degenerate:
        mask = valid if valid is not None else _all_valid(values)
        if _collapsed(values, out, mask, len(targets)):
            spread = _spread_to_targets(values, targets, mask)
            out = _quantize_once(spread, targets, mode, seed, valid)
    return out


def _all_valid(values: Grid) -> List[List[bool]]:
    return [[True] * len(row) for row in values]


def _quantize_once(
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
