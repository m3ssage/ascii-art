"""``python -m ascii_art`` behaves exactly like the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
