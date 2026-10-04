"""Decide which stock each part may be cut from."""

from __future__ import annotations

from plywood.core.models import SHEET_4X8, Part, Settings, Stock, StockKind


def _norm_tag(tag: str | None) -> str | None:
    tag = (tag or "").strip().lower()
    return tag or None


def stock_matches(part: Part, stock: Stock, settings: Settings) -> bool:
    """Same kind (sheet/board) and thickness within tolerance. Tags only conflict if both are set."""
    if part.kind != stock.kind:
        return False
    if abs(part.thickness - stock.thickness) > settings.thickness_tolerance:
        return False
    part_tag, stock_tag = _norm_tag(part.tag), _norm_tag(stock.tag)
    return part_tag is None or stock_tag is None or part_tag == stock_tag


def with_default_sheets(parts: list[Part], stocks: list[Stock], settings: Settings) -> list[Stock]:
    """Add an unlimited 4x8 sheet for every sheet part (thickness, tag) with no stock to buy.

    On-hand pieces don't count: they can run out, and the rest of the job still needs stock.
    """
    stocks = list(stocks)
    if not settings.default_sheets:
        return stocks
    for part in parts:
        if part.kind != StockKind.SHEET or any(not s.on_hand and stock_matches(part, s, settings) for s in stocks):
            continue
        length, width = SHEET_4X8
        stocks.append(
            Stock(
                name=f"4x8 {_norm_tag(part.tag) or 'sheet'} (default)",
                length=length,
                width=width,
                thickness=part.thickness,
                qty=None,
                tag=_norm_tag(part.tag),
                kind=StockKind.SHEET,
            )
        )
    return stocks


def allowed_rotations(part: Part, kind: StockKind) -> tuple[bool, ...]:
    """Orientations a part may take on a stock kind. False = part length along the grain."""
    if part.grain == "length":
        return (False,)
    if part.grain == "width":
        return (True,)
    if kind == StockKind.BOARD:
        return (False,)  # board grain runs with the part's length
    return (False, True)


def edge_trims(stock: Stock, settings: Settings) -> tuple[float, float, float, float]:
    """Amount trimmed from the (left, right, bottom, top) edges of a stock piece."""
    edges = stock.trim_edges or ""
    t = settings.edge_trim
    return tuple(t if e in edges else 0.0 for e in "lrbt")  # type: ignore[return-value]


def usable_size(stock: Stock, settings: Settings) -> tuple[float, float]:
    left, right, bottom, top = edge_trims(stock, settings)
    return stock.length - left - right, stock.width - bottom - top


def fits_stock(part: Part, stock: Stock, settings: Settings) -> bool:
    length, width = usable_size(stock, settings)
    for rotated in allowed_rotations(part, stock.kind):
        pw, ph = (part.width, part.length) if rotated else (part.length, part.width)
        if pw <= length + 1e-6 and ph <= width + 1e-6:
            return True
    return False
