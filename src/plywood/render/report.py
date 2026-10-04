"""Self-contained, printable HTML report: summary, then one page per sheet or board."""

from __future__ import annotations

from collections import Counter
from html import escape

from plywood.core.models import CutStep, Layout, Result
from plywood.core.units import Formatter
from plywood.render.svg import layout_svg

CSS = """
body { font-family: system-ui, sans-serif; color: #222; background: #fff; margin: 24px; }
h1 { margin: 0 0 4px; } h2 { margin: 0 0 8px; font-size: 1.15rem; }
.muted { color: #666; }
table { border-collapse: collapse; margin: 8px 0 16px; }
td, th { border-bottom: 1px solid #ddd; padding: 4px 10px; text-align: left; font-variant-numeric: tabular-nums; }
.warn { background: #fff3cd; border: 1px solid #e0c060; padding: 8px 12px; margin: 12px 0; }
.sheet { page-break-before: always; margin-top: 32px; }
.sheet svg { max-width: 100%; height: auto; }
ol.steps li { margin: 3px 0; }
.seg-part { font-weight: 600; } .seg-offcut { color: #4b7a3a; } .seg-scrap { color: #999; }
@media print { body { margin: 0; } .sheet { margin-top: 0; } }
"""


def _segment_html(step: CutStep, fmt: Formatter) -> str:
    parts = []
    for seg in step.segments:
        if seg.kind == "scrap" and seg.size <= 0:
            parts.append('<span class="seg-scrap">sliver</span>')
            continue
        label = {"part": seg.label, "piece": "→ more cuts", "offcut": "offcut", "scrap": "scrap"}[seg.kind]
        parts.append(f'<span class="seg-{seg.kind}">{fmt.length(seg.size)} {escape(label)}</span>')
    return " | ".join(parts)


def _steps_html(layout: Layout, fmt: Formatter) -> str:
    if not layout.steps:
        return "<p class='muted'>No cuts needed.</p>"
    items = []
    for step in layout.steps:
        piece = fmt.dims(step.piece.w, step.piece.h)
        verb = "Rip" if step.direction == "rip" else "Crosscut"
        n = len(step.positions)
        cuts = f"{n} cut{'s' if n > 1 else ''}"
        items.append(f"<li><b>{verb}</b> {piece} piece ({cuts}): {_segment_html(step, fmt)}</li>")
    return f"<ol class='steps'>{''.join(items)}</ol>"


def _summary(result: Result, fmt: Formatter) -> str:
    rows = []
    counts = Counter((lay.stock.name, lay.stock.thickness, lay.stock.on_hand, lay.stock.cost) for lay in result.layouts)
    for (name, thick, on_hand, cost), n in counts.items():
        source = "on hand" if on_hand else "buy"
        total = "" if on_hand or not cost else f"${cost * n:,.2f}"
        rows.append(f"<tr><td>{escape(name)}</td><td>{fmt.thickness(thick)}</td><td>{source}</td><td>{n}</td><td>{total}</td></tr>")
    table = (
        "<table><tr><th>Stock</th><th>Thickness</th><th>Source</th><th>Count</th><th>Cost</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    parts_total = sum(len(lay.placements) for lay in result.layouts)
    stats = (
        f"<p>{parts_total} parts on {len(result.layouts)} pieces of stock "
        f"({len(result.purchased)} to buy"
        + (f", ${result.purchase_cost:,.2f}" if result.purchase_cost else "")
        + f"). Waste {result.waste_pct:.1f}%.</p>"
    )
    warn = ""
    if result.unplaced:
        items = "".join(
            f"<li>{escape(u.part.name)} × {u.count}: {escape(u.reason)}</li>" for u in result.unplaced
        )
        warn = f"<div class='warn'><b>Not placed:</b><ul>{items}</ul></div>"
    defaults = [s for s in result.stocks if s.name.endswith("(default)")]
    if defaults:
        thick = ", ".join(sorted({fmt.thickness(s.thickness) for s in defaults}))
        warn += f"<p class='muted'>No stock listed for {thick}; assumed 4×8 sheets.</p>"
    return stats + warn + table


def report_html(result: Result, fmt: Formatter, title: str = "Cut list") -> str:
    s = result.settings
    settings_line = (
        f"Rip kerf {fmt.length(s.rip_kerf)}, crosscut kerf {fmt.length(s.crosscut_kerf)}, "
        f"edge trim {fmt.length(s.edge_trim)}."
    )
    sheets = []
    for lay in result.layouts:
        st = lay.stock
        src = "on hand" if st.on_hand else "buy"
        trim = f" Trim {fmt.length(lay.trim)} from every edge first." if lay.trim else ""
        sheets.append(
            f"<section class='sheet'><h2>#{lay.number} · {escape(st.name)} · "
            f"{fmt.dims(st.length, st.width)} × {fmt.thickness(st.thickness)} ({src})</h2>"
            f"<p class='muted'>{len(lay.placements)} parts, waste {lay.waste_pct:.1f}%. "
            f"Grain runs left to right.{trim}</p>"
            f"{layout_svg(lay, fmt)}{_steps_html(lay, fmt)}</section>"
        )
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<style>{CSS}</style></head><body><h1>{escape(title)}</h1>"
        f"<p class='muted'>{settings_line}</p>{_summary(result, fmt)}{''.join(sheets)}</body></html>"
    )
