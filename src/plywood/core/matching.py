"""Decide which stock each part may be cut from."""

from __future__ import annotations

from plywood.core.models import SHEET_4X8, Part, Settings, Stock, StockKind


def _norm_tag(tag: str | None) -> str | None:
    tag = (tag or "").strip().lower()
    return tag or None


def stock_matches(part: Part, stock: Stock, settings: Settings) -> bool:
    """Same kind (sheet/board), thickness within tolerance, and a tagged part needs the same tag."""
    if part.kind != stock.kind:
        return False
    if abs(part.thickness - stock.thickness) > settings.thickness_tolerance:
        return False
    tag = _norm_tag(part.tag)
    return tag is None or tag == _norm_tag(stock.tag)


def with_default_sheets(parts: list[Part], stocks: list[Stock], settings: Settings) -> list[Stock]:
    """Add an unlimited 4x8 sheet for every sheet part (thickness, tag) that has no matching stock."""
    stocks = list(stocks)
    if not settings.default_sheets:
        return stocks
    for part in parts:
        if part.kind != StockKind.SHEET or any(stock_matches(part, s, settings) for s in stocks):
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


def usable_size(stock: Stock, settings: Settings) -> tuple[float, float, float]:
    """(trim, usable length, usable width) for a stock piece."""
    trim = settings.edge_trim if stock.kind == StockKind.SHEET else 0.0
    return trim, stock.length - 2 * trim, stock.width - 2 * trim


def fits_stock(part: Part, stock: Stock, settings: Settings) -> bool:
    _, length, width = usable_size(stock, settings)
    for rotated in allowed_rotations(part, stock.kind):
        pw, ph = (part.width, part.length) if rotated else (part.length, part.width)
        if pw <= length + 1e-6 and ph <= width + 1e-6:
            return True
    return False
