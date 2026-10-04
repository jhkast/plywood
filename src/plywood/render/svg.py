"""SVG diagram of one layout. Origin is bottom-left; x runs along the stock grain."""

from __future__ import annotations

from html import escape

from plywood.core.models import Layout, Rect
from plywood.core.units import Formatter

PART_FILL = "#ead7b0"
PART_STROKE = "#7a5a2b"
OFFCUT_FILL = "#e3ecdc"
OFFCUT_STROKE = "#7d9a6d"
SHEET_FILL = "#b9b4ab"  # shows through as kerf and scrap
TEXT = "#2b2116"
CUT = "#c0392b"


def _text_block(lines: list[str], r: Rect, px, max_font: float = 14) -> str:
    """Centered multi-line label that fits inside rect r (in layout units)."""
    x, y, w, h = px(r)
    vertical = h > w * 1.4
    box_w, box_h = (h, w) if vertical else (w, h)
    longest = max(len(t) for t in lines)
    font = min(max_font, (box_w - 4) / (0.58 * longest), (box_h - 4) / (1.2 * len(lines)))
    if font < 6:
        lines = lines[:1]
        font = min(max_font, (box_w - 2) / (0.58 * len(lines[0])), box_h - 2)
        if font < 5:
            return ""
    cx, cy = x + w / 2, y + h / 2
    rot = f' transform="rotate(-90 {cx:.1f} {cy:.1f})"' if vertical else ""
    first = cy - (len(lines) - 1) * font * 0.6
    tspans = "".join(
        f'<tspan x="{cx:.1f}" y="{first + i * font * 1.2:.1f}">{escape(t)}</tspan>' for i, t in enumerate(lines)
    )
    return (
        f'<text font-size="{font:.1f}" text-anchor="middle" dominant-baseline="middle" '
        f'fill="{TEXT}"{rot}>{tspans}</text>'
    )


def layout_svg(layout: Layout, fmt: Formatter, width_px: float = 900) -> str:
    stock = layout.stock
    scale = width_px / stock.length
    height_px = stock.width * scale

    def px(r: Rect) -> tuple[float, float, float, float]:
        return r.x * scale, (stock.width - r.y - r.h) * scale, r.w * scale, r.h * scale

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 {width_px + 2:.1f} {height_px + 2:.1f}" '
        f'width="{width_px + 2:.0f}" height="{height_px + 2:.0f}" font-family="system-ui, sans-serif">',
        f'<rect x="0" y="0" width="{width_px:.1f}" height="{height_px:.1f}" fill="{SHEET_FILL}" stroke="#555"/>',
    ]
    for r in layout.offcuts:
        x, y, w, h = px(r)
        out.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{OFFCUT_FILL}" '
            f'stroke="{OFFCUT_STROKE}" stroke-dasharray="4 3"/>'
        )
        out.append(_text_block([fmt.dims(r.w, r.h)], r, px, max_font=11))
    for p in layout.placements:
        x, y, w, h = px(p.rect)
        out.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{PART_FILL}" '
            f'stroke="{PART_STROKE}"><title>{escape(p.label)}: {escape(fmt.dims(p.part.length, p.part.width))}'
            f"</title></rect>"
        )
        out.append(_text_block([p.label, fmt.dims(p.part.length, p.part.width)], p.rect, px))
    for step in layout.steps:
        x0, y0, w0, h0 = px(step.piece)
        for pos in step.positions:
            if step.direction == "rip":
                yy = (stock.width - pos - step.kerf / 2) * scale
                out.append(f'<line x1="{x0:.1f}" y1="{yy:.1f}" x2="{x0 + w0:.1f}" y2="{yy:.1f}" stroke="{CUT}" '
                           f'stroke-width="0.6" stroke-opacity="0.6"/>')
            else:
                xx = (pos + step.kerf / 2) * scale
                out.append(f'<line x1="{xx:.1f}" y1="{y0:.1f}" x2="{xx:.1f}" y2="{y0 + h0:.1f}" stroke="{CUT}" '
                           f'stroke-width="0.6" stroke-opacity="0.6"/>')
    out.append("</svg>")
    return "\n".join(out)
