#!/usr/bin/env python3
"""Contact sheet: source versus each render mode, as a PNG you can actually look at.

The report is emphatic about this (appendix 7.1): its own metric was
mis-measured twice until a human rendered the output and looked at it, and any
quality metric in this project has to be sanity-checked visually.  So this
script exists to be run by a person, not by CI.

    .venv/bin/python qual/figures.py                     # the photo probe
    .venv/bin/python qual/figures.py --fixture text --out qual/out/text.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402

from ascii_art import RenderOptions, render, to_text  # noqa: E402
from ascii_art.quality import contact_sheet  # noqa: E402

#: ``(label, RenderOptions kwargs)``
MODES: List[Tuple[str, dict]] = [
    ("ramp", {}),
    ("ramp + diffusion", {"dither": "diffusion"}),
    ("braille", {"mode": "braille"}),
    ("braille + diffusion", {"mode": "braille", "dither": "diffusion"}),
    ("block", {"mode": "block"}),
    ("edges", {"mode": "edges"}),
    ("half (colour)", {"mode": "half", "color": "256"}),
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default="photo", choices=sorted(fixtures.FIXTURES))
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    image = fixtures.FIXTURES[args.fixture]()
    outputs: List[Tuple[str, str]] = []
    for label, kwargs in MODES:
        options = RenderOptions(width=args.width, **kwargs)
        depth = kwargs.get("color", "none")
        canvas = render(image, options, color_depth=depth)
        outputs.append((f"{label} [--color {depth}]", to_text(canvas)))

    target = Path(args.out) if args.out else ROOT / "qual" / "out" / f"contact_{args.fixture}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    contact_sheet(outputs, image, str(target))
    print(f"wrote {target}")
    print("look at it: a metric that has not been eyeballed has not been checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
