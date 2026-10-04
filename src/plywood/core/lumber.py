"""Board packing: crosscut each board into segments, plane each to one thickness, then rip and crosscut.

A segment holds parts of one target thickness. A planed segment has `snipe` at each end, cut off
right after planing, and is at least `min_planer_length` long; on a rough board it is also
jointed, losing `edge_joint` off its bottom edge. Parts at a surfaced board's own thickness
need no planing, so their segment has no snipe.
"""

from __future__ import annotations

from typing import Callable

from plywood.core.guillotine import EPS, Bin, Instance, Node, Packing, _leaves, fit_score, mark_segment
from plywood.core.matching import allowed_rotations, needs_planing
from plywood.core.models import Settings, Stock

# How long a segment is: the longest part, the two longest end to end, or as many parts as fit
# in the rest of the board (then shortened to what they use).
SPANS = ("one", "two", "all")


def _along(item: Instance, b: Bin) -> tuple[float, float, bool]:
    """(length along the board, width across it, rotated) for a part on a board."""
    rotated = allowed_rotations(item.part, b.stock.kind)[0]
    p = item.part
    return (p.width, p.length, True) if rotated else (p.length, p.width, False)


def _same_thickness(a: Instance, b: Instance) -> bool:
    return abs(a.part.thickness - b.part.thickness) <= 0.01


def _segment_size(item: Instance, partner: Instance | None, b: Bin, settings: Settings) -> tuple[float, float]:
    """(length available to parts, total length cut off the board) for a segment starting with `item`."""
    inner = _along(item, b)[0]
    if partner is not None:
        inner += settings.crosscut_kerf + _along(partner, b)[0]
    if not needs_planing(item.part, b.stock, settings):
        return inner, inner
    margin = 2 * (settings.snipe + settings.crosscut_kerf) if settings.snipe > 0 else 0.0
    inner = max(inner, settings.min_planer_length - margin)
    return inner, inner + margin


def _fill(b: Bin, region: Node, items: list[Instance], rect_rule: str, split_rule: str) -> list[Instance]:
    """Guillotine-pack `items` (in order) into one segment's region; returns those placed."""
    local = [region]
    b.free.append(region)
    placed = []
    for it in items:
        if not b.accepts(it):
            continue
        best = None
        for rotated in allowed_rotations(it.part, b.stock.kind):
            pw, ph = (it.part.width, it.part.length) if rotated else (it.part.length, it.part.width)
            for node in local:
                if pw <= node.w + EPS and ph <= node.h + EPS:
                    key = fit_score(rect_rule, node.w, node.h, pw, ph)
                    if best is None or key < best[0]:
                        best = (key, node, pw, ph, rotated)
        if best is None:
            continue
        _, node, pw, ph, rotated = best
        far1, far2 = b.place(node, it, pw, ph, rotated, split_rule)
        local.remove(node)
        local.extend(f for f in (far1, far2) if f is not None)
        placed.append(it)
    return placed


def _add_segment(
    b: Bin, rest: dict[int, Node | None], item: Instance, pending: list[Instance], settings: Settings,
    rect_rule: str, split_rule: str, span: str,
) -> list[Instance]:
    """Cut the next segment off board `b` for `item` and fill it with matching parts."""
    stock = b.stock
    planed = needs_planing(item.part, stock, settings)
    group = [
        it for it in pending
        if b.stock_id in it.allowed
        and _same_thickness(it, item)
        and needs_planing(it.part, stock, settings) == planed
    ]
    room = rest[b.index]
    assert room is not None
    partner = group[1] if span == "two" and len(group) > 1 else None
    inner, total = _segment_size(item, partner, b, settings)
    if total > room.w + EPS:
        inner, total = _segment_size(item, None, b, settings)
    inset = settings.edge_joint if planed and stock.rough else 0.0
    if span == "all":
        # Pack into everything left on the board, then keep only the length those parts use.
        margin = total - inner
        trial = Bin(b.index, b.stock_id, stock, settings)
        trial.tag, trial.free = b.tag, []
        region = Node(0.0, 0.0, room.w - margin, room.h - inset)
        group = _fill(trial, region, group, rect_rule, split_rule)
        used = max(leaf.x + leaf.pw for leaf in _leaves(region) if leaf.kind == "part")
        floor = settings.min_planer_length - margin if planed else 0.0
        inner = max(used, floor, _along(item, b)[0])
        total = inner + margin

    b.free.remove(room)
    seg, far = b._cut(room, "V", total, b.segment_kerf)
    rest[b.index] = far
    if far is not None:
        b.free.append(far)
    region = seg
    if planed:
        mark_segment(b, seg, item.part.thickness, stock.rough, inset)
        if settings.snipe > 0:
            left, rest_of = b._cut(seg, "V", settings.snipe)
            b.free.append(left)
            assert rest_of is not None
            region, right = b._cut(rest_of, "V", inner)
            if right is not None:
                b.free.append(right)
    return _fill(b, region, group, rect_rule, split_rule)


def pack_boards(
    order: list[Instance],
    stocks: list[Stock],
    stock_order: list[int],
    settings: Settings,
    rect_rule: str,
    split_rule: str,
    bin_rule: str,
    span: str | Callable[[], str],
) -> Packing:
    """`span`: one of SPANS for every segment, or a function picking one per segment."""
    bins: list[Bin] = []
    rest: dict[int, Node | None] = {}  # bin index -> the part of the board not yet crosscut
    remaining = {i: s.qty for i, s in enumerate(stocks) if s.qty is not None}
    unplaced: list[Instance] = []
    pending = list(order)

    def room_for(b: Bin, item: Instance) -> float | None:
        node = rest.get(b.index)
        if node is None or b.stock_id not in item.allowed or not b.accepts(item):
            return None
        _, total = _segment_size(item, None, b, settings)
        return node.w - total if total <= node.w + EPS else None

    while pending:
        item = pending[0]
        target = None
        best_left = None
        for b in bins:
            left = room_for(b, item)
            if left is None:
                continue
            if bin_rule == "first":
                target = b
                break
            if best_left is None or left < best_left:
                target, best_left = b, left
        if target is None:
            # A new board: on hand before bought, then one that needs no planing for this part.
            rank = {sid: n for n, sid in enumerate(stock_order)}
            for sid in sorted(
                stock_order,
                key=lambda i: (not stocks[i].on_hand, needs_planing(item.part, stocks[i], settings), rank[i]),
            ):
                if sid not in item.allowed or remaining.get(sid, 1) == 0:
                    continue
                b = Bin(len(bins), sid, stocks[sid], settings)
                if not b.free:
                    continue
                rest[b.index] = b.root
                if room_for(b, item) is None:
                    continue
                bins.append(b)
                if sid in remaining:
                    remaining[sid] -= 1
                target = b
                break
        if target is None:
            unplaced.append(pending.pop(0))
            continue
        this_span = span if isinstance(span, str) else span()
        placed = {id(it) for it in _add_segment(target, rest, item, pending, settings, rect_rule, split_rule, this_span)}
        if id(item) not in placed:  # can't happen: the segment is sized for it
            unplaced.append(item)
            placed.add(id(item))
        pending = [it for it in pending if id(it) not in placed]

    return Packing(bins, unplaced, stocks)
