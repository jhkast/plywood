"""Geometric sanity checks for a result. Returns a list of problems (empty means valid)."""

from __future__ import annotations

from plywood.core.matching import needs_planing, thickness_ok
from plywood.core.models import Grain, Rect, Result, StockKind

EPS = 1e-6


def _inside(r: Rect, outer: Rect) -> bool:
    return (
        r.x >= outer.x - EPS and r.y >= outer.y - EPS
        and r.x + r.w <= outer.x + outer.w + EPS and r.y + r.h <= outer.y + outer.h + EPS
    )


def check(result: Result) -> list[str]:
    s = result.settings
    problems: list[str] = []
    for lay in result.layouts:
        left, right, bottom, top = lay.trims
        lo_x, lo_y = left, bottom
        hi_x, hi_y = lay.stock.length - right, lay.stock.width - top
        ps = lay.placements
        planes = [st for st in lay.steps if st.direction == "plane"]
        for st in planes:
            if st.piece.w < s.min_planer_length - EPS:
                problems.append(f"sheet {lay.number}: segment {st.piece_label} is shorter than the planer minimum")
        for p in ps:
            r = p.rect
            where = f"sheet {lay.number} {p.label}"
            if r.x < lo_x - EPS or r.y < lo_y - EPS or r.x + r.w > hi_x + EPS or r.y + r.h > hi_y + EPS:
                problems.append(f"{where}: outside usable area")
            al, aw = s.allowance(p.part.kind)
            want = (p.part.width + aw, p.part.length + al) if p.rotated else (p.part.length + al, p.part.width + aw)
            if abs(r.w - want[0]) > EPS or abs(r.h - want[1]) > EPS:
                problems.append(f"{where}: size mismatch")
            if p.part.grain == Grain.LENGTH and p.rotated:
                problems.append(f"{where}: grain-locked to length but rotated")
            if p.part.grain == Grain.WIDTH and not p.rotated:
                problems.append(f"{where}: grain-locked to width but not rotated")
            if lay.stock.kind.is_board and p.part.grain == Grain.NONE and p.rotated:
                problems.append(f"{where}: rotated across a board")
            if p.part.kind != lay.stock.kind:
                problems.append(f"{where}: {p.part.kind} part on {lay.stock.kind} stock")
            if not thickness_ok(p.part, lay.stock, s):
                problems.append(f"{where}: thickness mismatch")
            if needs_planing(p.part, lay.stock, s):
                seg = next((st.piece for st in planes if _inside(r, st.piece) and abs(st.thickness - p.part.thickness) < 0.01), None)
                if seg is None:
                    problems.append(f"{where}: needs planing but isn't in a segment planed to its thickness")
                elif r.x < seg.x + s.snipe - EPS or r.x + r.w > seg.x + seg.w - s.snipe + EPS:
                    problems.append(f"{where}: inside the snipe")
        tags = {(p.part.tag or "").strip().lower() for p in ps} - {""}
        if len(tags) > 1:
            problems.append(f"sheet {lay.number}: mixes materials {sorted(tags)}")
        for i, a in enumerate(ps):
            for b in ps[i + 1 :]:
                ra, rb = a.rect, b.rect
                gap_x = max(rb.x - (ra.x + ra.w), ra.x - (rb.x + rb.w))
                gap_y = max(rb.y - (ra.y + ra.h), ra.y - (rb.y + rb.h))
                rip, cross = (s.sheet_kerf, s.sheet_kerf) if lay.stock.kind == StockKind.SHEET else (s.rip_kerf, s.crosscut_kerf)
                if gap_x < min(cross, s.rough_crosscut_kerf if lay.stock.rough else cross) - EPS and gap_y < rip - EPS:
                    problems.append(f"sheet {lay.number}: {a.label} and {b.label} overlap or lack kerf")
    used: dict[int, int] = {}
    for lay in result.layouts:
        if lay.stock.qty is not None:
            used[id(lay.stock)] = used.get(id(lay.stock), 0) + 1
            if used[id(lay.stock)] > lay.stock.qty:
                problems.append(f"used more of on-hand stock {lay.stock.name!r} than available")
    return problems
