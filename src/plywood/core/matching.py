"""Decide which stock each part may be cut from."""

from __future__ import annotations

import math

from plywood.core.models import INCH, SHEET_4X8, Part, Settings, Stock, StockKind

QUARTERS = (4, 5, 6, 8, 10, 12, 16)  # standard rough lumber thicknesses, in quarter inches

# Dimensional lumber: nominal name -> actual thickness and width, in inches.
_ACTUAL = {1: 0.75, 2: 1.5, 3: 2.5, 4: 3.5, 6: 5.5, 8: 7.25, 10: 9.25, 12: 11.25}
DIMENSIONAL_SIZES = {
    f"{t}x{w}": (_ACTUAL[t] * INCH, _ACTUAL[w] * INCH)
    for t, widths in ((1, (2, 3, 4, 6, 8, 10, 12)), (2, (2, 3, 4, 6, 8, 10, 12)), (4, (4, 6)), (6, (6,)))
    for w in widths
}


def nominal(thickness: float, width: float, tol: float = 0.5) -> str | None:
    """'2x4' for a 1-1/2" x 3-1/2" stick, None for anything else."""
    return next(
        (n for n, (t, w) in DIMENSIONAL_SIZES.items() if abs(t - thickness) <= tol and abs(w - width) <= tol), None
    )


def _norm_tag(tag: str | None) -> str | None:
    tag = (tag or "").strip().lower()
    return tag or None


def thickness_ok(part: Part, stock: Stock, settings: Settings) -> bool:
    """Sheets: the same thickness. Boards: the same or up to `max_planing` thicker. A rough board:
    at least `rough_cleanup` thicker, and up to `max_planing` thicker or the standard rough
    thickness for the part, whichever is more (8/4 for 1-1/2" legs). A board to find: that
    standard thickness."""
    tol = settings.thickness_tolerance
    extra = stock.thickness - part.thickness
    if stock.kind == StockKind.SHEET:
        return abs(extra) <= tol
    standard = _quarters(part, settings) * INCH / 4
    if stock.rough:
        return settings.rough_cleanup - tol <= extra <= max(settings.max_planing, standard - part.thickness) + tol
    return -tol <= extra <= settings.max_planing + tol


def _quarters(part: Part, settings: Settings) -> int:
    """The thinnest standard rough thickness (in quarter inches) that cleans up to the part."""
    need = part.thickness + settings.rough_cleanup - settings.thickness_tolerance
    return next((q for q in QUARTERS if q * INCH / 4 >= need), math.ceil(need / (INCH / 4)))


def needs_planing(part: Part, stock: Stock, settings: Settings) -> bool:
    return stock.kind.is_board and (
        stock.rough or stock.thickness - part.thickness > settings.thickness_tolerance
    )


def stock_matches(part: Part, stock: Stock, settings: Settings) -> bool:
    """Same kind and a usable thickness. Tags only conflict if both are set."""
    if part.kind != stock.kind or not thickness_ok(part, stock, settings):
        return False
    part_tag, stock_tag = _norm_tag(part.tag), _norm_tag(stock.tag)
    return part_tag is None or stock_tag is None or part_tag == stock_tag


def with_default_stock(parts: list[Part], stocks: list[Stock], settings: Settings) -> list[Stock]:
    """Add stock for parts with nothing to buy: unlimited 4x8 sheets and standard dimensional
    sizes. Hardwood gets none: parts without a board go on the shopping list instead.

    On-hand pieces don't count: they can run out, and the rest of the job still needs stock.
    """
    stocks = list(stocks)
    for part in parts:
        if part.kind == StockKind.HARDWOOD:
            continue
        if part.kind == StockKind.DIMENSIONAL:
            if settings.default_dimensional and not any(
                not s.on_hand and stock_matches(part, s, settings) and fits_stock(part, s, settings) for s in stocks
            ):
                stocks += [s for s in _standard_sticks(part, settings) if s not in stocks]
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


def _standard_sticks(part: Part, settings: Settings) -> list[Stock]:
    """Every standard size and length a dimensional part could come from (exact thickness, or
    planed down within max planing), so the optimizer can pick the least waste."""
    tag = _norm_tag(part.tag)
    out = []
    for name, (thick, width) in DIMENSIONAL_SIZES.items():
        for length in settings.dimensional_lengths:
            stick = Stock(name, length, width, thick, qty=None, tag=tag, kind=StockKind.DIMENSIONAL)
            if thickness_ok(part, stick, settings) and fits_stock(part, stick, settings):
                out.append(stick)
    return out


def allowed_rotations(part: Part, kind: StockKind) -> tuple[bool, ...]:
    """Orientations a part may take on a stock kind. False = part length along the grain."""
    if part.grain == "length":
        return (False,)
    if part.grain == "width":
        return (True,)
    if kind.is_board:
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
