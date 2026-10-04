"""Decide which stock each part may be cut from."""

from __future__ import annotations

import math

from plywood.core.models import INCH, SHEET_4X8, Part, Settings, Stock, StockKind

QUARTERS = (4, 5, 6, 8, 10, 12, 16)  # standard rough lumber thicknesses, in quarter inches


def _norm_tag(tag: str | None) -> str | None:
    tag = (tag or "").strip().lower()
    return tag or None


def thickness_ok(part: Part, stock: Stock, settings: Settings) -> bool:
    """Sheets: the same thickness. Boards: the same or up to `max_planing` thicker, and a rough
    board at least `rough_cleanup` thicker. A board to find: the standard thickness for the part."""
    tol = settings.thickness_tolerance
    extra = stock.thickness - part.thickness
    if stock.kind == StockKind.SHEET:
        return abs(extra) <= tol
    if stock.find:
        return abs(stock.thickness - _quarters(part, settings) * INCH / 4) <= tol
    low = settings.rough_cleanup - tol if stock.rough else -tol
    return low <= extra <= settings.max_planing + tol


def _quarters(part: Part, settings: Settings) -> int:
    """The thinnest standard rough thickness (in quarter inches) that cleans up to the part."""
    need = part.thickness + settings.rough_cleanup - settings.thickness_tolerance
    return next((q for q in QUARTERS if q * INCH / 4 >= need), math.ceil(need / (INCH / 4)))


def needs_planing(part: Part, stock: Stock, settings: Settings) -> bool:
    return stock.kind == StockKind.BOARD and (
        stock.rough or stock.thickness - part.thickness > settings.thickness_tolerance
    )


def stock_matches(part: Part, stock: Stock, settings: Settings) -> bool:
    """Same kind (sheet/board) and a usable thickness. Tags only conflict if both are set."""
    if part.kind != stock.kind or not thickness_ok(part, stock, settings):
        return False
    part_tag, stock_tag = _norm_tag(part.tag), _norm_tag(stock.tag)
    return part_tag is None or stock_tag is None or part_tag == stock_tag


def with_default_stock(parts: list[Part], stocks: list[Stock], settings: Settings) -> list[Stock]:
    """Add stock for parts with nothing to buy: unlimited 4x8 sheets, and boards to find.

    On-hand pieces don't count: they can run out, and the rest of the job still needs stock.
    """
    stocks = list(stocks)
    for part in parts:
        if part.kind == StockKind.BOARD:
            buyable = [s for s in stocks if not s.on_hand and stock_matches(part, s, settings)]
            if settings.default_boards and not any(fits_stock(part, s, settings) for s in buyable):
                stocks.append(_board_to_find(part, settings))
            continue
        if not settings.default_sheets or any(not s.on_hand and stock_matches(part, s, settings) for s in stocks):
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


def _board_to_find(part: Part, settings: Settings) -> Stock:
    """A rough board in the next standard thickness: the typical size, or bigger if the part needs it."""
    quarters = _quarters(part, settings)
    a = settings.allowance
    length, width = part.length + a, part.width + a
    if part.grain == "width":
        length, width = width, length
    width_needed = width + settings.edge_joint
    length_needed = max(length + 2 * (settings.snipe + settings.crosscut_kerf), settings.min_planer_length)
    w = settings.board_width if width_needed <= settings.board_width else math.ceil(width_needed / INCH) * INCH
    ln = (
        settings.board_length
        if length_needed <= settings.board_length
        else math.ceil(length_needed / (12 * INCH)) * 12 * INCH
    )
    tag = _norm_tag(part.tag)
    return Stock(
        name=f"{quarters}/4 {tag or 'board'}",
        length=ln,
        width=w,
        thickness=quarters * INCH / 4,
        qty=None,
        tag=tag,
        kind=StockKind.BOARD,
        rough=True,
        find=True,
    )


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
    if needs_planing(part, stock, settings):
        if length < settings.min_planer_length - 1e-6:
            return False
        length -= 2 * (settings.snipe + settings.crosscut_kerf)
        if stock.rough:
            width -= settings.edge_joint
    for rotated in allowed_rotations(part, stock.kind):
        pw, ph = (part.width, part.length) if rotated else (part.length, part.width)
        if pw <= length + 1e-6 and ph <= width + 1e-6:
            return True
    return False
