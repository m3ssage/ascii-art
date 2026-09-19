"""Output formatting: plain text, ANSI, HTML.

The hard rule from the design (and from the ``chafa`` defect in report section
4.4): stdout is text unless a colour depth was asked for, and pixel-protocol
escapes are never emitted at all.  ``--polite`` strips anything that is not an
SGR colour sequence so the result is safe for ``less -R``.
"""

from __future__ import annotations

import html as _html
import re
from functools import lru_cache
from typing import List, Optional

from .canvas import RGB, Canvas
from .palette import PALETTE_16, nearest_index, palette_256

#: Any escape that is not an SGR colour sequence.
_UNSAFE_ESCAPE = re.compile(
    r"""
    \x1b\[[0-9;?]*[A-Za-ln-~]   # CSI other than ...m
  | \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)  # OSC (window title, hyperlinks, ...)
  | \x1b[()][A-Za-z0-9]        # charset selection
  | \x1b[@-Z\\-_]              # two-byte Fe escapes
  | \r
    """,
    re.VERBOSE,
)

RESET = "\x1b[0m"
FG_DEFAULT = "\x1b[39m"
BG_DEFAULT = "\x1b[49m"


@lru_cache(maxsize=4096)
def _fg_seq(rgb: RGB, depth: str, background: str) -> str:
    if depth == "2":
        return "\x1b[30m" if background == "light" else "\x1b[97m"
    if depth == "8":
        return f"\x1b[{30 + nearest_index(PALETTE_16[:8], rgb)}m"
    if depth == "16":
        index = nearest_index(PALETTE_16, rgb)
        return f"\x1b[{30 + index}m" if index < 8 else f"\x1b[{90 + index - 8}m"
    if depth == "256":
        return f"\x1b[38;5;{nearest_index(palette_256(), rgb)}m"
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


@lru_cache(maxsize=4096)
def _bg_seq(rgb: RGB, depth: str) -> str:
    if depth == "2":
        return "\x1b[107m"
    if depth == "8":
        return f"\x1b[{40 + nearest_index(PALETTE_16[:8], rgb)}m"
    if depth == "16":
        index = nearest_index(PALETTE_16, rgb)
        return f"\x1b[{40 + index}m" if index < 8 else f"\x1b[{100 + index - 8}m"
    if depth == "256":
        return f"\x1b[48;5;{nearest_index(palette_256(), rgb)}m"
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def strip_unsafe_escapes(text: str) -> str:
    """Remove every escape that is not an SGR colour sequence (and all ``\\r``)."""

    return _UNSAFE_ESCAPE.sub("", text)


def to_text(canvas: Canvas) -> str:
    """Plain UTF-8, no escapes at all.  What a pipe always gets by default."""

    return canvas.plain_text() + "\n"


def to_ansi(
    canvas: Canvas,
    depth: str = "truecolor",
    *,
    fg_only: bool = False,
    polite: bool = False,
) -> str:
    if depth == "none":
        return to_text(canvas)

    lines: List[str] = []
    for y in range(canvas.rows):
        parts: List[str] = []
        current_fg: Optional[RGB] = None
        current_bg: Optional[RGB] = None
        row = canvas.cells[y]
        for cell in row:
            glyph = cell.glyph
            if cell.fg is not None and cell.fg != current_fg:
                parts.append(_fg_seq(cell.fg, depth, canvas.background))
                current_fg = cell.fg
            elif cell.fg is None and current_fg is not None:
                parts.append(FG_DEFAULT)
                current_fg = None

            if not fg_only:
                if cell.bg is not None and cell.bg != current_bg:
                    parts.append(_bg_seq(cell.bg, depth))
                    current_bg = cell.bg
                elif cell.bg is None and current_bg is not None:
                    parts.append(BG_DEFAULT)
                    current_bg = None

            parts.append(glyph)
        if current_fg is not None or current_bg is not None:
            parts.append(RESET)
        lines.append("".join(parts))

    text = "\n".join(lines) + "\n"
    if polite:
        text = strip_unsafe_escapes(text)
    return text


def to_html(canvas: Canvas, depth: str = "truecolor", *, fg_only: bool = False) -> str:
    bg = "#000000" if canvas.background == "dark" else "#ffffff"
    fg = "#ffffff" if canvas.background == "dark" else "#000000"
    out: List[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>ascii-art</title>",
        "<style>",
        "pre.ascii-art {",
        f"  background: {bg};",
        f"  color: {fg};",
        "  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;",
        "  font-size: 14px;",
        "  line-height: 1.0;",
        "  white-space: pre;",
        "  padding: 0.5em;",
        "}",
        "</style>",
        "</head>",
        "<body>",
        '<pre class="ascii-art">',
    ]

    for y in range(canvas.rows):
        row = canvas.cells[y]
        current_fg: Optional[RGB] = None
        current_bg: Optional[RGB] = None
        buffer: List[str] = []
        row_parts: List[str] = []

        def flush() -> None:
            if not buffer:
                return
            styles = []
            if current_fg is not None:
                styles.append(f"color:#{current_fg[0]:02x}{current_fg[1]:02x}{current_fg[2]:02x}")
            if current_bg is not None and not fg_only:
                styles.append(
                    f"background-color:#{current_bg[0]:02x}{current_bg[1]:02x}{current_bg[2]:02x}"
                )
            body = _html.escape("".join(buffer))
            if styles:
                style_attr = ";".join(styles)
                row_parts.append(f'<span style="{style_attr}">{body}</span>')
            else:
                row_parts.append(body)
            buffer.clear()

        for cell in row:
            fg_changed = cell.fg != current_fg
            bg_changed = (not fg_only) and cell.bg != current_bg
            if buffer and (fg_changed or bg_changed):
                flush()
            current_fg = cell.fg
            if not fg_only:
                current_bg = cell.bg
            buffer.append(cell.glyph)
        flush()
        out.append("".join(row_parts))

    out.extend(["</pre>", "</body>", "</html>"])
    return "\n".join(out) + "\n"


FORMATS = ("text", "ansi", "html")


def format_canvas(
    canvas: Canvas,
    fmt: str,
    depth: str,
    *,
    fg_only: bool = False,
    polite: bool = False,
) -> str:
    if fmt == "text":
        return to_text(canvas)
    if fmt == "ansi":
        return to_ansi(canvas, depth, fg_only=fg_only, polite=polite)
    if fmt == "html":
        return to_html(canvas, depth, fg_only=fg_only)
    from .errors import UsageError

    raise UsageError(f"unknown --format {fmt!r} (choose from {', '.join(FORMATS)})")


__all__ = [
    "FORMATS",
    "format_canvas",
    "strip_unsafe_escapes",
    "to_ansi",
    "to_html",
    "to_text",
]
