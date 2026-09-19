"""The rendered result: a grid of cells, independent of output format."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

SPACE = " "


@dataclass
class Cell:
    glyph: str = SPACE
    fg: Optional[RGB] = None
    bg: Optional[RGB] = None


@dataclass
class Canvas:
    cols: int
    rows: int
    cells: List[List[Cell]]
    mode: str = "ramp"
    color_depth: str = "none"
    background: str = "dark"
    warnings: List[str] = field(default_factory=list)

    def line(self, index: int) -> str:
        return "".join(cell.glyph for cell in self.cells[index])

    def plain_text(self) -> str:
        return "\n".join(self.line(y) for y in range(self.rows))

    def ink_cells(self) -> List[Tuple[int, int]]:
        """Coordinates of every non-blank cell (used by the geometry test)."""

        out: List[Tuple[int, int]] = []
        for y in range(self.rows):
            row = self.cells[y]
            for x in range(self.cols):
                if row[x].glyph.strip():
                    out.append((x, y))
        return out


def blank_canvas(cols: int, rows: int, **kwargs) -> Canvas:
    return Canvas(
        cols=cols,
        rows=rows,
        cells=[[Cell() for _ in range(cols)] for _ in range(rows)],
        **kwargs,
    )


def average_rgb(samples: Sequence[RGB]) -> RGB:
    if not samples:
        return (0, 0, 0)
    r = sum(s[0] for s in samples) // len(samples)
    g = sum(s[1] for s in samples) // len(samples)
    b = sum(s[2] for s in samples) // len(samples)
    return (r, g, b)


__all__ = ["Cell", "Canvas", "RGB", "SPACE", "average_rgb", "blank_canvas"]
