"""Geometric sanity checks for a result. Returns a list of problems (empty means valid)."""

from __future__ import annotations

from plywood.core.models import Grain, Result, StockKind

EPS = 1e-6


def check(result: Result) -> list[str]:
    s = result.settings
    problems: list[str] = []
    for lay in result.layouts:
        lo_x, lo_y = lay.trim, lay.trim
        hi_x, hi_y = lay.stock.length - lay.trim, lay.stock.width - lay.trim
        ps = lay.placements
        for p in ps:
            r = p.rect
            where = f"sheet {lay.number} {p.label}"
            if r.x < lo_x - EPS or r.y < lo_y - EPS or r.x + r.w > hi_x + EPS or r.y + r.h > hi_y + EPS:
                problems.append(f"{where}: outside usable area")
            want = (p.part.width, p.part.length) if p.rotated else (p.part.length, p.part.width)
            if abs(r.w - want[0]) > EPS or abs(r.h - want[1]) > EPS:
                problems.append(f"{where}: size mismatch")
            if p.part.grain == Grain.LENGTH and p.rotated:
                problems.append(f"{where}: grain-locked to length but rotated")
            if p.part.grain == Grain.WIDTH and not p.rotated:
                problems.append(f"{where}: grain-locked to width but not rotated")
            if lay.stock.kind == StockKind.BOARD and p.part.grain == Grain.NONE and p.rotated:
                problems.append(f"{where}: rotated across a board")
            if p.part.kind != lay.stock.kind:
                problems.append(f"{where}: {p.part.kind} part on {lay.stock.kind} stock")
            if abs(p.part.thickness - lay.stock.thickness) > s.thickness_tolerance:
                problems.append(f"{where}: thickness mismatch")
        for i, a in enumerate(ps):
            for b in ps[i + 1 :]:
                ra, rb = a.rect, b.rect
                gap_x = max(rb.x - (ra.x + ra.w), ra.x - (rb.x + rb.w))
                gap_y = max(rb.y - (ra.y + ra.h), ra.y - (rb.y + rb.h))
                if gap_x < s.crosscut_kerf - EPS and gap_y < s.rip_kerf - EPS:
                    problems.append(f"sheet {lay.number}: {a.label} and {b.label} overlap or lack kerf")
    used: dict[int, int] = {}
    for lay in result.layouts:
        if lay.stock.qty is not None:
            used[id(lay.stock)] = used.get(id(lay.stock), 0) + 1
            if used[id(lay.stock)] > lay.stock.qty:
                problems.append(f"used more of on-hand stock {lay.stock.name!r} than available")
    return problems
