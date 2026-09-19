"""Command line interface.

A thin shell over :mod:`ascii_art.render`.  Everything the design promises
about behaviour lives here: the pipe rules, exit codes, ``NO_COLOR``, ``--help``
completeness, and the guarantee that diagnostics go to stderr only.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .errors import AsciiArtError, InputError, UsageError
from .filters import FilterSpec, validate as validate_filters
from .geometry import parse_font_ratio, terminal_box
from .loader import formats_report, load_image
from .output import FORMATS, format_canvas
from .palette import RGB, resolve_depth, stdout_is_tty
from .ramp import DEFAULT_RAMP
from .render import (
    ALPHA_MODES,
    BACKGROUNDS,
    COLOR_DEPTHS,
    MODES,
    RenderOptions,
    render,
)

PROG = "ascii-art"

EPILOG = f"""\
modes
  ramp      (default) an ordered luminance ramp; best all-rounder
  braille   2x4 dots per cell, four times the detail of a ramp
  block     2x2 quadrant blocks; the best monochrome tone reproduction
  half      half blocks carrying two colour samples per cell (colour only)
  edges     Sobel edges drawn as - | / \\ ; for line art, not photographs

ramp
  The default ramp is ordered by *measured* ink coverage, not by a hand-picked
  string.  With --ramp measured (the default) any glyph set given to --chars is
  reordered the same way, and the same measurements set the quantisation
  thresholds.  --ramp as-given keeps your order.

  default ramp: {DEFAULT_RAMP!r}

background
  --background dark  assumes a dark terminal: bright pixels get dense glyphs
  --background light assumes a light terminal: the ink polarity is flipped
  --background auto  is dark, which is what every other tool guesses

alpha
  Transparent pixels are "no ink" by default: they render as a space (or the
  terminal background in colour modes).  --alpha composite instead blends them
  onto --alpha-bg, or onto the terminal colour implied by --background
  (black for dark, white for light) when --alpha-bg is not given.

output
  stdout is plain UTF-8 unless stdout is a terminal or you ask for colour.
  --format text is always plain.  No kitty/sixel/iTerm image protocol is ever
  emitted, under any circumstances.

colour depth
  --color 2 is two colours: the terminal background, plus one ink colour
  chosen from --background (white for dark, black for light).  Tone comes from
  the glyph ramp exactly as it does with --color none, so raising or lowering
  the colour depth never adds or removes glyphs -- it only changes escapes.
  Dithering has no effect with --color truecolor, where every colour is
  representable.

exit codes
  0 success   1 usage error   2 input error

guarantees
  stdout is plain text unless colour was requested, and no pixel protocol is
  ever emitted.  No mode returns an empty canvas for an image that has content:
  when the whole tonal band would fall on one side of a mode's threshold, the
  valid range is re-mapped across that quantiser's range so the content is still
  drawn.  A flat colour still renders flat.

examples
  ascii-art logo.png
  ascii-art --width 100 --mode braille --dither diffusion photo.jpg
  ascii-art --background light --chars "@%#*+=-:. " notes.png
  cat icon.png | ascii-art --size 40x20 --color truecolor --output art.html \\
      --format html
  ascii-art --mode block --color 256 --fg-only screenshot.png | less -R
"""


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; the contract says usage errors are 1."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(1, f"{PROG}: error: {message}\n")


def _hex_colour(text: str) -> RGB:
    raw = text.strip().lstrip("#").lower()
    named = {"black": "000000", "white": "ffffff"}
    raw = named.get(raw, raw)
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        raise UsageError(f"invalid colour {text!r} (expected #RRGGBB)")
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        raise UsageError(f"invalid colour {text!r} (expected #RRGGBB)") from None


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROG,
        description=(
            "Convert images to ASCII/Unicode art.  Script-safe by design: correct "
            "exit codes, diagnostics on stderr, plain text on a pipe."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument(
        "input",
        nargs="*",
        metavar="IMAGE",
        help="image files; '-' or no argument reads stdin",
    )
    parser.add_argument(
        "--formats",
        action="store_true",
        help="print the compiled-in input formats and exit",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE", help="write to FILE instead of stdout"
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        default=None,
        help="output format: text (plain), ansi (colour escapes) or html. "
        "Defaults to ansi on a terminal and text when piped.",
    )

    mode = parser.add_argument_group("render mode")
    mode.add_argument("--mode", choices=MODES, default="ramp", help="render mode (default: ramp)")
    mode.add_argument(
        "--chars",
        metavar="STRING",
        default=None,
        help="glyph set for --mode ramp, e.g. \" .:-=+*#%%@\" (default: measured ramp)",
    )
    mode.add_argument(
        "--ramp",
        choices=("measured", "as-given"),
        default="measured",
        help="order --chars by measured ink coverage (default) or trust the given order",
    )
    mode.add_argument(
        "--edge-threshold",
        type=float,
        default=0.15,
        metavar="F",
        help="edge magnitude cutoff for --mode edges, relative to the strongest "
        "edge in the image (default: 0.15)",
    )

    tone = parser.add_argument_group("tone, colour and dithering")
    tone.add_argument(
        "--color",
        choices=COLOR_DEPTHS,
        default=None,
        metavar="{none,2,8,16,256,truecolor,auto}",
        help="colour depth; auto resolves to none unless stdout is a terminal "
        "(default: auto).  '2' means two colours -- the terminal background "
        "plus one forced ink colour -- with tone carried by the glyph ramp, "
        "so a colour depth changes escapes and never the glyphs",
    )
    tone.add_argument(
        "--dither",
        choices=("none", "ordered", "diffusion", "noise"),
        default="none",
        help="dithering algorithm; the single biggest quality lever in mono. "
        "No effect with --color truecolor, where every colour is representable "
        "(default: none)",
    )
    tone.add_argument(
        "--seed", type=int, default=None, metavar="N", help="seed for --dither noise"
    )
    tone.add_argument(
        "--background",
        choices=BACKGROUNDS,
        default="auto",
        help="terminal background, which decides ink polarity (default: auto = dark)",
    )
    tone.add_argument(
        "--invert", action="store_true", help="invert the image before rendering"
    )
    tone.add_argument(
        "--fg-only",
        action="store_true",
        help="emit foreground colours only, never background colours",
    )

    alpha = parser.add_argument_group("alpha")
    alpha.add_argument(
        "--alpha",
        choices=ALPHA_MODES,
        default="transparent",
        help="transparent pixels are no ink (default), or composite them onto a colour",
    )
    alpha.add_argument(
        "--alpha-bg",
        metavar="#RRGGBB",
        default=None,
        help="colour used to composite transparent pixels (default: terminal colour)",
    )
    alpha.add_argument(
        "--alpha-threshold",
        type=int,
        default=128,
        metavar="0-255",
        help="alpha at or above this counts as opaque (default: 128)",
    )

    size = parser.add_argument_group("sizing and geometry")
    size.add_argument("--width", type=int, default=None, metavar="N", help="output width in cells")
    size.add_argument("--height", type=int, default=None, metavar="N", help="output height in cells")
    size.add_argument(
        "--size", metavar="WxH", default=None, help="bounding box in cells, aspect preserved"
    )
    size.add_argument(
        "--scale",
        metavar="N|max",
        default=None,
        help="multiply the computed size by N, or 'max' to fill the terminal",
    )
    size.add_argument("--fit", action="store_true", help="preserve aspect inside --size (default)")
    size.add_argument("--stretch", action="store_true", help="stretch to fill --size exactly")
    size.add_argument(
        "--font-ratio",
        default="1/2",
        metavar="W/H",
        help="cell width/height ratio used for the aspect correction (default: 1/2)",
    )

    pre = parser.add_argument_group("pre-processing")
    pre.add_argument("--brightness", type=float, default=1.0, metavar="F", help="1.0 is unchanged")
    pre.add_argument("--contrast", type=float, default=1.0, metavar="F", help="1.0 is unchanged")
    pre.add_argument("--gamma", type=float, default=1.0, metavar="F", help="1.0 is unchanged")
    pre.add_argument("--rotate", type=int, choices=(90, 180, 270), default=0, metavar="DEG")
    pre.add_argument("--flip-x", action="store_true", help="mirror horizontally")
    pre.add_argument("--flip-y", action="store_true", help="mirror vertically")
    pre.add_argument(
        "--font",
        metavar="TTF",
        default=None,
        help="TrueType font used to measure glyph ink coverage",
    )

    ui = parser.add_argument_group("interface")
    ui.add_argument(
        "--polite",
        action="store_true",
        help="strip every escape that is not an SGR colour sequence, for less -R",
    )
    ui.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    return parser


def _resolve_format(args: argparse.Namespace, depth: str) -> str:
    if args.format:
        return args.format
    return "text" if depth == "none" else "ansi"


def _resolve_depth(args: argparse.Namespace, fmt_hint: str) -> str:
    env = os.environ
    explicit = args.color is not None
    requested = args.color or "auto"
    if args.format == "text":
        if explicit and requested not in ("none", "auto"):
            raise UsageError(
                f"--format text is plain UTF-8 and cannot carry colour; drop "
                f"--color {requested} or use --format ansi"
            )
        return "none"
    if env.get("NO_COLOR") and not explicit:
        return "none"
    if explicit:
        return requested
    if args.format == "html":
        return "truecolor"
    if args.format == "ansi":
        return "truecolor"
    return resolve_depth(
        "auto",
        is_tty=stdout_is_tty() and args.output is None,
        fmt=fmt_hint,
        env=env,
    )


def _build_options(args: argparse.Namespace, depth: str) -> RenderOptions:
    filters = FilterSpec(
        brightness=args.brightness,
        contrast=args.contrast,
        gamma=args.gamma,
        invert=args.invert,
        rotate=args.rotate,
        flip_x=args.flip_x,
        flip_y=args.flip_y,
    )
    validate_filters(filters)
    alpha_bg = _hex_colour(args.alpha_bg) if args.alpha_bg else None
    return RenderOptions(
        mode=args.mode,
        chars=args.chars,
        ramp_order=args.ramp,
        color=depth,
        dither=args.dither,
        seed=args.seed,
        background=args.background,
        invert=args.invert,
        fg_only=args.fg_only,
        alpha=args.alpha,
        alpha_bg=alpha_bg,
        alpha_threshold=args.alpha_threshold,
        edge_threshold=args.edge_threshold,
        width=args.width,
        height=args.height,
        size=args.size,
        scale=args.scale,
        fit=args.fit,
        stretch=args.stretch,
        font_ratio=parse_font_ratio(args.font_ratio),
        filters=filters,
        font_path=args.font,
        is_tty=stdout_is_tty() and args.output is None,
        term=terminal_box(),
    )


def _render_inputs(
    args: argparse.Namespace, options: RenderOptions, depth: str
) -> str:
    sources: List[str] = list(args.input) or ["-"]
    fmt = _resolve_format(args, depth)
    chunks: List[str] = []
    for source in sources:
        image = load_image(source)
        canvas = render(image, options, color_depth=depth)
        chunks.append(
            format_canvas(canvas, fmt, depth, fg_only=options.fg_only, polite=args.polite)
        )
    if fmt == "text":
        return "\n".join(chunk.rstrip("\n") for chunk in chunks) + "\n"
    return "\n".join(chunks)


def _write(text: str, args: argparse.Namespace) -> None:
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            raise InputError(
                f"{args.output}: cannot write output: {exc.strerror or exc}"
            ) from exc
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # `ascii-art big.png | head` is not an error.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:  # pragma: no cover - defensive
            pass
        raise SystemExit(0) from None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.formats:
        sys.stdout.write(formats_report() + "\n")
        return 0

    try:
        depth = _resolve_depth(args, "ansi")
        options = _build_options(args, depth)
        options.validate()
        text = _render_inputs(args, options, depth)
        _write(text, args)
    except AsciiArtError as exc:
        sys.stderr.write(f"{PROG}: {exc}\n")
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        sys.stderr.write(f"{PROG}: interrupted\n")
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
