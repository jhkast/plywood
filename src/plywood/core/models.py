"""Input and output data types. All lengths are millimetres.

Orientation conventions:
- Stock `length` runs along its grain and maps to the x axis of a layout.
- A part's `grain` says which of its own dimensions must run along the stock grain.
- A horizontal cut (parallel to x, along the grain) is a *rip*; a vertical cut is a *crosscut*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

INCH = 25.4
SHEET_4X8 = (96 * INCH, 48 * INCH)


class Grain(StrEnum):
    NONE = "none"  # free to rotate
    LENGTH = "length"  # part length runs along the stock grain
    WIDTH = "width"  # part width runs along the stock grain

    @classmethod
    def parse(cls, text: str | None) -> Grain:
        t = (text or "").strip().lower()
        if t in ("", "none", "n", "no", "-", "any"):
            return cls.NONE
        if t in ("length", "l", "len", "long"):
            return cls.LENGTH
        if t in ("width", "w", "wide"):
            return cls.WIDTH
        raise ValueError(f"unknown grain {text!r} (use none, length, or width)")


class StockKind(StrEnum):
    SHEET = "sheet"
    DIMENSIONAL = "dimensional"  # surfaced lumber in standard sizes (2x4, 1x6…), always buyable
    HARDWOOD = "hardwood"  # sold rough by the board foot; what the yard has is found, not ordered

    @property
    def is_board(self) -> bool:
        return self is not StockKind.SHEET

    @classmethod
    def parse(cls, text: str | None) -> StockKind:
        t = (text or "").strip().lower()
        if t in ("", "sheet", "s", "ply", "plywood", "panel"):
            return cls.SHEET
        if t in ("dimensional", "dim", "d", "dimension"):
            return cls.DIMENSIONAL
        if t in ("hardwood", "h", "hw", "board", "b", "lumber", "wood", "solid"):  # "board": older files
            return cls.HARDWOOD
        raise ValueError(f"unknown kind {text!r} (use sheet, dimensional or hardwood)")


@dataclass(frozen=True)
class Part:
    name: str
    length: float
    width: float
    thickness: float
    qty: int = 1
    grain: Grain = Grain.NONE
    tag: str | None = None
    kind: StockKind = StockKind.SHEET  # a part is only ever cut from stock of the same kind
    spare: bool = False  # an extra blank to find (shopping only), not a part of the design


@dataclass(frozen=True)
class Stock:
    name: str
    length: float  # along the grain
    width: float
    thickness: float
    qty: int | None = None  # on-hand count; None means buy as many as needed
    tag: str | None = None
    kind: StockKind = StockKind.SHEET
    # Edges to trim, as seen in the diagram: l/r = the ends, b/t = the long sides. None = no edges.
    trim_edges: str | None = None
    rough: bool = False  # rough-sawn board: every part from it is jointed and planed

    @property
    def on_hand(self) -> bool:
        return self.qty is not None


@dataclass
class Settings:
    sheet_kerf: float = 0.125 * INCH  # every sheet cut (track or table saw)
    rip_kerf: float = 0.125 * INCH  # board rips (table saw)
    crosscut_kerf: float = 0.125 * INCH  # board crosscuts (miter saw)
    rough_crosscut_kerf: float = 0.125 * INCH  # cutting a rough board into segments (jig saw)
    edge_trim: float = 0.0  # removed from each trimmed edge (stock rows choose which edges)
    allowance: float = 0.0  # rough-cut oversize, total per dimension (parts cut this much bigger)
    thickness_tolerance: float = 0.5  # mm
    default_sheets: bool = True  # add unlimited 4x8 sheets for thicknesses with no stock
    default_dimensional: bool = True  # add standard dimensional sizes for parts with no stock
    min_offcut: float = 4 * INCH  # offcuts smaller than this in either dimension are scrap
    # Lumber
    max_planing: float = 0.25 * INCH  # a board can be at most this much thicker than its parts
    rough_cleanup: float = 0.125 * INCH  # the least a rough board loses to face jointing and planing
    edge_joint: float = INCH / 16  # taken off one edge of a rough segment by the jointer
    min_planer_length: float = 18 * INCH
    snipe: float = 4 * INCH  # extra length at each end of a planed segment, cut off after planing
    dimensional_lengths: tuple[float, ...] = (96 * INCH, 120 * INCH, 144 * INCH)  # lengths you can buy
    # Extra wood on the shopping list only (never laid out in the cut list)
    spare_sheets: int = 0  # per kind of sheet bought
    spare_sticks: int = 1  # per dimensional size bought
    spare_pct: float = 10.0  # extra hardwood blanks per group of identical blanks, rounded up
    waste_pct: float = 25.0  # added to the hardwood board-foot estimate (defects, odd widths)
    tries: int = 1000  # random layouts tried after the fixed sweep
    priority: str = "waste"  # after fewest sheets: "waste", "balanced", or "cuts"
    seed: int = 0  # same inputs + same seed = same layout


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass(frozen=True)
class Placement:
    part: Part
    copy: int  # 1-based index among this part's qty
    rect: Rect
    rotated: bool  # True when the part's width runs along x

    @property
    def label(self) -> str:
        return self.part.name if self.part.qty == 1 else f"{self.part.name} #{self.copy}"


@dataclass(frozen=True)
class Segment:
    """One piece produced by a cut step, measured along the cut axis."""

    size: float
    kind: str  # "part", "piece" (needs more cuts), "offcut", "scrap"
    label: str = ""
    final: tuple[float, float] | None = None  # finished part size, as oriented, when cut oversize


@dataclass(frozen=True)
class CutStep:
    number: int
    direction: str  # "rip", "crosscut", "trim" (all edges of the sheet), or "plane"
    piece: Rect  # the piece being cut
    positions: tuple[float, ...]  # absolute coordinates where each kerf starts
    kerf: float
    segments: tuple[Segment, ...]
    piece_label: str = ""  # "" for the whole stock piece, else the letter given when it was cut off
    thickness: float = 0.0  # "plane": the target thickness
    jointed: bool = False  # "plane": a face and an edge are jointed first


@dataclass
class Layout:
    number: int
    stock: Stock
    trims: tuple[float, float, float, float]  # removed from the left, right, bottom, top edges
    placements: list[Placement]
    steps: list[CutStep]
    offcuts: list[Rect]  # usable leftovers
    scrap: list[Rect]  # leftovers below min_offcut
    tag: str | None = None  # material this piece was used as (from the stock or its parts)
    cuts: int = 0  # saw passes

    @property
    def parts_area(self) -> float:
        return sum(p.rect.area for p in self.placements)

    @property
    def area(self) -> float:
        return self.stock.length * self.stock.width

    @property
    def waste_pct(self) -> float:
        return 100.0 * (1 - self.parts_area / self.area)


@dataclass(frozen=True)
class Unplaced:
    part: Part
    count: int
    reason: str


@dataclass
class Result:
    layouts: list[Layout]
    unplaced: list[Unplaced]
    settings: Settings
    stocks: list[Stock]  # including any default sheets that were added
    iterations: int = 0
    notes: list[str] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)  # one cutting tree per layout, JSON, for restoring it
    to_find: list[Unplaced] = field(default_factory=list)  # hardwood parts with no board yet: the shopping list

    @property
    def purchased(self) -> list[Layout]:
        return [lay for lay in self.layouts if not lay.stock.on_hand]

    @property
    def waste_pct(self) -> float:
        total = sum(lay.area for lay in self.layouts)
        used = sum(lay.parts_area for lay in self.layouts)
        return 100.0 * (1 - used / total) if total else 0.0
