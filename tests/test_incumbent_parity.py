"""Head-to-head parity against the incumbent converters.

This is the "match or beat the report's measured numbers" half of the contract,
expressed as a comparison rather than a magic constant: on shared fixtures, the
same metric, and the incumbents' own published command lines from report
section 7.1.

It needs at least one incumbent on ``PATH`` (or in ``ASCII_ART_INCUMBENT_PATH``)
and skips otherwise, so the default test run stays dependency-free.  On this
machine::

    PATH=/tmp/ascii-lab/nixprofile/bin:$PATH .venv/bin/python -m pytest tests/test_incumbent_parity.py

The recorded outcome of exactly that run is in ``qual/RESULTS.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Sequence

import pytest

from ascii_art.quality import measure_output

pytestmark = pytest.mark.parity

#: Incumbent command lines, taken from the report's section 7.1 ``TESTS`` dict.
INCUMBENTS: Dict[str, List[str]] = {
    "jp2a": ["jp2a", "--width=80"],
    "jp2a-edges": ["jp2a", "--width=80", "--edge-threshold=0.2"],
    "chafa-ascii": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                    "--symbols", "ascii", "-c", "none"],
    "chafa-def": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max", "-c", "none"],
    "chafa-braille": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                      "--symbols", "braille", "-c", "none"],
    "chafa-block": ["chafa", "-f", "symbols", "--size", "80x200", "--scale", "max",
                    "--symbols", "block", "-c", "none"],
    "aic": ["ascii-image-converter", "-W", "80", "-g"],
    "aic-braille": ["ascii-image-converter", "-W", "80", "-b"],
    "aic-braille-dither": ["ascii-image-converter", "-W", "80", "-b", "--dither"],
    "img2txt": ["img2txt", "-W", "80", "-f", "utf8", "-d", "none"],
}


def _search_path() -> str:
    extra = os.environ.get("ASCII_ART_INCUMBENT_PATH", "")
    if extra:
        return extra + os.pathsep + os.environ.get("PATH", "")
    return os.environ.get("PATH", "")


def _available() -> Dict[str, List[str]]:
    path = _search_path()
    found = {}
    for name, command in INCUMBENTS.items():
        if shutil.which(command[0], path=path):
            found[name] = command
    return found


AVAILABLE = _available()

pytestmark = [
    pytest.mark.parity,
    pytest.mark.skipif(not AVAILABLE, reason="no incumbent converters on PATH"),
]


def _run(command: Sequence[str], image, timeout: float = 180) -> Optional[str]:
    env = dict(os.environ)
    env["PATH"] = _search_path()
    env.pop("TERM_PROGRAM", None)
    env.pop("GHOSTTY_BIN_DIR", None)
    try:
        completed = subprocess.run(
            [*command, str(image)],
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - environment
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    return completed.stdout.decode("utf-8", "replace")


def _ours(args: Sequence[str], image) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "ascii_art", *args, str(image)],
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    return completed.stdout.decode("utf-8", "replace")


OUR_COMMANDS: Dict[str, List[str]] = {
    "ours-ramp": ["--width", "80"],
    "ours-ramp-dither": ["--width", "80", "--dither", "diffusion"],
    "ours-braille": ["--width", "80", "--mode", "braille"],
    "ours-braille-dither": ["--width", "80", "--mode", "braille", "--dither", "diffusion"],
    "ours-block": ["--width", "80", "--mode", "block"],
    "ours-block-dither": ["--width", "80", "--mode", "block", "--dither", "diffusion"],
    "ours-half": ["--width", "80", "--mode", "half", "--color", "256"],
}


def _scores(image_path) -> Dict[str, dict]:
    from PIL import Image

    with Image.open(image_path) as handle:
        source = handle.convert("RGBA")
    scores: Dict[str, dict] = {}
    for name, command in OUR_COMMANDS.items():
        scores[name] = measure_output(_ours(command, image_path), source).__dict__
    for name, command in AVAILABLE.items():
        text = _run(command, image_path)
        if text is None:
            continue
        scores[name] = measure_output(text, source).__dict__
    return scores


@pytest.mark.parametrize("fixture", ["photo", "shapes"])
def test_structure_and_fit_match_every_incumbent(paths, fixture):
    """Our best mode must be at least as good as the best incumbent on both axes."""

    scores = _scores(paths[fixture])
    incumbent_names = [name for name in AVAILABLE if name in scores]
    assert incumbent_names, "no incumbent produced output"
    our_names = [name for name in OUR_COMMANDS if name in scores]

    best_incumbent_struct = min(scores[name]["rmse_struct"] for name in incumbent_names)
    best_incumbent_fit = min(scores[name]["rmse_fit"] for name in incumbent_names)
    best_ours_struct = min(scores[name]["rmse_struct"] for name in our_names)
    best_ours_fit = min(scores[name]["rmse_fit"] for name in our_names)

    detail = {name: (scores[name]["rmse_struct"], scores[name]["rmse_fit"]) for name in scores}
    assert best_ours_struct <= best_incumbent_struct, detail
    assert best_ours_fit <= best_incumbent_fit, detail


@pytest.mark.parametrize("fixture", ["photo", "shapes", "text"])
def test_raw_rmse_matches_every_incumbent(paths, fixture):
    scores = _scores(paths[fixture])
    incumbent_names = [name for name in AVAILABLE if name in scores]
    our_names = [name for name in OUR_COMMANDS if name in scores]
    assert incumbent_names
    best_incumbent = min(scores[name]["rmse"] for name in incumbent_names)
    best_ours = min(scores[name]["rmse"] for name in our_names)
    assert best_ours <= best_incumbent, {n: scores[n]["rmse"] for n in scores}


def test_braille_dither_beats_the_incumbents_dither(paths):
    """Both tools expose a braille dither; ours should not be the worse one."""

    if "aic-braille-dither" not in AVAILABLE:
        pytest.skip("ascii-image-converter not available")
    scores = _scores(paths["photo"])
    assert (
        scores["ours-braille-dither"]["rmse_struct"]
        <= scores["aic-braille-dither"]["rmse_struct"]
    )
    assert scores["ours-braille-dither"]["rmse_fit"] <= scores["aic-braille-dither"]["rmse_fit"]
