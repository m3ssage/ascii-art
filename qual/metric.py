#!/usr/bin/env python3
"""The report's section 7.1 metric, adapted to compare any set of tools.

This is the script that produced the numbers in ``qual/RESULTS.md``.  It uses
the project's own metric (``ascii_art.quality``), which was validated against
the report's ImageMagick-based script to within ~0.1 RMSE on the report's own
``photo.png`` -- see ``qual/metric_magick.py`` for that verbatim cross-check.

Usage::

    .venv/bin/python qual/metric.py qual/images/photo.png
    .venv/bin/python qual/metric.py --tool jp2a:"jp2a --width=80" image.png
    ASCII_ART_INCUMBENT_PATH=/path/to/bin .venv/bin/python qual/metric.py --incumbents

With no ``--tool`` and no ``--incumbents`` it measures this implementation only,
which is what the regression baselines are derived from.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from ascii_art.quality import measure_output  # noqa: E402

#: The incumbents' command lines, exactly as the report's section 7.1 lists them.
INCUMBENT_COMMANDS: Dict[str, List[str]] = {
    "jp2a": ["jp2a", "--width=80"],
    "jp2a-invert": ["jp2a", "--width=80", "--invert"],
    "jp2a-edges": ["jp2a", "--width=80", "--edge-threshold=0.2"],
    "aic": ["ascii-image-converter", "-W", "80", "-g"],
    "aic-complex": ["ascii-image-converter", "-W", "80", "-g", "-c"],
    "aic-braille": ["ascii-image-converter", "-W", "80", "-b"],
    "aic-braille-dither": ["ascii-image-converter", "-W", "80", "-b", "--dither"],
    "chafa-ascii": [
        "chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
        "--symbols", "ascii", "-c", "none",
    ],
    "chafa-def": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max", "-c", "none"],
    "chafa-braille": [
        "chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
        "--symbols", "braille", "-c", "none",
    ],
    "chafa-block": [
        "chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
        "--symbols", "block", "-c", "none",
    ],
    "chafa-vhalf": [
        "chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
        "--symbols", "vhalf", "-c", "none",
    ],
    "img2txt": ["img2txt", "-W", "80", "-f", "utf8", "-d", "none"],
    "catimg": ["catimg", "-w", "80"],
}

OUR_COMMANDS: Dict[str, List[str]] = {
    "ascii-art-ramp": ["--width", "80"],
    "ascii-art-ramp-dither": ["--width", "80", "--dither", "diffusion"],
    "ascii-art-braille": ["--width", "80", "--mode", "braille"],
    "ascii-art-braille-dither": ["--width", "80", "--mode", "braille", "--dither", "diffusion"],
    "ascii-art-block": ["--width", "80", "--mode", "block"],
    "ascii-art-edges": ["--width", "80", "--mode", "edges"],
}


def search_path() -> str:
    extra = os.environ.get("ASCII_ART_INCUMBENT_PATH")
    if extra:
        return extra + os.pathsep + os.environ.get("PATH", "")
    return os.environ.get("PATH", "")


def run_tool(command: Sequence[str], image: Path, env: dict) -> Optional[str]:
    if not shutil.which(command[0], path=env["PATH"]):
        return None
    try:
        completed = subprocess.run(
            [*command, str(image)], capture_output=True, timeout=180, env=env
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout.decode("utf-8", "replace")


def run_ours(args: Sequence[str], image: Path) -> Optional[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "ascii_art", *args, str(image)],
        capture_output=True,
        timeout=180,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", "replace")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("images", nargs="+", metavar="IMAGE")
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="NAME:COMMAND",
        help="extra tool to measure, e.g. 'mine:./render --width 80'",
    )
    parser.add_argument(
        "--incumbents",
        action="store_true",
        help="also measure chafa, jp2a, ascii-image-converter, img2txt and catimg",
    )
    parser.add_argument("--no-ours", action="store_true", help="skip this implementation")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    env["PATH"] = search_path()
    env.pop("TERM_PROGRAM", None)
    env.pop("GHOSTTY_BIN_DIR", None)

    extra: Dict[str, List[str]] = {}
    for spec in args.tool:
        name, _, command = spec.partition(":")
        if not command:
            parser.error(f"--tool expects NAME:COMMAND, got {spec!r}")
        extra[name] = shlex.split(command)

    tools: Dict[str, Tuple[str, object]] = {}
    if not args.no_ours:
        for name, command in OUR_COMMANDS.items():
            tools[name] = ("ours", command)
    for name, command in extra.items():
        tools[name] = ("external", command)
    if args.incumbents:
        for name, command in INCUMBENT_COMMANDS.items():
            tools[name] = ("external", command)

    for image_name in args.images:
        image_path = Path(image_name)
        with Image.open(image_path) as handle:
            source = handle.convert("RGBA")
        rows = []
        for name, (kind, command) in tools.items():
            text = run_ours(command, image_path) if kind == "ours" else run_tool(command, image_path, env)
            if text is None:
                continue
            metrics = measure_output(text, source)
            rows.append((name, metrics))
        rows.sort(key=lambda item: item[1].rmse_struct)
        print(f"=== {image_name} ===")
        print(
            f"{'tool':28s} {'grid':9s} {'RMSE':>8s} {'RMSE_struct':>12s} "
            f"{'RMSE_fit':>9s} {'out_mean':>9s} {'ref_mean':>9s}"
        )
        for name, metrics in rows:
            grid = f"{metrics.cols}x{metrics.rows}"
            print(
                f"{name:28s} {grid:9s} {metrics.rmse:8.2f} {metrics.rmse_struct:12.4f} "
                f"{metrics.rmse_fit:9.2f} {metrics.out_mean:9.1f} {metrics.ref_mean:9.1f}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
