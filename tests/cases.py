"""The canonical measured cases of the regression suite.

Defined once so that ``qual/make_baselines.py`` and ``tests/test_quality.py``
always agree on what is being measured.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ascii_art import RenderOptions, render
from ascii_art.quality import measure_canvas

#: ``(case name, fixture name, RenderOptions kwargs)``
CASES: List[Tuple[str, str, dict]] = [
    ("photo_ramp", "photo", dict(width=80)),
    ("photo_ramp_diffusion", "photo", dict(width=80, dither="diffusion")),
    ("photo_ramp_ordered", "photo", dict(width=80, dither="ordered")),
    ("photo_braille", "photo", dict(width=80, mode="braille")),
    ("photo_braille_diffusion", "photo", dict(width=80, mode="braille", dither="diffusion")),
    ("photo_block", "photo", dict(width=80, mode="block")),
    ("photo_edges", "photo", dict(width=80, mode="edges")),
    ("text_ramp", "text", dict(width=80)),
    ("text_braille_diffusion", "text", dict(width=80, mode="braille", dither="diffusion")),
    ("text_block", "text", dict(width=80, mode="block")),
    ("shapes_ramp", "shapes", dict(width=80)),
    ("shapes_block", "shapes", dict(width=80, mode="block")),
    ("gradient_ramp", "ramp", dict(width=256)),
]


def measure_case(case: str, images: Dict[str, object]) -> dict:
    for name, fixture, kwargs in CASES:
        if name != case:
            continue
        image = images[fixture]
        canvas = render(image, RenderOptions(**kwargs), color_depth="none")
        metrics = measure_canvas(canvas, image)
        return {
            "fixture": fixture,
            "cols": metrics.cols,
            "rows": metrics.rows,
            "rmse": metrics.rmse,
            "rmse_struct": metrics.rmse_struct,
            "rmse_fit": metrics.rmse_fit,
            "out_mean": metrics.out_mean,
            "ref_mean": metrics.ref_mean,
            "distinct_glyphs": metrics.distinct_glyphs,
        }
    raise KeyError(case)


def measure_all(images: Dict[str, object]) -> Dict[str, dict]:
    return {name: measure_case(name, images) for name, _, _ in CASES}


__all__ = ["CASES", "measure_all", "measure_case"]
