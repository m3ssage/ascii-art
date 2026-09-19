"""The rendering pipeline: image in, :class:`~ascii_art.canvas.Canvas` out.

This is the importable library the CLI is a thin shell over (report section
6.1).  Order is deliberate and follows the report's priority list: area-average
downsampling, correct alpha, honest background polarity, then the modes.

Every mode shares one contract: a grid of *desired ink coverage* values in
``[0, 1]`` is quantised onto a small set of achievable targets by
:func:`ascii_art.dither.quantize`.  That is what makes ``--dither`` behave
identically for a 70-glyph ramp, an 8-dot braille cell and a 2-colour palette.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image

from .canvas import RGB, Canvas, average_rgb, blank_canvas
from .dither import DITHER_MODES, quantize
from .errors import UsageError
from .filters import FilterSpec, apply_filters
from .geometry import compute_geometry
from .palette import (
    INK_ON_DARK,
    INK_ON_LIGHT,
    TERMINAL_DARK,
    TERMINAL_LIGHT,
    palette_for_depth,
    quantize_rgb,
)
from .ramp import Ramp, build_ramp

MODES = ("ramp", "braille", "block", "half", "edges")
MODE_SUBSAMPLES: Dict[str, Tuple[int, int]] = {
    "ramp": (1, 1),
    "braille": (2, 4),
    "block": (2, 2),
    "half": (1, 2),
    "edges": (1, 1),
}
BACKGROUNDS = ("dark", "light", "auto")
ALPHA_MODES = ("transparent", "composite")
COLOR_DEPTHS = ("none", "2", "8", "16", "256", "truecolor", "auto")

#: Quadrant bit layout: TL=8, TR=4, BL=2, BR=1.
_QUADRANT_GLYPHS = {
    0b0000: " ",
    0b1000: "\u2598",
    0b0100: "\u259d",
    0b0010: "\u2596",
    0b0001: "\u2597",
    0b1100: "\u2580",
    0b0011: "\u2584",
    0b1010: "\u258c",
    0b0101: "\u2590",
    0b1001: "\u259a",
    0b0110: "\u259e",
    0b1110: "\u259b",
    0b1101: "\u259c",
    0b1011: "\u2599",
    0b0111: "\u259f",
    0b1111: "\u2588",
}

#: Braille dot bit for each ``(dx, dy)`` inside a 2x4 cell (Unicode order).
_BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)

#: Five tone levels for a 2x2 block cell: 0-4 quadrants lit.
_BLOCK_TARGETS = (0.0, 0.25, 0.5, 0.75, 1.0)

#: Direction glyphs indexed horizontal, vertical, slash, backslash.
_EDGE_GLYPHS = ("-", "|", "/", "\\")


@dataclass
class RenderOptions:
    """Everything the renderer needs; the CLI builds one of these."""

    mode: str = "ramp"
    chars: Optional[str] = None
    ramp_order: str = "measured"
    color: str = "none"
    dither: str = "none"
    seed: Optional[int] = None
    background: str = "dark"
    invert: bool = False
    fg_only: bool = False
    alpha: str = "transparent"
    alpha_bg: Optional[RGB] = None
    alpha_threshold: int = 128
    edge_threshold: float = 0.15

    width: Optional[int] = None
    height: Optional[int] = None
    size: Optional[str] = None
    scale: Optional[str] = None
    fit: bool = False
    stretch: bool = False
    font_ratio: float = 0.5

    filters: FilterSpec = field(default_factory=FilterSpec)
    font_path: Optional[str] = None
    is_tty: bool = False
    term: Optional[Tuple[int, int]] = None
    show_edge_threshold: bool = False

    def validate(self) -> None:
        if self.mode not in MODES:
            raise UsageError(f"unknown --mode {self.mode!r} (choose from {', '.join(MODES)})")
        if self.background not in BACKGROUNDS:
            raise UsageError(
                f"unknown --background {self.background!r} (choose dark, light or auto)"
            )
        if self.alpha not in ALPHA_MODES:
            raise UsageError(
                f"unknown --alpha {self.alpha!r} (choose transparent or composite)"
            )
        if self.color not in COLOR_DEPTHS:
            raise UsageError(
                f"unknown --color {self.color!r} "
                f"(choose from {', '.join(COLOR_DEPTHS)})"
            )
        if self.dither not in DITHER_MODES:
            raise UsageError(
                f"unknown --dither {self.dither!r} (choose from {', '.join(DITHER_MODES)})"
            )
        if not 0 <= self.alpha_threshold <= 255:
            raise UsageError("--alpha-threshold must be between 0 and 255")
        if self.edge_threshold < 0:
            raise UsageError("--edge-threshold must not be negative")
        if self.mode == "half" and self.color == "none":
            raise UsageError(
                "--mode half needs colour: half blocks carry two colour samples per "
                "cell and measure as broken in monochrome (report section 3.5). "
                "Use --mode block for monochrome sub-cell tone, or pass --color."
            )
        if self.mode == "half" and self.color in ("2",):
            raise UsageError(
                "--mode half needs at least 8 colours because a half-block cell "
                "carries two independent colour samples"
            )


def resolve_background(value: str) -> str:
    """``auto`` means dark, which is what every incumbent guesses (4.9)."""

    return "dark" if value == "auto" else value


@dataclass
class _Context:
    canvas: Canvas
    image: Image.Image
    raw: bytes  # sample-grid RGB bytes
    cell_raw: bytes  # cell-grid (cols x rows) RGB bytes
    lum: List[float]
    valid: bytes
    options: RenderOptions
    depth: str
    background: str
    desired: Callable[[float], float]
    gw: int
    gh: int


def render(
    image: Image.Image,
    options: RenderOptions,
    *,
    color_depth: Optional[str] = None,
) -> Canvas:
    """Render ``image`` according to ``options``.

    ``color_depth`` overrides ``options.color`` when the CLI has already
    resolved ``auto`` (and ``NO_COLOR``).
    """

    options.validate()

    depth = color_depth if color_depth is not None else options.color
    if depth == "auto":
        depth = "none"

    image = apply_filters(image, options.filters)

    geometry = compute_geometry(
        image.width,
        image.height,
        width=options.width,
        height=options.height,
        size=options.size,
        scale=options.scale,
        fit=options.fit,
        stretch=options.stretch,
        font_ratio=options.font_ratio,
        term=options.term,
        is_tty=options.is_tty,
    )

    background = resolve_background(options.background)
    # The terminal's own background, not the ink colour: semi-transparent pixels
    # must fade toward what the terminal paints behind the glyphs.
    assumed_bg = TERMINAL_LIGHT if background == "light" else TERMINAL_DARK
    blend_bg = options.alpha_bg or assumed_bg

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    # Transparent pixels show the terminal through, so the ink decision is made
    # against the assumed background; semi-transparent pixels fade toward it.
    # A fully opaque image needs no compositing at all, which keeps the common
    # case (an ordinary PNG or JPEG) off the alpha path entirely.
    alpha_channel = image.getchannel("A")
    if alpha_channel.getextrema() == (255, 255):
        composited = image.convert("RGB")
        alpha_mask: Optional[Image.Image] = None
    elif options.alpha == "composite":
        plate = Image.new("RGBA", image.size, (*blend_bg, 255))
        composited = Image.alpha_composite(plate, image).convert("RGB")
        alpha_mask = None
    else:
        plate = Image.new("RGBA", image.size, (*blend_bg, 0))
        composited = Image.alpha_composite(plate, image).convert("RGB")
        alpha_mask = alpha_channel

    cols, rows = geometry.cols, geometry.rows
    sub_x, sub_y = MODE_SUBSAMPLES[options.mode]
    gw, gh = cols * sub_x, rows * sub_y

    sample = composited.resize((gw, gh), Image.BOX)
    raw = sample.tobytes()
    lum = _luminance_grid(raw, gw * gh)

    if alpha_mask is not None:
        amask = alpha_mask.resize((gw, gh), Image.BOX).tobytes()
        threshold = options.alpha_threshold
        valid = bytes(1 if amask[i] >= threshold else 0 for i in range(gw * gh))
    else:
        valid = b"\x01" * (gw * gh)

    light = background == "light"

    def desired(value: float) -> float:
        return 1.0 - value if light else value

    canvas = blank_canvas(cols, rows, mode=options.mode, color_depth=depth,
                          background=background)
    cell_raw = composited.resize((cols, rows), Image.BOX).tobytes()

    ctx = _Context(
        canvas=canvas,
        image=composited,
        raw=raw,
        cell_raw=cell_raw,
        lum=lum,
        valid=valid,
        options=options,
        depth=depth,
        background=background,
        desired=desired,
        gw=gw,
        gh=gh,
    )

    handler = {
        "ramp": _render_ramp,
        "braille": _render_braille,
        "block": _render_block,
        "half": _render_half,
        "edges": _render_edges,
    }[options.mode]
    handler(ctx)
    return canvas


def _luminance_grid(raw: bytes, count: int) -> List[float]:
    out = [0.0] * count
    for i in range(count):
        o = i * 3
        out[i] = (0.2126 * raw[o] + 0.7152 * raw[o + 1] + 0.0722 * raw[o + 2]) / 255.0
    return out


def _has_ink(glyph: str) -> bool:
    """Whether a glyph puts ink on the page.

    ``U+2800 BRAILLE PATTERN BLANK`` is a real glyph with no dots; it is used to
    keep the grid's shape, so it must not attract a colour escape.
    """

    return bool(glyph.strip()) and glyph != "\u2800"


def _cell_rgb(ctx: _Context, y: int, x: int) -> RGB:
    o = (y * ctx.canvas.cols + x) * 3
    return (ctx.cell_raw[o], ctx.cell_raw[o + 1], ctx.cell_raw[o + 2])


# --------------------------------------------------------------------------
# colour attachment
# --------------------------------------------------------------------------


def _attach_cell_colors(ctx: _Context) -> None:
    """One foreground colour per non-blank cell, quantised (and dithered)."""

    canvas = ctx.canvas
    depth = ctx.depth
    if depth == "none":
        return
    if depth == "2":
        ink = INK_ON_LIGHT if ctx.background == "light" else INK_ON_DARK
        for y in range(canvas.rows):
            row = canvas.cells[y]
            for x in range(canvas.cols):
                if _has_ink(row[x].glyph):
                    row[x].fg = ink
        return

    cols, rows = canvas.cols, canvas.rows
    ink_grid = [[_has_ink(canvas.cells[y][x].glyph) for x in range(cols)] for y in range(rows)]
    grid = [[_cell_rgb(ctx, y, x) for x in range(cols)] for y in range(rows)]

    palette = palette_for_depth(depth)
    if palette is None:  # truecolor
        for y in range(rows):
            for x in range(cols):
                if ink_grid[y][x]:
                    canvas.cells[y][x].fg = grid[y][x]
        return

    quantised = quantize_rgb(
        grid, palette, dither=ctx.options.dither, seed=ctx.options.seed, valid=ink_grid
    )
    for y in range(rows):
        for x in range(cols):
            if ink_grid[y][x]:
                canvas.cells[y][x].fg = quantised[y][x]


def _attach_two_colour_grids(
    ctx: _Context,
    fg_grid: List[List[Optional[RGB]]],
    bg_grid: List[List[Optional[RGB]]],
) -> None:
    canvas = ctx.canvas
    depth = ctx.depth
    cols, rows = canvas.cols, canvas.rows

    if depth == "none":
        return
    if depth == "2":
        ink = INK_ON_LIGHT if ctx.background == "light" else INK_ON_DARK
        for y in range(rows):
            for x in range(cols):
                if fg_grid[y][x] is not None:
                    canvas.cells[y][x].fg = ink
        return

    palette = palette_for_depth(depth)
    fg_valid = [[fg_grid[y][x] is not None for x in range(cols)] for y in range(rows)]
    bg_valid = [[bg_grid[y][x] is not None for x in range(cols)] for y in range(rows)]
    fg_in = [[fg_grid[y][x] or (0, 0, 0) for x in range(cols)] for y in range(rows)]
    bg_in = [[bg_grid[y][x] or (0, 0, 0) for x in range(cols)] for y in range(rows)]

    if palette is None:
        fg_out, bg_out = fg_in, bg_in
    else:
        fg_out = quantize_rgb(
            fg_in, palette, dither=ctx.options.dither, seed=ctx.options.seed, valid=fg_valid
        )
        bg_out = quantize_rgb(
            bg_in, palette, dither=ctx.options.dither, seed=ctx.options.seed, valid=bg_valid
        )

    for y in range(rows):
        for x in range(cols):
            cell = canvas.cells[y][x]
            if fg_grid[y][x] is not None:
                cell.fg = fg_out[y][x]
            if bg_grid[y][x] is not None:
                cell.bg = bg_out[y][x]


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------


def _render_ramp(ctx: _Context) -> None:
    canvas = ctx.canvas
    cols, rows = canvas.cols, canvas.rows
    ramp: Ramp = build_ramp(ctx.options.chars, ctx.options.ramp_order, ctx.options.font_path)

    values: List[List[float]] = []
    valids: List[List[bool]] = []
    for y in range(rows):
        vrow: List[float] = []
        krow: List[bool] = []
        base = y * cols
        for x in range(cols):
            vrow.append(ctx.desired(ctx.lum[base + x]))
            krow.append(bool(ctx.valid[base + x]))
        values.append(vrow)
        valids.append(krow)

    # Glyph selection is deliberately independent of --color.  It used to be
    # otherwise for --color 2, which hard-thresholded luminance at mid-grey and
    # emitted the densest glyph or nothing.  That collapses any image whose
    # tonal band lies wholly on one side of 0.5: a flat-colour logo on black
    # renders as an empty canvas, and the same logo on white as a solid block,
    # because a fixed threshold is not a valid one-bit quantiser for an
    # arbitrary image.  Using the measured ramp fixes the whole class, and the
    # only input that can still produce an empty canvas is a single flat colour
    # -- which is what a blank canvas means.
    indices = quantize(
        values, ramp.targets, ctx.options.dither, ctx.options.seed, valids
    )
    glyphs = ramp.glyphs
    for y in range(rows):
        crow = canvas.cells[y]
        irow = indices[y]
        krow = valids[y]
        for x in range(cols):
            if krow[x]:
                crow[x].glyph = glyphs[irow[x]]

    _attach_cell_colors(ctx)


def _render_braille(ctx: _Context) -> None:
    canvas = ctx.canvas
    cols, rows = canvas.cols, canvas.rows
    gw, gh = cols * 2, rows * 4

    values: List[List[float]] = []
    valids: List[List[bool]] = []
    for y in range(gh):
        base = y * gw
        values.append([ctx.desired(ctx.lum[base + x]) for x in range(gw)])
        valids.append([bool(ctx.valid[base + x]) for x in range(gw)])

    indices = quantize(values, (0.0, 1.0), ctx.options.dither, ctx.options.seed, valids)

    for cy in range(rows):
        for cx in range(cols):
            bits = 0
            any_valid = False
            for dy in range(4):
                srow = cy * 4 + dy
                for dx in range(2):
                    sx = cx * 2 + dx
                    if not valids[srow][sx]:
                        continue
                    any_valid = True
                    if indices[srow][sx]:
                        bits |= _BRAILLE_BITS[dy][dx]
            if any_valid:
                # A braille cell with no dots is U+2800, not a space: it is a
                # real (blank) glyph, so the grid survives whitespace-stripping
                # pipelines that would otherwise eat the image's bottom rows.
                # Fully transparent cells stay a plain space, i.e. "no cell".
                canvas.cells[cy][cx].glyph = chr(0x2800 + bits)

    _attach_cell_colors(ctx)


def _render_block(ctx: _Context) -> None:
    canvas = ctx.canvas
    cols, rows = canvas.cols, canvas.rows
    gw = cols * 2

    qv: List[List[List[float]]] = []
    qk: List[List[List[bool]]] = []
    qc: List[List[List[RGB]]] = []
    for cy in range(rows):
        rv: List[List[float]] = []
        rk: List[List[bool]] = []
        rc: List[List[RGB]] = []
        for cx in range(cols):
            vs: List[float] = []
            ks: List[bool] = []
            cs: List[RGB] = []
            for dy in range(2):
                for dx in range(2):
                    i = (cy * 2 + dy) * gw + cx * 2 + dx
                    vs.append(ctx.desired(ctx.lum[i]))
                    ks.append(bool(ctx.valid[i]))
                    o = i * 3
                    cs.append((ctx.raw[o], ctx.raw[o + 1], ctx.raw[o + 2]))
            rv.append(vs)
            rk.append(ks)
            rc.append(cs)
        qv.append(rv)
        qk.append(rk)
        qc.append(rc)

    any_valid = [[any(qk[y][x]) for x in range(cols)] for y in range(rows)]
    means = [
        [
            (
                sum(qv[y][x][i] for i in range(4) if qk[y][x][i]) / sum(qk[y][x])
                if any_valid[y][x]
                else 0.0
            )
            for x in range(cols)
        ]
        for y in range(rows)
    ]

    # The cell mean gives five tone levels (0-4 quadrants); dithering the mean
    # is what keeps flat areas from collapsing to solid black or solid white.
    levels = quantize(means, _BLOCK_TARGETS, ctx.options.dither, ctx.options.seed, any_valid)

    fg_grid: List[List[Optional[RGB]]] = [[None] * cols for _ in range(rows)]
    bg_grid: List[List[Optional[RGB]]] = [[None] * cols for _ in range(rows)]

    for y in range(rows):
        for x in range(cols):
            if not any_valid[y][x]:
                continue
            n_on = levels[y][x]
            order = sorted(range(4), key=lambda i: (-qv[y][x][i], i))
            on = set(order[:n_on])
            bits = 0
            fg_samples: List[RGB] = []
            bg_samples: List[RGB] = []
            for i in range(4):
                if i in on:
                    bits |= 1 << (3 - i)
                    fg_samples.append(qc[y][x][i])
                else:
                    bg_samples.append(qc[y][x][i])
            glyph = _QUADRANT_GLYPHS[bits]
            canvas.cells[y][x].glyph = glyph
            if glyph.strip():
                if fg_samples:
                    fg_grid[y][x] = average_rgb(fg_samples)
                if bg_samples:
                    bg_grid[y][x] = average_rgb(bg_samples)

    _attach_two_colour_grids(ctx, fg_grid, bg_grid)


def _render_half(ctx: _Context) -> None:
    canvas = ctx.canvas
    cols, rows = canvas.cols, canvas.rows
    gw = cols

    top: List[List[RGB]] = []
    bottom: List[List[RGB]] = []
    for cy in range(rows):
        trow: List[RGB] = []
        brow: List[RGB] = []
        for cx in range(cols):
            o1 = ((cy * 2) * gw + cx) * 3
            o2 = ((cy * 2 + 1) * gw + cx) * 3
            trow.append((ctx.raw[o1], ctx.raw[o1 + 1], ctx.raw[o1 + 2]))
            brow.append((ctx.raw[o2], ctx.raw[o2 + 1], ctx.raw[o2 + 2]))
        top.append(trow)
        bottom.append(brow)

    palette = palette_for_depth(ctx.depth)
    if palette is not None:
        valid = [[True] * cols for _ in range(rows)]
        top = quantize_rgb(
            top, palette, dither=ctx.options.dither, seed=ctx.options.seed, valid=valid
        )
        bottom = quantize_rgb(
            bottom, palette, dither=ctx.options.dither, seed=ctx.options.seed, valid=valid
        )

    for y in range(rows):
        for x in range(cols):
            cell = canvas.cells[y][x]
            t = top[y][x]
            b = bottom[y][x]
            if t == b:
                if t == (0, 0, 0) and ctx.background == "dark":
                    continue
                cell.glyph = "\u2588"
                cell.fg = t
            else:
                cell.glyph = "\u2580"
                cell.fg = t
                cell.bg = b


def _render_edges(ctx: _Context) -> None:
    canvas = ctx.canvas
    cols, rows = canvas.cols, canvas.rows
    lum = ctx.lum

    def at(x: int, y: int) -> float:
        if x < 0 or y < 0 or x >= cols or y >= rows:
            return 0.0
        return lum[y * cols + x]

    magnitudes = [0.0] * (cols * rows)
    directions = [0] * (cols * rows)
    peak = 0.0

    for y in range(rows):
        for x in range(cols):
            tl = at(x - 1, y - 1)
            tc = at(x, y - 1)
            tr = at(x + 1, y - 1)
            ml = at(x - 1, y)
            mr = at(x + 1, y)
            bl = at(x - 1, y + 1)
            bc = at(x, y + 1)
            br = at(x + 1, y + 1)
            gx = (tr + 2 * mr + br) - (tl + 2 * ml + bl)
            gy = (bl + 2 * bc + br) - (tl + 2 * tc + tr)
            mag = math.hypot(gx, gy)
            i = y * cols + x
            magnitudes[i] = mag
            if mag > peak:
                peak = mag
            if abs(gx) >= 2.0 * abs(gy):
                directions[i] = 1  # a vertical edge reads as |
            elif abs(gy) >= 2.0 * abs(gx):
                directions[i] = 0  # a horizontal edge reads as -
            elif gx * gy >= 0:
                directions[i] = 3
            else:
                directions[i] = 2

    if peak <= 0:
        _attach_cell_colors(ctx)
        return

    cutoff = ctx.options.edge_threshold * peak
    for y in range(rows):
        for x in range(cols):
            i = y * cols + x
            if not ctx.valid[i] or magnitudes[i] < cutoff:
                continue
            canvas.cells[y][x].glyph = _EDGE_GLYPHS[directions[i]]

    _attach_cell_colors(ctx)


__all__ = [
    "ALPHA_MODES",
    "BACKGROUNDS",
    "COLOR_DEPTHS",
    "MODES",
    "MODE_SUBSAMPLES",
    "RenderOptions",
    "render",
    "resolve_background",
]
