"""Self-contained, printable HTML report: summary, then one page per sheet or board."""

from __future__ import annotations

from collections import Counter
from html import escape

from plywood.core.findlist import find_groups, with_waste
from plywood.core.matching import nominal
from plywood.core.models import CutStep, Layout, Result, Segment, StockKind
from plywood.core.units import Formatter
from plywood.render.svg import layout_svg, piece_colors, relative_width

CSS = """
body { font-family: system-ui, sans-serif; color: #222; background: #fff; margin: 24px; }
h1 { margin: 0 0 4px; } h2 { margin: 0 0 8px; font-size: 1.15rem; }
.muted { color: #666; }
table { border-collapse: collapse; margin: 8px 0 16px; }
td, th { border-bottom: 1px solid #ddd; padding: 4px 10px; text-align: left; font-variant-numeric: tabular-nums; }
.warn { background: #fff3cd; border: 1px solid #e0c060; padding: 8px 12px; margin: 12px 0; }
.sheet { page-break-before: always; margin-top: 32px; }
.sheet svg { height: auto; }
ol.steps { list-style: none; padding: 0; margin: 8px 0; }
ol.steps li { display: grid; grid-template-columns: 22px max-content 14px 1fr; gap: 6px; margin: 0 0 6px; align-items: start; }
.arrow { color: #888; }
.letter { color: #fff; border-radius: 3px; padding: 0 4px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
svg { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.final { color: #6b5a3e; font-weight: 400; }
ol.steps .num { flex: none; width: 22px; height: 22px; border-radius: 50%; background: #c0392b; color: #fff;
  font-size: 12px; font-weight: 700; display: grid; place-items: center; margin-top: 1px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip { border-radius: 4px; padding: 1px 7px; font-size: 13px; border: 1px solid transparent; }
.chip.part { background: #ead7b0; color: #2b2116; }
.chip.piece, .chip.source { border-color: #7a5a2b; color: inherit; }
.chip.leftover { color: #666; border-color: #bbb; border-style: dashed; }
.chip.plane { background: #dfe8f3; color: #1d3550; }
.find h4 { margin: 12px 0 2px; } .find p { margin: 0 0 4px; }
@media print { body { margin: 0; } .sheet { margin-top: 0; } }
"""


def _piece_dims(seg: Segment, step: CutStep, fmt: Formatter) -> str:
    """Full size of a piece produced by a step (the cut only sets one of its two dimensions)."""
    w, h = (step.piece.w, seg.size) if step.direction == "rip" else (seg.size, step.piece.h)
    return fmt.dims(w, h)


def _letter(label: str, colors: dict[str, str]) -> str:
    """A piece letter, drawn like its badge on the diagram when the piece has a color."""
    color = colors.get(label)
    if not color:
        return f"<b>{escape(label)}</b>"
    return f'<b class="letter" style="background:{color}">{escape(label)}</b>'


def _chip(seg: Segment, step: CutStep, fmt: Formatter, colors: dict[str, str]) -> str:
    dims = _piece_dims(seg, step, fmt)
    kind = "leftover" if seg.kind in ("offcut", "scrap") else seg.kind
    name = seg.label if kind in ("part", "piece") else "leftover"
    attr = f' data-piece="{escape(seg.label)}"' if seg.kind == "piece" else ""
    final = f' <span class="final">→ {fmt.dims(*seg.final)}</span>' if seg.final else ""
    label = _letter(name, colors) if kind == "piece" else f"<b>{escape(name)}</b>"
    return f'<span class="chip {kind}"{attr}>{label} {dims}{final}</span>'


def steps_html(layout: Layout, fmt: Formatter, colored: bool = False) -> str:
    """`colored`: give each lettered piece its own color, matching the diagram (for print)."""
    colors = piece_colors(layout) if colored else {}
    if not layout.steps:
        return ""
    whole = "Board" if layout.stock.kind.is_board else "Sheet"
    items = []
    for step in layout.steps:
        source = step.piece_label or whole
        if step.direction == "plane":
            verb = "joint &amp; plane" if step.jointed else "plane"
            chips = f'<span class="chip plane"><b>{verb}</b> to {fmt.thickness(step.thickness)}</span>'
        elif step.direction == "trim":
            left, right, bottom, top = layout.trims
            dims = fmt.dims(step.piece.w - left - right, step.piece.h - bottom - top)
            chips = (
                f'<span class="chip leftover"><b>trim</b> {fmt.length(step.segments[0].size)}</span> '
                f'<span class="chip source"><b>{whole}</b> {dims}</span>'
            )
        else:
            chips = " ".join(_chip(seg, step, fmt, colors) for seg in step.segments if seg.size > 0)
        items.append(
            f"<li data-step='{step.number}' data-piece='{escape(step.piece_label)}'>"
            f"<span class='num'>{step.number}</span>"
            f"<span class='chip source'>{_letter(source, colors)} {fmt.dims(step.piece.w, step.piece.h)}</span>"
            f"<span class='arrow'>→</span><span class='chips'>{chips}</span></li>"
        )
    return f"<ol class='steps'>{''.join(items)}</ol>"


def stock_label(stock, fmt: Formatter) -> str:
    """E.g. 3/4" birch ply · 96" × 48", or 2x4 · 96" for dimensional lumber."""
    if stock.kind == StockKind.DIMENSIONAL and (n := nominal(stock.thickness, stock.width)):
        feet = stock.length / (12 * 25.4)
        length = f"{round(feet)}'" if fmt.unit == "in" and abs(feet - round(feet)) < 1e-6 else fmt.length(stock.length)
        return f"{n}{' ' + stock.tag if stock.tag else ''} · {length}"
    return f"{fmt.thickness(stock.thickness)} {stock.tag or stock.kind} · {fmt.dims(stock.length, stock.width)}"


def _source(stock) -> str:
    return "on hand" if stock.on_hand else "buy"


def spares_for(stock, settings) -> int:
    if stock.on_hand:
        return 0
    return settings.spare_sheets if stock.kind == StockKind.SHEET else settings.spare_sticks


def buy_lines(result: Result, fmt: Formatter) -> list[dict]:
    """Stock to buy, one line per kind of stock, with the spares from Settings."""
    counts = Counter(stock_label(lay.stock, fmt) for lay in result.purchased)
    spares = {stock_label(lay.stock, fmt): spares_for(lay.stock, result.settings) for lay in result.purchased}
    return [{"label": label, "count": n, "spares": spares[label]} for label, n in counts.items()]


def find_lines(result: Result, fmt: Formatter, spares: bool = True) -> list[dict]:
    """The hardwood shopping list: per thickness and species, totals and the blanks to find."""
    s = result.settings
    out = []
    for g in find_groups(result.to_find, s, spares):
        out.append({
            "key": g.key,
            "label": g.label,
            "tag": g.tag,
            "quarters": g.quarters,
            "thickness": fmt.exact(g.thickness),
            "board_feet": round(g.board_feet, 1),
            "board_feet_total": round(with_waste(g.board_feet, s), 1),
            "waste_pct": s.waste_pct,
            "longest": fmt.length(g.longest),
            "widest": fmt.length(g.widest),
            "spare_of": g.spare_of,
            "blanks": [
                {
                    "count": p.count,
                    "size": fmt.dims(p.length(s), p.full_width(s)),
                    "parts": _for_text(p),
                    "spares": dict(p.spares),  # the phone needs these found too
                }
                for p in g.pieces
            ],
        })
    return out


def _for_text(piece) -> str:
    """'2 × Short Ledger, 1 × Long Ledger (spare)': every part in one piece."""
    parts = [f"{n} × {name}" for name, n in piece.parts.most_common()]
    parts += [f"{n} × {name} (spare)" for name, n in piece.spares.most_common()]
    return ", ".join(parts)


def count_text(count: int, spares: int) -> str:
    if not spares:
        return str(count)
    return f"{count} + {spares} spare" if count else f"{spares} spare"


def find_html(lines: list[dict]) -> str:
    out = []
    for g in lines:
        feet = f"{g['board_feet']:.1f}"
        if g["waste_pct"]:
            feet = f"about {g['board_feet_total']:.0f} ({feet} + {g['waste_pct']:g}%)"
        rows = "".join(
            f"<tr><td>{b['count']}</td><td>at least {b['size']}</td><td>{escape(b['parts'])}</td></tr>"
            for b in g["blanks"]
        )
        out.append(
            f"<h4>{escape(g['label'])}</h4><p>{feet} bd ft · longest {g['longest']} · widest {g['widest']}</p>"
            f"<table><tr><th>Pieces</th><th>Size</th><th>For</th></tr>{rows}</table>"
        )
    return f"<div class='find'><h3>Find (hardwood)</h3>{''.join(out)}</div>" if out else ""


def _to_get(result: Result) -> str:
    text = f"{len(result.purchased)} to buy"
    find = sum(u.count for u in result.to_find)
    return text + f", {find} hardwood parts to find" if find else text


def shopping_html(result: Result, fmt: Formatter) -> str:
    """Stock to buy (with spares), hardwood to find, and the on-hand stock the cut list uses."""
    buy = "".join(
        f"<tr><td>{escape(b['label'])}</td><td>{count_text(b['count'], b['spares'])}</td></tr>"
        for b in buy_lines(result, fmt)
    )
    on_hand = Counter(stock_label(lay.stock, fmt) for lay in result.layouts if lay.stock.on_hand)
    used = "".join(f"<tr><td>{escape(label)}</td><td>{n}</td></tr>" for label, n in on_hand.items())
    return (
        (f"<h3>Buy</h3><table><tr><th>Stock</th><th>Count</th></tr>{buy}</table>" if buy else "")
        + find_html(find_lines(result, fmt))
        + (f"<h3>From stock on hand</h3><table><tr><th>Stock</th><th>Count</th></tr>{used}</table>" if used else "")
    )


def shopping_count(result: Result, fmt: Formatter) -> int:
    """Pieces to buy or find, spares included."""
    buy = sum(b["count"] + b["spares"] for b in buy_lines(result, fmt))
    return buy + sum(b["count"] for g in find_lines(result, fmt) for b in g["blanks"])


def summary_html(result: Result, fmt: Formatter, stats: bool = True, shopping: bool = True) -> str:
    table = shopping_html(result, fmt) if shopping else ""
    parts_total = sum(len(lay.placements) for lay in result.layouts)
    line = (
        f"<p>{parts_total} parts on {len(result.layouts)} pieces of stock "
        f"({_to_get(result)}). Waste {result.waste_pct:.1f}%.</p>"
    ) if stats else ""
    warn = ""
    if result.unplaced:
        items = "".join(
            f"<li>{escape(u.part.name)} × {u.count}: {escape(u.reason)}</li>" for u in result.unplaced
        )
        warn = f"<div class='warn'><b>Not placed:</b><ul>{items}</ul></div>"
    return line + warn + table


def sheet_title(lay: Layout, fmt: Formatter) -> str:
    st = lay.stock
    src = _source(st)
    used_as = f" as {lay.tag}" if lay.tag and not st.tag else ""
    return f"#{lay.number} · {stock_label(st, fmt)}{used_as} ({src})"


def sheet_note(lay: Layout, fmt: Formatter) -> str:
    return f"{len(lay.placements)} parts · {lay.cuts} cuts · {lay.waste_pct:.0f}% waste"


def report_html(result: Result, fmt: Formatter, title: str = "Cut list") -> str:
    s = result.settings
    kinds = {lay.stock.kind for lay in result.layouts}
    kerfs = []
    if StockKind.SHEET in kinds:
        kerfs.append(f"sheets {fmt.length(s.sheet_kerf)}")
    if any(k.is_board for k in kinds):
        kerfs += [f"rips {fmt.length(s.rip_kerf)}", f"crosscuts {fmt.length(s.crosscut_kerf)}"]
        if any(lay.stock.rough for lay in result.layouts):
            kerfs.append(f"rough crosscuts {fmt.length(s.rough_crosscut_kerf)}")
    settings_line = f"Kerf: {', '.join(kerfs) or fmt.length(s.sheet_kerf)} · edge trim {fmt.length(s.edge_trim)}"
    for kind in sorted({lay.stock.kind for lay in result.layouts} | {u.part.kind for u in result.to_find}):
        al, aw = s.allowance(kind)
        if al == aw and al:
            settings_line += f" · {kind} oversize {fmt.length(al)}"
        elif al or aw:
            settings_line += f" · {kind} oversize {fmt.length(al)} on length, {fmt.length(aw)} on width"
    sheets = [
        f"<section class='sheet'><h2>{escape(sheet_title(lay, fmt))}</h2>"
        f"<p class='muted'>{escape(sheet_note(lay, fmt))}</p>"
        f"{layout_svg(lay, fmt, relative=relative_width(lay, result.layouts), pieces=True)}"
        f"{steps_html(lay, fmt, colored=True)}</section>"
        for lay in result.layouts
    ]
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{CSS}</style></head><body><h1>{escape(title)}</h1>"
        f"<p class='muted'>{settings_line}</p>{summary_html(result, fmt)}{''.join(sheets)}</body></html>"
    )
