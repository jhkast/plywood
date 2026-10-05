"""The shopping list for hardwood: rough blanks to look for at the yard.

Hardwood comes in random widths and lengths, so instead of a cut diagram on an imagined board,
each part becomes a minimum blank (sized for milling) and identical blanks are grouped. Any
board that holds the blanks will do; the cut list is made once the real boards are entered.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from plywood.core.matching import _norm_tag, _quarters
from plywood.core.models import INCH, Settings, Unplaced

BOARD_FOOT = 144 * INCH**3


def blank_size(part, settings: Settings) -> tuple[float, float]:
    """(length, width) of the rough blank one part needs: snipe at both ends (at least the
    planer's minimum), a crosscut at each end, and one edge jointed."""
    a = settings.allowance
    length, width = part.length + a, part.width + a
    if part.grain == "width":
        length, width = width, length
    blank = max(length + 2 * (settings.snipe + settings.crosscut_kerf), settings.min_planer_length)
    return blank, width + settings.edge_joint


@dataclass
class BlankGroup:
    length: float
    width: float
    count: int = 0  # for parts of the design
    spares: int = 0
    parts: Counter = field(default_factory=Counter)  # part name -> count

    @property
    def total(self) -> int:
        return self.count + self.spares


@dataclass
class FindGroup:
    """Everything to find in one thickness and species, e.g. 8/4 white ash."""

    quarters: int
    tag: str
    blanks: list[BlankGroup]

    @property
    def key(self) -> str:
        return f"{self.quarters}|{self.tag}"

    @property
    def label(self) -> str:
        return f"{self.quarters}/4 {self.tag or 'hardwood'}"

    @property
    def thickness(self) -> float:
        return self.quarters * INCH / 4

    @property
    def board_feet(self) -> float:
        return sum(b.total * b.length * b.width for b in self.blanks) * self.thickness / BOARD_FOOT

    @property
    def longest(self) -> float:
        return max(b.length for b in self.blanks)

    @property
    def widest(self) -> float:
        return max(b.width for b in self.blanks)


def find_groups(to_find: list[Unplaced], settings: Settings, spares: bool = True) -> list[FindGroup]:
    """Group the parts to find by thickness and species, then by blank size.

    `spares`: add `spare_pct` extra blanks to each group of identical blanks. Without it, parts
    marked `spare` (the phone adds them, so they count toward "enough") are counted as spares.
    """
    groups: dict[tuple[int, str], dict[tuple[float, float], BlankGroup]] = {}
    for u in to_find:
        quarters = _quarters(u.part, settings)
        length, width = blank_size(u.part, settings)
        size = (round(length, 2), round(width, 2))
        blanks = groups.setdefault((quarters, _norm_tag(u.part.tag) or ""), {})
        b = blanks.setdefault(size, BlankGroup(length, width))
        if u.part.spare:
            b.spares += u.count
        else:
            b.count += u.count
            b.parts[u.part.name] += u.count
    out = []
    for (quarters, tag), blanks in sorted(groups.items()):
        ordered = sorted(blanks.values(), key=lambda b: (-b.length, -b.width))
        if spares:
            for b in ordered:
                b.spares = math.ceil(b.count * settings.spare_pct / 100 - 1e-9)
        out.append(FindGroup(quarters, tag, [b for b in ordered if b.total]))
    return out


def with_waste(board_feet: float, settings: Settings) -> float:
    return board_feet * (1 + settings.waste_pct / 100)
