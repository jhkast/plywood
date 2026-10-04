"""Guillotine packing: every cut runs edge to edge across the piece being cut.

Each stock piece is a binary tree. A free leaf receives a part at its origin corner and is
split by up to two cuts (one rip, one crosscut, in an order chosen by the split rule).
Kerf is removed by every cut. The tree is later flattened into saw steps.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from plywood.core.models import CutStep, Layout, Part, Placement, Rect, Segment, Settings, Stock
from plywood.core.matching import allowed_rotations, usable_size

EPS = 1e-6

RECT_RULES = ("baf", "bssf", "blsf")
SPLIT_RULES = ("sas", "las", "maxas", "minas", "h", "v")
BIN_RULES = ("best", "first")


@dataclass(eq=False)
class Node:
    x: float
    y: float
    w: float
    h: float
    kind: str = "free"  # free | part | cut
    item: Instance | None = None
    rotated: bool = False
    direction: str = ""  # "H" rip (line parallel to x) or "V" crosscut
    pos: float = 0.0
    kerf: float = 0.0
    children: list[Node] = field(default_factory=list)  # [near] or [near, far]


@dataclass(frozen=True, eq=False)
class Instance:
    part: Part
    copy: int
    allowed: frozenset[int]  # indexes into the stock list


def fit_score(rule: str, fw: float, fh: float, pw: float, ph: float) -> tuple[float, float]:
    lw, lh = fw - pw, fh - ph
    if rule == "baf":
        return (fw * fh - pw * ph, min(lw, lh))
    if rule == "bssf":
        return (min(lw, lh), max(lw, lh))
    return (max(lw, lh), min(lw, lh))  # blsf


def rip_first(rule: str, fw: float, fh: float, pw: float, ph: float) -> bool:
    """True to make the full-width rip before the crosscut."""
    lw, lh = fw - pw, fh - ph
    match rule:
        case "h":
            return True
        case "v":
            return False
        case "sas":
            return lw <= lh
        case "las":
            return lw > lh
        case "maxas":
            return fw * lh >= lw * fh
        case _:  # minas
            return fw * lh < lw * fh


class Bin:
    def __init__(self, index: int, stock_id: int, stock: Stock, settings: Settings):
        self.index = index
        self.stock_id = stock_id
        self.stock = stock
        self.rip_kerf = settings.rip_kerf
        self.crosscut_kerf = settings.crosscut_kerf
        self.trim, length, width = usable_size(stock, settings)
        self.root = Node(self.trim, self.trim, length, width)
        self.free: list[Node] = [self.root] if length > EPS and width > EPS else []
        self.parts_area = 0.0

    def _cut(self, n: Node, direction: str, size: float) -> tuple[Node, Node | None]:
        """Cut `n` so the near piece is `size` along the cut axis. No cut if nothing is left over."""
        extent = n.h if direction == "H" else n.w
        if extent - size <= EPS:
            return n, None
        kerf = self.rip_kerf if direction == "H" else self.crosscut_kerf
        far_size = extent - size - kerf
        n.kind, n.direction, n.kerf = "cut", direction, kerf
        if direction == "H":
            n.pos = n.y + size
            near = Node(n.x, n.y, n.w, size)
            far = Node(n.x, n.y + size + kerf, n.w, far_size) if far_size > EPS else None
        else:
            n.pos = n.x + size
            near = Node(n.x, n.y, size, n.h)
            far = Node(n.x + size + kerf, n.y, far_size, n.h) if far_size > EPS else None
        n.children = [near] if far is None else [near, far]
        return near, far

    def place(self, node: Node, item: Instance, pw: float, ph: float, rotated: bool, split_rule: str) -> None:
        self.free.remove(node)
        if rip_first(split_rule, node.w, node.h, pw, ph):
            near, far1 = self._cut(node, "H", ph)
            leaf, far2 = self._cut(near, "V", pw)
        else:
            near, far1 = self._cut(node, "V", pw)
            leaf, far2 = self._cut(near, "H", ph)
        leaf.kind, leaf.item, leaf.rotated = "part", item, rotated
        self.free.extend(f for f in (far1, far2) if f is not None)
        self.parts_area += pw * ph


@dataclass
class Packing:
    bins: list[Bin]
    unplaced: list[Instance]
    stocks: list[Stock]

    def score(self) -> tuple:
        purchased = [b for b in self.bins if not b.stock.on_hand]
        used_area = sum(b.stock.length * b.stock.width for b in self.bins)
        waste = used_area - sum(b.parts_area for b in self.bins)
        largest_free = max((f.w * f.h for b in self.bins for f in b.free), default=0.0)
        return (
            len(self.unplaced),
            round(sum(b.stock.cost for b in purchased), 6),
            len(purchased),
            round(waste, 3),
            -round(largest_free, 3),
        )


def pack(
    order: list[Instance],
    stocks: list[Stock],
    stock_order: list[int],
    settings: Settings,
    rect_rule: str,
    split_rule: str,
    bin_rule: str,
) -> Packing:
    bins: list[Bin] = []
    remaining = {i: s.qty for i, s in enumerate(stocks) if s.qty is not None}
    unplaced: list[Instance] = []

    for item in order:
        best = None
        for b in bins:
            if b.stock_id not in item.allowed:
                continue
            for rotated in allowed_rotations(item.part, b.stock.kind):
                pw, ph = (item.part.width, item.part.length) if rotated else (item.part.length, item.part.width)
                for node in b.free:
                    if pw <= node.w + EPS and ph <= node.h + EPS:
                        key = fit_score(rect_rule, node.w, node.h, pw, ph)
                        if best is None or key < best[0]:
                            best = (key, b, node, pw, ph, rotated)
            if bin_rule == "first" and best is not None:
                break

        if best is None:
            for sid in stock_order:
                if sid not in item.allowed or remaining.get(sid, 1) == 0:
                    continue
                b = Bin(len(bins), sid, stocks[sid], settings)
                for rotated in allowed_rotations(item.part, b.stock.kind):
                    pw, ph = (item.part.width, item.part.length) if rotated else (item.part.length, item.part.width)
                    if b.free and pw <= b.root.w + EPS and ph <= b.root.h + EPS:
                        key = fit_score(rect_rule, b.root.w, b.root.h, pw, ph)
                        if best is None or key < best[0]:
                            best = (key, b, b.root, pw, ph, rotated)
                if best is not None:
                    bins.append(b)
                    if sid in remaining:
                        remaining[sid] -= 1
                    break

        if best is None:
            unplaced.append(item)
            continue
        _, b, node, pw, ph, rotated = best
        b.place(node, item, pw, ph, rotated, split_rule)

    return Packing(bins, unplaced, stocks)


# ---------------------------------------------------------------- tree → layout


def _segment(node: Node | None, along: str, settings: Settings) -> Segment:
    if node is None:
        return Segment(0.0, "scrap")
    size = node.h if along == "H" else node.w
    if node.kind == "part":
        p = node.item
        label = p.part.name if p.part.qty == 1 else f"{p.part.name} #{p.copy}"
        return Segment(size, "part", label)
    if node.kind == "cut":
        return Segment(size, "piece")
    usable = min(node.w, node.h) >= settings.min_offcut
    return Segment(size, "offcut" if usable else "scrap")


def cut_steps(root: Node, settings: Settings) -> list[CutStep]:
    """Flatten the tree into saw steps, breadth first: parallel cuts across one piece become one step."""
    steps: list[CutStep] = []
    queue = deque([root])
    while queue:
        n = queue.popleft()
        if n.kind != "cut":
            continue
        positions: list[float] = []
        pieces: list[Node | None] = []
        cur = n
        while True:
            positions.append(cur.pos)
            near = cur.children[0]
            far = cur.children[1] if len(cur.children) > 1 else None
            pieces.append(near)
            if far is not None and far.kind == "cut" and far.direction == n.direction:
                cur = far
                continue
            pieces.append(far)
            break
        steps.append(
            CutStep(
                number=len(steps) + 1,
                direction="rip" if n.direction == "H" else "crosscut",
                piece=Rect(n.x, n.y, n.w, n.h),
                positions=tuple(positions),
                kerf=n.kerf,
                segments=tuple(_segment(p, n.direction, settings) for p in pieces),
            )
        )
        queue.extend(p for p in pieces if p is not None)
    return steps


def _leaves(node: Node):
    if node.kind == "cut":
        for c in node.children:
            yield from _leaves(c)
    else:
        yield node


def to_layout(b: Bin, number: int, settings: Settings) -> Layout:
    placements, offcuts, scrap = [], [], []
    for leaf in _leaves(b.root):
        rect = Rect(leaf.x, leaf.y, leaf.w, leaf.h)
        if leaf.kind == "part":
            placements.append(Placement(leaf.item.part, leaf.item.copy, rect, leaf.rotated))
        elif min(leaf.w, leaf.h) >= settings.min_offcut:
            offcuts.append(rect)
        else:
            scrap.append(rect)
    return Layout(number, b.stock, b.trim, placements, cut_steps(b.root, settings), offcuts, scrap)
