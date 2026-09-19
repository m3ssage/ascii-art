#!/usr/bin/env python3
"""Regenerate ``tests/baselines.json``.

Run from the repository root with the project venv::

    .venv/bin/python qual/make_baselines.py

The baselines are the project's own measurements of its own deterministic
fixtures.  ``tests/test_quality.py`` fails if a future change makes any of them
worse by more than the tolerance, which is the "must not regress" half of report
section 6.3.

The report's published incumbent numbers are copied in alongside, so the
comparison the design asks for ("match or beat") stays visible in one place.
``qual/RESULTS.md`` holds the direct head-to-head on the report's own images.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402
from cases import measure_all  # noqa: E402

#: Incumbent numbers as published in the report (section 3.5 and 4.8), measured
#: on its own probe images with its own metric.  Lower is better everywhere.
REPORT_BASELINES = {
    "_comment": (
        "Measured on the report's own test images with the report's own metric. "
        "See qual/RESULTS.md for the head-to-head against this implementation."
    ),
    "photo.png": {
        "rmse": 56.39,
        "rmse_struct": 0.2329,
        "rmse_fit": 16.04,
        "best_tool": "ascii-image-converter -W 80 -g",
    },
    "shapes.png": {
        "rmse": 9.05,
        "rmse_struct": 0.0463,
        "rmse_fit": 5.61,
        "best_tool": "ascii-image-converter -W 80 -g",
    },
    "circle400.png": {
        "rmse": 73.09,
        "rmse_struct": 0.0383,
        "rmse_fit": 4.66,
        "best_tool": "ascii-image-converter -W 80 -b",
    },
    "text.png": {
        "rmse": 16.34,
        "rmse_struct": 0.1820,
        "rmse_fit": 2.74,
        "best_tool": "ascii-image-converter -W 80 -b --dither",
    },
    "text.png_ramp_mode_bar": {
        "rmse_fit": 6.0,
        "note": (
            "section 6.3.3: any ramp-mode implementation that cannot reach ~6 "
            "has not beaten the incumbents (best non-dither result was 5.86)"
        ),
    },
    "tone_bar": {
        "distinct_glyphs": 16,
        "note": "section 6.3.4: the 256-step ramp must yield >= 16 distinct glyphs",
    },
    "dither_bar": {
        "rmse_struct_improvement_percent": 25.0,
        "note": "section 6.3.4: --dither must improve RMSE_struct on the photo",
    },
    "geometry_bar": {
        "aspect_tolerance": 0.05,
        "note": "section 6.3.2: circle400 apparent pixel aspect within +/-0.05 of 1.00",
    },
}


def main() -> int:
    images = {name: factory() for name, factory in fixtures.FIXTURES.items()}
    payload = {
        "generated_by": "qual/make_baselines.py",
        "metric": "src/ascii_art/quality.py (Pillow, Lanczos downscale)",
        "cases": measure_all(images),
        "report_baselines": REPORT_BASELINES,
    }
    target = ROOT / "tests" / "baselines.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    for name, row in sorted(payload["cases"].items()):
        print(
            f"{name:26s} {row['cols']}x{row['rows']:<3} "
            f"rmse={row['rmse']:8.2f} struct={row['rmse_struct']:8.4f} "
            f"fit={row['rmse_fit']:7.2f}"
        )
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
