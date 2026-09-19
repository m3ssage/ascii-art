#!/usr/bin/env python3
"""Write the report's probe images into ``qual/images/``.

The report's own lab set lived in a scratch directory and was not committed.  So
the regression suite builds equivalent probes from code (``tests/fixtures.py``),
and this script materialises them for eyeballing and for ``qual/metric.py``.

``qual/images/`` is generated and gitignored; the fixtures are deterministic, so
regenerating gives byte-identical files.

    .venv/bin/python qual/make_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures  # noqa: E402


def main() -> int:
    target = ROOT / "qual" / "images"
    paths = fixtures.write_all(target, include_big=False)
    for name, path in sorted(paths.items()):
        print(f"{name:12s} {path.relative_to(ROOT)}")
    print(f"\nwrote {len(paths)} fixtures to {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
