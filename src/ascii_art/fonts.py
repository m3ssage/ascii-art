"""Monospace font discovery and glyph ink-coverage measurement.

The measured ramp (report section 4.1) needs to know how much ink each glyph
actually puts on the page.  We measure it by rasterising the glyph with a real
font rasteriser -- Pillow bundles FreeType, so this needs no extra dependency.

If no monospace font can be found the precomputed table below is used.  It was
measured on this machine with JetBrainsMono Nerd Font at a cell advance of
16.2 px, normalised so that ``U+2588 FULL BLOCK`` has coverage 1.0.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

#: Order matters: the first font that can be loaded wins.
FONT_CANDIDATES: Tuple[str, ...] = (
    "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf",
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/TTF/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
    "/usr/share/fonts/TTF/UbuntuMono-R.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
)

#: Fallback coverage table, relative to a full block.  Used only when no
#: TrueType monospace font is installed.
FALLBACK_COVERAGE: Dict[str, float] = {
    " ": 0.0,
    ".": 0.0242,
    "'": 0.0355,
    "`": 0.016,
    "^": 0.0739,
    '"': 0.0711,
    ",": 0.0378,
    ":": 0.0499,
    ";": 0.0634,
    "I": 0.1463,
    "l": 0.123,
    "!": 0.079,
    "i": 0.135,
    ">": 0.0913,
    "<": 0.0912,
    "~": 0.0644,
    "+": 0.0921,
    "_": 0.0482,
    "-": 0.0322,
    "?": 0.1096,
    "]": 0.1379,
    "[": 0.1379,
    "}": 0.1437,
    "{": 0.1408,
    "1": 0.148,
    ")": 0.1246,
    "(": 0.1247,
    "|": 0.1053,
    "\\": 0.1115,
    "/": 0.1115,
    "t": 0.1386,
    "f": 0.1413,
    "j": 0.1452,
    "r": 0.1087,
    "x": 0.1352,
    "n": 0.1448,
    "u": 0.1373,
    "v": 0.1255,
    "c": 0.1306,
    "z": 0.1353,
    "X": 0.1728,
    "Y": 0.1345,
    "U": 0.18,
    "J": 0.1222,
    "C": 0.1551,
    "L": 0.1185,
    "Q": 0.2196,
    "0": 0.2035,
    "O": 0.1908,
    "Z": 0.1563,
    "m": 0.1856,
    "w": 0.1809,
    "q": 0.183,
    "p": 0.1826,
    "d": 0.1833,
    "b": 0.1826,
    "k": 0.1672,
    "h": 0.1659,
    "a": 0.1709,
    "o": 0.1493,
    "*": 0.1224,
    "#": 0.2029,
    "M": 0.2254,
    "W": 0.2552,
    "&": 0.2146,
    "8": 0.2091,
    "%": 0.2178,
    "B": 0.2235,
    "@": 0.2677,
    "$": 0.2314,
    "\u2588": 1.0,
    "\u2591": 0.25,
    "\u2592": 0.5,
    "\u2593": 0.75,
}

_FONT_ENV_VARS = ("ASCII_ART_FONT", "ASCIIART_FONT")


def find_mono_font_path(explicit: Optional[str] = None) -> Optional[str]:
    """Return a usable monospace TrueType font path, or ``None``.

    Resolution order: explicit argument, ``ASCII_ART_FONT`` /
    ``ASCIIART_FONT`` environment variables, the built-in candidate list, then
    ``fc-match`` as a last resort.
    """

    if explicit:
        return explicit if os.path.exists(explicit) else None
    for var in _FONT_ENV_VARS:
        value = os.environ.get(var)
        if value and os.path.exists(value):
            return value
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    fc_match = shutil.which("fc-match")
    if fc_match:
        import subprocess

        try:
            out = subprocess.run(
                [fc_match, "-f", "%{file}", "monospace"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):  # pragma: no cover
            return None
        if out and os.path.exists(out):
            return out
    return None


@lru_cache(maxsize=8)
def _font(path: str, pointsize: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, pointsize)


@lru_cache(maxsize=8)
def _measure_all(path: str) -> Tuple[Dict[str, float], str]:
    """Measure every interesting glyph once per font, normalised to a block."""

    probe = ImageFont.truetype(path, 100)
    advance = probe.getlength("M" * 10) / 10.0
    if advance <= 0:  # pragma: no cover - defensive
        raise ValueError(f"font {path!r} has no advance width")
    pointsize = max(4, int(round(16 * 100.0 / advance)))
    font = _font(path, pointsize)
    cell = 64

    def ink(ch: str) -> float:
        canvas = Image.new("L", (cell * 2, cell * 2), 0)
        ImageDraw.Draw(canvas).text(
            (cell // 2, cell // 2), ch, fill=255, font=font, anchor="ls"
        )
        return sum(canvas.get_flattened_data()) / 255.0

    full = ink("\u2588") or 1.0
    table: Dict[str, float] = {}
    for ch in set(_MEASURE_ALPHABET) | {chr(c) for c in range(32, 127)}:
        if ch in ("\n", "\r", "\t"):
            continue
        table[ch] = ink(ch) / full
    return table, path


#: Extra glyphs worth measuring beyond printable ASCII (user ramps may use them).
_MEASURE_ALPHABET = " " + "".join(
    chr(cp)
    for cp in (
        list(range(0x2580, 0x25A0))  # block elements + quadrants
        + list(range(0x2591, 0x2594))  # shading
        + [0x2800, 0x28FF]  # braille
        + list(range(0x25CB, 0x25D0))  # geometric shapes
    )
)


def measure_coverage(
    glyphs: Iterable[str],
    font_path: Optional[str] = None,
    pointsize: Optional[int] = None,
) -> Tuple[Dict[str, float], bool]:
    """Measure ink coverage for ``glyphs``.

    Returns ``(coverage_map, measured)`` where ``measured`` says whether a real
    font rasteriser was used.  Missing glyphs fall back to the embedded table,
    and finally to a linear guess across the requested set.
    """

    unique: List[str] = list(dict.fromkeys(glyphs))
    path = find_mono_font_path(font_path)
    table: Dict[str, float] = {}
    measured = False
    if path:
        try:
            table, _ = _measure_all(path)
            measured = True
        except (OSError, ValueError):  # pragma: no cover - defensive
            table = {}
    values: Dict[str, float] = {}
    missing: List[str] = []
    for ch in unique:
        if ch in table:
            values[ch] = table[ch]
        elif ch in FALLBACK_COVERAGE:
            values[ch] = FALLBACK_COVERAGE[ch]
        else:
            missing.append(ch)
    if missing:
        # Last resort: spread the unknowns evenly over the unclaimed range so
        # ordering is still deterministic and monotonic.
        used = [v for v in values.values()]
        low = min(used) if used else 0.0
        high = max(used) if used else 1.0
        span = (high - low) or 1.0
        step = span / (len(missing) + 1)
        for i, ch in enumerate(missing, start=1):
            values[ch] = low + step * i
    return values, measured
