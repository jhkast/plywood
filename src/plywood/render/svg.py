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
PIECE = "#2d6fb3"


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


def relative_width(layout: Layout, layouts: list[Layout]) -> float:
    """Fraction of the full width that draws every layout at one scale, so a small piece looks small."""
    longest = max(lay.stock.length for lay in layouts)
    return max(0.15, layout.stock.length / longest)


BADGE_W, BADGE_H = 17, 14

# Distinct, print-friendly colors for lettered pieces (no red: that's the cut lines).
PIECE_COLORS = ("#1f6feb", "#2e9e44", "#e8890c", "#8e44ad", "#0aa3a3", "#e0457b", "#a68a00", "#1b2f6b")


def piece_colors(layout: Layout) -> dict[str, str]:
    """A color per lettered piece, never the same as the piece it was cut from."""
    colors: dict[str, str] = {}
    n = 0
    for step in layout.steps:
        parent = colors.get(step.piece_label)
        for seg in step.segments:
            if seg.kind != "piece":
                continue
            color = PIECE_COLORS[n % len(PIECE_COLORS)]
            n += 1
            if color == parent:
                color = PIECE_COLORS[n % len(PIECE_COLORS)]
                n += 1
            colors[seg.label] = color
    return colors


def _badge_spot(
    x: float, y: float, w: float, h: float, taken: list[tuple[float, float, float]], bw: float = BADGE_W
) -> tuple[float, float]:
    """Top-left of a `bw`-wide badge inside a piece, clear of every badge already placed.

    Tries each corner first, then steps inward from the corners (sideways and up/down).
    """
    pad = 2
    step_x, step_y = bw + pad, BADGE_H + pad
    cols = max(1, int((w - pad) // step_x))
    rows = max(1, int((h - pad) // step_y))
    left, right = x + pad, x + w - bw - pad
    top, bottom = y + pad, y + h - BADGE_H - pad
    candidates = []
    for ring in range(max(cols, rows)):
        for k in range(min(ring, cols - 1) + 1):
            j = ring - k
            if j >= rows:
                continue
            candidates += [
                (right - k * step_x, top + j * step_y),
                (left + k * step_x, top + j * step_y),
                (right - k * step_x, bottom - j * step_y),
                (left + k * step_x, bottom - j * step_y),
            ]
    # Too thin to hold a free spot inside: the nearest free spot around its middle.
    cx, cy = x + w / 2 - bw / 2, y + h / 2 - BADGE_H / 2
    for r in range(1, 8):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) == r:
                    candidates.append((cx + dx * step_x, cy + dy * step_y))
    for bx, by in candidates:
        if all(bx >= tx + tw or tx >= bx + bw or abs(by - ty) >= BADGE_H for tx, ty, tw in taken):
            taken.append((bx, by, bw))
            return bx, by
    taken.append((right, top, bw))
    return right, top


def layout_svg(
    layout: Layout, fmt: Formatter, width_px: float = 900, relative: float | None = None, pieces: bool = False
) -> str:
    """`relative` sizes the drawing as a percentage of its container instead of fixed pixels.

    Lettered pieces (A, B…) are drawn as outlines; visible when `pieces`, otherwise hidden
    until the app highlights one.
    """
    if relative:
        width_px = 640 * relative  # same label size on every sheet, readable when shown ~half-screen
    stock = layout.stock
    scale = width_px / stock.length
    height_px = stock.width * scale

    def px(r: Rect) -> tuple[float, float, float, float]:
        return r.x * scale, (stock.width - r.y - r.h) * scale, r.w * scale, r.h * scale

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 {width_px + 2:.1f} {height_px + 2:.1f}" preserveAspectRatio="xMinYMin meet" '
        + (f'width="{relative * 100:.1f}%" ' if relative else f'width="{width_px + 2:.0f}" height="{height_px + 2:.0f}" ')
        + 'font-family="system-ui, sans-serif">',
        f'<rect x="0" y="0" width="{width_px:.1f}" height="{height_px:.1f}" fill="{SHEET_FILL}" stroke="#555"/>',
    ]
    for r in [*layout.offcuts, *layout.scrap]:
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
    outlines, letters = [], []  # letters are drawn after every outline so none gets covered
    colors = piece_colors(layout)
    planed = {st.piece_label: st.thickness for st in layout.steps if st.direction == "plane"}
    drawn: set[str] = set()
    taken: list[tuple[float, float, float]] = []  # badges already placed: (x, y, width)
    for step in layout.steps:
        x0, y0, w0, h0 = px(step.piece)
        if step.piece_label and step.piece_label not in drawn:
            drawn.add(step.piece_label)
            # A planed segment's badge also shows its thickness: "A 3/4"".
            text = step.piece_label
            if step.piece_label in planed:
                text += " " + fmt.thickness(planed[step.piece_label])
            bw = BADGE_W if len(text) == 1 else 6 * len(text) + 6
            bx, by = _badge_spot(x0, y0, w0, h0, taken, bw)
            color = colors.get(step.piece_label, PIECE)
            hidden = "" if pieces else ' opacity="0"'
            outlines.append(
                f'<g class="piece" data-piece="{step.piece_label}"{hidden}>'
                f'<rect x="{x0 + 1.5:.1f}" y="{y0 + 1.5:.1f}" width="{max(w0 - 3, 1):.1f}" height="{max(h0 - 3, 1):.1f}" '
                f'fill="none" stroke="{color}" stroke-width="2"/></g>'
            )
            letters.append(
                f'<g class="letter" data-piece="{step.piece_label}"{hidden}>'
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw}" height="{BADGE_H}" rx="3" fill="{color}"/>'
                f'<text x="{bx + bw / 2:.1f}" y="{by + BADGE_H / 2 + 0.5:.1f}" font-size="10" font-weight="700" '
                f'fill="#fff" text-anchor="middle" dominant-baseline="middle">{escape(text)}</text></g>'
            )
        if step.direction == "plane":
            continue
        if step.direction == "trim":
            left, right, bottom, top = (t * scale for t in layout.trims)
            xl, xr, yt, yb = x0 + left, x0 + w0 - right, y0 + top, y0 + h0 - bottom
            lines = []
            if left:
                lines.append((xl, y0, xl, y0 + h0))
            if right:
                lines.append((xr, y0, xr, y0 + h0))
            if top:
                lines.append((x0, yt, x0 + w0, yt))
            if bottom:
                lines.append((x0, yb, x0 + w0, yb))
            for a, b, c, d in lines:
                out.append(f'<line data-step="{step.number}" x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d:.1f}" '
                           f'stroke="{CUT}" stroke-width="1" stroke-opacity="0.75"/>')
            continue
        for pos in step.positions:
            if step.direction == "rip":
                yy = (stock.width - pos - step.kerf / 2) * scale
                out.append(f'<line data-step="{step.number}" x1="{x0:.1f}" y1="{yy:.1f}" x2="{x0 + w0:.1f}" '
                           f'y2="{yy:.1f}" stroke="{CUT}" stroke-width="1" stroke-opacity="0.75"/>')
            else:
                xx = (pos + step.kerf / 2) * scale
                out.append(f'<line data-step="{step.number}" x1="{xx:.1f}" y1="{y0:.1f}" x2="{xx:.1f}" '
                           f'y2="{y0 + h0:.1f}" stroke="{CUT}" stroke-width="1" stroke-opacity="0.75"/>')
    out.extend(outlines)
    out.extend(letters)
    out.append("</svg>")
    return "\n".join(out)
