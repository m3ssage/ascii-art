"""The luminance ramp: which glyphs, in which order, and with which thresholds.

The report's differentiator #3: every incumbent hard-codes a ramp string and
nobody publishes how the order was derived.  Here the order comes from
*measured ink coverage* (see :mod:`ascii_art.fonts`), and the same measurement
supplies the quantisation thresholds -- a glyph that lays down 30% ink should
represent 30% tone, not "index 7 of 23".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .errors import UsageError
from .fonts import measure_coverage

#: Default ramp *set*.  With ``--ramp measured`` (the default) the order is
#: decided by measurement, not by this string's order.
DEFAULT_RAMP = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"


@dataclass(frozen=True)
class Ramp:
    """An ordered glyph set plus the coverage targets used for quantisation."""

    glyphs: Tuple[str, ...]
    #: Coverage of each glyph normalised to ``[0, 1]`` where 1.0 is the densest
    #: glyph in this ramp.
    targets: Tuple[float, ...]
    #: Whether the order/thresholds came from a real font rasteriser.
    measured: bool

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.glyphs)

    @property
    def levels(self) -> int:
        return len(self.glyphs)

    def index_for(self, value: float) -> int:
        """Map a desired coverage in ``[0, 1]`` to the nearest glyph index."""

        targets = self.targets
        if value <= targets[0]:
            return 0
        if value >= targets[-1]:
            return len(targets) - 1
        # ``targets`` is sorted ascending; binary search the midpoint bounds.
        lo, hi = 0, len(targets) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if value < targets[mid]:
                hi = mid
            else:
                lo = mid
        # Pick whichever neighbour is closer.
        if value - targets[lo] <= targets[hi] - value:
            return lo
        return hi

    def step(self, index: int) -> float:
        """Local spacing around ``index``; used to scale dither noise."""

        targets = self.targets
        n = len(targets)
        if n < 2:
            return 1.0
        if index <= 0:
            return max(targets[1] - targets[0], 1e-6)
        if index >= n - 1:
            return max(targets[-1] - targets[-2], 1e-6)
        return max((targets[index + 1] - targets[index - 1]) / 2.0, 1e-6)


def _unique(chars: str) -> List[str]:
    out: List[str] = []
    for ch in chars:
        if ch not in out:
            out.append(ch)
    return out


def build_ramp(
    chars: Optional[str] = None,
    order: str = "measured",
    font_path: Optional[str] = None,
) -> Ramp:
    """Build a :class:`Ramp`.

    ``chars`` is the glyph set (``--chars``); ``None`` means :data:`DEFAULT_RAMP`.
    ``order`` is ``"measured"`` (sort by ink coverage) or ``"as-given"``.
    """

    if order not in ("measured", "as-given"):
        raise UsageError(f"unknown --ramp order {order!r} (use measured or as-given)")

    glyphs = _unique(chars if chars is not None else DEFAULT_RAMP)
    if len(glyphs) < 2:
        raise UsageError("--chars needs at least two distinct characters")

    coverage, measured = measure_coverage(glyphs, font_path=font_path)

    if order == "measured":
        # Stable sort keeps the author's order for glyphs of equal coverage.
        glyphs = sorted(glyphs, key=lambda ch: coverage[ch])

    raw = [max(0.0, coverage[ch]) for ch in glyphs]
    top = max(raw) or 1.0
    targets = [v / top for v in raw]

    # Guarantee strictly ascending targets so index_for/step stay well defined
    # even when a font reports identical coverage for neighbouring glyphs.
    fixed: List[float] = [0.0]
    n = len(targets)
    for i in range(1, n):
        floor = fixed[-1] + 1e-4
        fixed.append(max(targets[i], floor))
    if fixed[-1] > 0:  # renormalise after the monotonic repair
        scale = fixed[-1]
        fixed = [v / scale for v in fixed]

    return Ramp(glyphs=tuple(glyphs), targets=tuple(fixed), measured=measured)


def ramp_report(ramp: Ramp) -> List[Tuple[str, float]]:
    """``(glyph, coverage)`` pairs, for ``--ramp-debug`` style output/tests."""

    return list(zip(ramp.glyphs, ramp.targets))


def coverage_map(chars: str, font_path: Optional[str] = None) -> Dict[str, float]:
    """Raw measured coverage for a glyph set (used by tests and the ramp report)."""

    values, _ = measure_coverage(_unique(chars), font_path=font_path)
    return values


__all__ = [
    "DEFAULT_RAMP",
    "Ramp",
    "build_ramp",
    "coverage_map",
    "ramp_report",
]
