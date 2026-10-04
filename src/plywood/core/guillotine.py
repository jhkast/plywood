"""Guillotine packing: every cut runs edge to edge across the piece being cut.

Each stock piece is a binary tree. A free leaf receives a part at its origin corner and is
split by up to two cuts (one rip, one crosscut, in an order chosen by the split rule).
Kerf is removed by every cut. The tree is later flattened into saw steps.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace

from plywood.core.models import CutStep, Layout, Part, Placement, Rect, Segment, Settings, Stock
from plywood.core.matching import allowed_rotations, edge_trims, usable_size

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
    tag: str | None = None  # normalized part tag
    source: Part | None = None  # the finished part, when `part` is its oversize rough-cut version

    @property
    def final(self) -> Part:
        return self.source or self.part


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
        self.trims = edge_trims(stock, settings)
        length, width = usable_size(stock, settings)
        self.root = Node(self.trims[0], self.trims[2], length, width)
        self.free: list[Node] = [self.root] if length > EPS and width > EPS else []
        self.parts_area = 0.0
        self.cuts = sum(1 for t in self.trims if t > 0)  # one cut per trimmed edge
        # An untagged piece takes on the tag of the first tagged part placed on it (one material per piece).
        self.tag = (stock.tag or "").strip().lower() or None

    def accepts(self, item: Instance) -> bool:
        return item.tag is None or self.tag is None or item.tag == self.tag

    def _cut(self, n: Node, direction: str, size: float) -> tuple[Node, Node | None]:
        """Cut `n` so the near piece is `size` along the cut axis. No cut if nothing is left over."""
        extent = n.h if direction == "H" else n.w
        if extent - size <= EPS:
            return n, None
        kerf = self.rip_kerf if direction == "H" else self.crosscut_kerf
        far_size = extent - size - kerf
        self.cuts += 1
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

    def place(
        self, node: Node, item: Instance, pw: float, ph: float, rotated: bool, split_rule: str
    ) -> tuple[Node | None, Node | None]:
        """Put a part at the node's origin. Returns the leftovers: (after the first cut, after the second)."""
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
        if item.tag is not None:
            self.tag = item.tag
        return far1, far2

    def steps(self) -> int:
        """Number of saw steps (parallel cuts across one piece count as one step)."""
        count, stack = 0, [(self.root, "")]
        while stack:
            n, parent_dir = stack.pop()
            if n.kind != "cut":
                continue
            far = n.children[1] if len(n.children) > 1 else None
            if n.direction != parent_dir:
                count += 1
            stack.append((n.children[0], ""))
            if far is not None:
                stack.append((far, n.direction))  # continuing the same rip/crosscut run
        return count


@dataclass
class Packing:
    bins: list[Bin]
    unplaced: list[Instance]
    stocks: list[Stock]

    def score(self, priority: str = "waste") -> tuple:
        """Lower is better. Sheets to buy always come first; `priority` decides what breaks ties."""
        purchased = [b for b in self.bins if not b.stock.on_hand]
        used_area = sum(b.stock.length * b.stock.width for b in self.bins)
        waste = used_area - sum(b.parts_area for b in self.bins)
        sheet = max((b.stock.length * b.stock.width for b in self.bins), default=1.0)
        largest_free = max((f.w * f.h for b in self.bins for f in b.free), default=0.0) / sheet
        cuts = sum(b.cuts for b in self.bins)
        steps = sum(b.steps() for b in self.bins)
        # Waste is fixed once the stock is chosen, so it only matters when on-hand pieces differ.
        head = (len(self.unplaced), round(sum(b.stock.cost for b in purchased), 6), len(purchased), round(waste, 3))
        if priority == "cuts":
            return (*head, steps + cuts, -round(largest_free, 4))
        if priority == "balanced":
            # A step or cut is worth about 3% of a sheet of reusable offcut.
            return (*head, round(0.03 * (steps + cuts) - largest_free, 6))
        return (*head, -round(largest_free, 4), steps + cuts)


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
            if b.stock_id not in item.allowed or not b.accepts(item):
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


def pack_strips(
    order: list[Instance],
    stocks: list[Stock],
    stock_order: list[int],
    settings: Settings,
    exact_only: bool,
) -> Packing:
    """Shop-style layout: rip the sheet into full-length strips, then crosscut each strip.

    Parts go into a strip of their own width when one has room; otherwise (unless `exact_only`)
    into a wider strip with an extra trim rip; otherwise a new strip is ripped off the sheet.
    """
    bins: list[Bin] = []
    remainder: dict[int, Node | None] = {}  # bin index -> unripped part of the sheet
    strips: list[tuple[Bin, list[Node | None], float]] = []  # (bin, [tail], strip width)
    remaining = {i: s.qty for i, s in enumerate(stocks) if s.qty is not None}
    unplaced: list[Instance] = []

    def orientations(item: Instance, b: Bin) -> list[tuple[float, float, bool]]:
        opts = []
        for rotated in allowed_rotations(item.part, b.stock.kind):
            pw, ph = (item.part.width, item.part.length) if rotated else (item.part.length, item.part.width)
            opts.append((pw, ph, rotated))
        return sorted(opts, key=lambda o: o[1])  # narrowest strip first

    def try_strips(item: Instance, exact: bool) -> bool:
        for b, tail, width in strips:
            node = tail[0]
            if node is None or b.stock_id not in item.allowed or not b.accepts(item):
                continue
            for pw, ph, rotated in orientations(item, b):
                if (abs(ph - width) <= EPS) != exact:
                    continue
                if pw <= node.w + EPS and ph <= node.h + EPS:
                    far1, _ = b.place(node, item, pw, ph, rotated, "v")
                    tail[0] = far1
                    return True
        return False

    def new_strip(item: Instance, b: Bin) -> bool:
        node = remainder.get(b.index)
        if node is None or b.stock_id not in item.allowed or not b.accepts(item):
            return False
        for pw, ph, rotated in orientations(item, b):
            if pw <= node.w + EPS and ph <= node.h + EPS:
                far1, far2 = b.place(node, item, pw, ph, rotated, "h")
                remainder[b.index] = far1
                strips.append((b, [far2], ph))
                return True
        return False

    for item in order:
        if try_strips(item, exact=True) or (not exact_only and try_strips(item, exact=False)):
            continue
        if any(new_strip(item, b) for b in bins):
            continue
        placed = False
        for sid in stock_order:
            if sid not in item.allowed or remaining.get(sid, 1) == 0:
                continue
            b = Bin(len(bins), sid, stocks[sid], settings)
            remainder[b.index] = b.root if b.free else None
            if new_strip(item, b):
                bins.append(b)
                if sid in remaining:
                    remaining[sid] -= 1
                placed = True
                break
        if not placed:
            unplaced.append(item)

    return Packing(bins, unplaced, stocks)


# ---------------------------------------------------------------- tree → layout


def _segment(node: Node | None, along: str, settings: Settings) -> Segment:
    if node is None:
        return Segment(0.0, "scrap")
    size = node.h if along == "H" else node.w
    if node.kind == "part":
        p = node.item
        label = p.part.name if p.part.qty == 1 else f"{p.part.name} #{p.copy}"
        final = None
        if p.source is not None:
            src = p.source
            final = (src.width, src.length) if node.rotated else (src.length, src.width)
        return Segment(size, "part", label, final)
    if node.kind == "cut":
        return Segment(size, "piece")
    usable = min(node.w, node.h) >= settings.min_offcut
    return Segment(size, "offcut" if usable else "scrap")


def _letters(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def cut_steps(root: Node, settings: Settings) -> list[CutStep]:
    """Flatten the tree into saw steps, breadth first: parallel cuts across one piece become one step.

    Intermediate pieces (ones that need more cutting) get letters A, B, C… so steps can refer to them.
    """
    steps: list[CutStep] = []
    names: dict[int, str] = {}
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
        segments = []
        for piece in pieces:
            seg = _segment(piece, n.direction, settings)
            if seg.kind == "piece":
                names[id(piece)] = _letters(len(names))
                seg = Segment(seg.size, "piece", names[id(piece)])
            segments.append(seg)
        steps.append(
            CutStep(
                number=len(steps) + 1,
                direction="rip" if n.direction == "H" else "crosscut",
                piece=Rect(n.x, n.y, n.w, n.h),
                positions=tuple(positions),
                kerf=n.kerf,
                segments=tuple(segments),
                piece_label=names.get(id(n), ""),
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
            placements.append(Placement(leaf.item.final, leaf.item.copy, rect, leaf.rotated))
        elif min(leaf.w, leaf.h) >= settings.min_offcut:
            offcuts.append(rect)
        else:
            scrap.append(rect)
    steps = cut_steps(b.root, settings)
    if any(t > 0 for t in b.trims):
        whole = Rect(0.0, 0.0, b.stock.length, b.stock.width)
        trim = CutStep(1, "trim", whole, (), settings.rip_kerf, (Segment(settings.edge_trim, "scrap"),))
        steps = [trim] + [replace(s, number=s.number + 1) for s in steps]
    return Layout(number, b.stock, b.trims, placements, steps, offcuts, scrap, b.tag, b.cuts)
