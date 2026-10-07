"""The shopping list for hardwood: rough pieces to look for at the yard.

Hardwood comes in random widths and lengths, so instead of a cut diagram on an imagined board,
parts become pieces sized for milling:
- parts within `combine_width` of each other's width go end to end in a strip, up to
  `combine_length` long (fewer snipe allowances, fewer short pieces);
- strips narrower than `min_piece_width` go side by side, to be ripped apart, since nobody sells
  a 1-1/2" board.
Any board that holds the pieces will do; the cut list is made once the real boards are entered.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from plywood.core.matching import _norm_tag, _quarters
from plywood.core.models import INCH, Settings, Unplaced

BOARD_FOOT = 144 * INCH**3
SPARE = " (spare)"  # name suffix of the spare parts the phone adds


def part_size(part, settings: Settings) -> tuple[float, float]:
    """(length along the grain, width) a part takes in a piece, with the rough-cut oversize."""
    al, aw = settings.allowance(part.kind)
    length, width = part.length + al, part.width + aw
    return (width, length) if part.grain == "width" else (length, width)


@dataclass
class Strip:
    """Parts end to end."""

    lengths: list[float]
    width: float  # widest part
    parts: Counter = field(default_factory=Counter)  # part name -> count
    spares: Counter = field(default_factory=Counter)

    def length(self, settings: Settings) -> float:
        """With a crosscut between parts and snipe at each end, and at least the planer's minimum."""
        k = settings.crosscut_kerf
        run = sum(self.lengths) + k * (len(self.lengths) - 1)
        return max(run + 2 * (settings.snipe + k), settings.min_planer_length)


@dataclass
class Piece:
    """A rough piece to find: strips side by side, plus extra width to joint and rip."""

    strips: list[Strip]
    count: int = 1  # pieces of this size
    also: list[Piece] = field(default_factory=list)  # the other pieces of this size

    def length(self, settings: Settings) -> float:
        return max(s.length(settings) for s in self.strips)

    def full_width(self, settings: Settings) -> float:
        width = sum(s.width for s in self.strips) + settings.rip_kerf * (len(self.strips) - 1)
        return max(width + settings.piece_extra_width, settings.min_piece_width)

    @property
    def parts(self) -> Counter:
        """In all `count` pieces."""
        return sum((s.parts for p in (self, *self.also) for s in p.strips), Counter())

    @property
    def spares(self) -> Counter:
        return sum((s.spares for p in (self, *self.also) for s in p.strips), Counter())


@dataclass
class FindGroup:
    """Everything to find in one thickness and species, e.g. 8/4 white ash."""

    quarters: int
    tag: str
    pieces: list[Piece]
    settings: Settings
    spare_of: dict[str, str] = field(default_factory=dict)  # spare label -> a part it stands for

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
        s = self.settings
        return sum(p.count * p.length(s) * p.full_width(s) for p in self.pieces) * self.thickness / BOARD_FOOT

    @property
    def longest(self) -> float:
        return max(p.length(self.settings) for p in self.pieces)

    @property
    def widest(self) -> float:
        return max(p.full_width(self.settings) for p in self.pieces)


def _strips(items: list[tuple[str, float, float, bool]], settings: Settings) -> list[Strip]:
    """First fit, widest parts first: a part joins the first strip it's close enough in width
    to and that stays within the longest piece to look for. Spares go last."""
    strips: list[Strip] = []
    for name, length, width, spare in sorted(items, key=lambda it: (-it[2], -it[1], it[3], it[0])):
        for s in strips:
            if s.width - width <= settings.combine_width + 1e-6:
                s.lengths.append(length)
                if s.length(settings) <= settings.combine_length + 1e-6:
                    break
                s.lengths.pop()
        else:
            s = Strip([length], width)
            strips.append(s)
        (s.spares if spare else s.parts)[name] += 1
    return strips


def _pieces(strips: list[Strip], settings: Settings) -> list[Piece]:
    """Narrow strips side by side, longest first, until the piece is wide enough to find."""
    wide = lambda width: width + settings.piece_extra_width >= settings.min_piece_width - 1e-6  # noqa: E731
    pieces = [Piece([s]) for s in strips if wide(s.width)]
    narrow = sorted((s for s in strips if not wide(s.width)), key=lambda s: -s.length(settings))
    while narrow:
        group = [narrow.pop(0)]
        while narrow and not wide(sum(s.width for s in group) + settings.rip_kerf * len(group)):
            group.append(narrow.pop(0))
        pieces.append(Piece(group))
    # Pieces of the same size are listed once, with a count and everything they're for.
    merged: dict[tuple, Piece] = {}
    for p in pieces:
        key = (round(p.length(settings), 2), round(p.full_width(settings), 2))
        if key in merged:
            merged[key].count += 1
            merged[key].also.append(p)
        else:
            merged[key] = p
    return sorted(merged.values(), key=lambda p: (-p.length(settings), -p.full_width(settings)))


def _common_name(names: list[str]) -> str:
    """What parts of one size have in common: 'Back Left Leg - Back', 'Front Right Leg - Front'
    -> 'Leg'. The first name if nothing is shared."""
    names = sorted(set(names))
    if len(names) == 1:
        return names[0]
    words = [re.findall(r"[\w']+", n) for n in names]
    common = [w for w in words[0] if all(w in other for other in words[1:])]
    return " ".join(dict.fromkeys(common)) or names[0]


def find_groups(to_find: list[Unplaced], settings: Settings, spares: bool = True) -> list[FindGroup]:
    """Group the parts to find by thickness and species, then put them into pieces.

    `spares`: add `spare_pct` extra of each part size, rounded up. Without it, parts marked
    `spare` (the phone adds them, so they count toward "enough") are the spares.
    """
    groups: dict[tuple[int, str], list[tuple[str, float, float, bool]]] = {}
    for u in to_find:
        length, width = part_size(u.part, settings)
        name = u.part.name.removesuffix(SPARE)
        items = groups.setdefault((_quarters(u.part, settings), _norm_tag(u.part.tag) or ""), [])
        items += [(name, length, width, u.part.spare)] * u.count
    out = []
    for (quarters, tag), items in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):  # species, then thickness
        spare_of: dict[str, str] = {}
        if spares:
            # Per size, not per name: eight legs named one by one still get one spare.
            sizes: dict[tuple[float, float], list[str]] = {}
            exact: dict[tuple[float, float], tuple[float, float]] = {}
            for name, ln, w, spare in items:
                if not spare:
                    size = (round(ln, 2), round(w, 2))
                    sizes.setdefault(size, []).append(name)
                    exact.setdefault(size, (ln, w))
            for size, names in sizes.items():
                n = math.ceil(len(names) * settings.spare_pct / 100 - 1e-9)
                if n:
                    label = _common_name(names)
                    if label in spare_of:  # another size already goes by that name
                        label = min(names)
                    spare_of[label] = min(names)
                    items = items + [(label, *exact[size], True)] * n
        out.append(FindGroup(quarters, tag, _pieces(_strips(items, settings), settings), settings, spare_of))
    return out


def with_waste(board_feet: float, settings: Settings) -> float:
    return board_feet * (1 + settings.waste_pct / 100)
