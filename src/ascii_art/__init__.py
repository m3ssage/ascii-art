"""ascii-art: script-safe image to ASCII/Unicode art.

The library is the product; the CLI is a thin shell over it.  Typical use:

    from ascii_art import RenderOptions, load_image, render, to_text

    canvas = render(load_image("logo.png"), RenderOptions(width=80))
    print(to_text(canvas))
"""

from __future__ import annotations

__version__ = "1.0.0"

from .canvas import BRAILLE_BLANK, Canvas, Cell, has_ink
from .errors import AsciiArtError, InputError, UsageError
from .filters import FilterSpec
from .fonts import find_mono_font_path, measure_coverage
from .geometry import Geometry, compute_geometry
from .loader import load_image
from .output import format_canvas, to_ansi, to_html, to_text
from .palette import resolve_depth
from .quality import Metrics, measure_canvas, measure_lines, measure_output, render_metrics
from .ramp import DEFAULT_RAMP, Ramp, build_ramp
from .render import MODES, RenderOptions, render

__all__ = [
    "AsciiArtError",
    "BRAILLE_BLANK",
    "Canvas",
    "Cell",
    "DEFAULT_RAMP",
    "FilterSpec",
    "Geometry",
    "InputError",
    "Metrics",
    "MODES",
    "Ramp",
    "RenderOptions",
    "UsageError",
    "__version__",
    "build_ramp",
    "compute_geometry",
    "find_mono_font_path",
    "format_canvas",
    "has_ink",
    "load_image",
    "measure_coverage",
    "measure_canvas",
    "measure_lines",
    "measure_output",
    "render",
    "render_metrics",
    "resolve_depth",
    "to_ansi",
    "to_html",
    "to_text",
]
