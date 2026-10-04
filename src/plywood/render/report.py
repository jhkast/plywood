"""Self-contained, printable HTML report: summary, then one page per sheet or board."""

from __future__ import annotations

from collections import Counter
from html import escape

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
    whole = "Board" if layout.stock.kind == StockKind.BOARD else "Sheet"
    items = []
    for step in layout.steps:
        source = step.piece_label or whole
        if step.direction == "trim":
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


def summary_html(result: Result, fmt: Formatter, stats: bool = True) -> str:
    rows = []
    counts = Counter((lay.stock.name, lay.stock.thickness, lay.stock.on_hand) for lay in result.layouts)
    for (name, thick, on_hand), n in counts.items():
        source = "on hand" if on_hand else "buy"
        rows.append(f"<tr><td>{escape(name)}</td><td>{fmt.thickness(thick)}</td><td>{source}</td><td>{n}</td></tr>")
    table = (
        "<table><tr><th>Stock</th><th>Thickness</th><th>Source</th><th>Count</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    parts_total = sum(len(lay.placements) for lay in result.layouts)
    line = (
        f"<p>{parts_total} parts on {len(result.layouts)} pieces of stock "
        f"({len(result.purchased)} to buy). Waste {result.waste_pct:.1f}%.</p>"
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
    src = "on hand" if st.on_hand else "buy"
    used_as = f" as {lay.tag}" if lay.tag and not st.tag else ""
    return (f"#{lay.number} · {st.name}{used_as} · {fmt.dims(st.length, st.width)} × "
            f"{fmt.thickness(st.thickness)} ({src})")


def sheet_note(lay: Layout, fmt: Formatter) -> str:
    return f"{len(lay.placements)} parts · {lay.cuts} cuts · {lay.waste_pct:.0f}% waste"


def report_html(result: Result, fmt: Formatter, title: str = "Cut list") -> str:
    s = result.settings
    settings_line = f"Kerf {fmt.length(s.rip_kerf)} · edge trim {fmt.length(s.edge_trim)}"
    if s.allowance:
        settings_line += f" · oversize {fmt.length(s.allowance)}"
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
