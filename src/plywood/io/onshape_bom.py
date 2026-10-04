"""Import an Onshape assembly BOM exported as CSV.

Each cut part carries a "cut list dims" string (written into Title 1 by the
`Cut list dims` FeatureScript), e.g.:

    762 x 590.55 x 19.05 mm sheet grain=L tag=birch

i.e. `<length> x <width> x <thickness> <mm|in> <sheet|board> [grain=L|W] [tag=<text>]`.
The importer finds that string in whatever column holds it, so the column name doesn't matter.
Structured (multi-level) BOMs are supported: item numbers like 2.3 multiply by the parent's quantity.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from plywood.core.models import Grain, Part, StockKind
from plywood.core.units import MM_PER_INCH

DIMS = re.compile(
    r"^\s*(?P<l>\d+(?:\.\d*)?)\s*[x×]\s*(?P<w>\d+(?:\.\d*)?)\s*[x×]\s*(?P<t>\d+(?:\.\d*)?)\s*"
    r"(?P<unit>in|mm)\b(?P<rest>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Dims:
    length: float  # mm
    width: float
    thickness: float
    kind: StockKind
    grain: Grain
    tag: str | None


def parse_dims(text: str) -> Dims | None:
    m = DIMS.match(text or "")
    if not m:
        return None
    scale = MM_PER_INCH if m["unit"].lower() == "in" else 1.0
    rest = m["rest"].strip()
    tag = None
    if "tag=" in rest.lower():
        i = rest.lower().index("tag=")
        tag = rest[i + 4 :].strip() or None
        rest = rest[:i]
    kind, grain = StockKind.SHEET, Grain.NONE
    for word in rest.lower().split():
        if word in ("sheet", "board"):
            kind = StockKind(word)
        elif word.startswith("grain="):
            grain = Grain.parse(word.removeprefix("grain="))
    return Dims(float(m["l"]) * scale, float(m["w"]) * scale, float(m["t"]) * scale, kind, grain, tag)


@dataclass
class BomImport:
    parts: list[Part]
    skipped: list[str] = field(default_factory=list)  # BOM rows with no cut list dims (hardware etc.)


def _find(header: list[str], *names: str) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


_MIRRORED = re.compile(r"[\s_-]*\(?mirrored\)?\s*$", re.IGNORECASE)


def looks_like_onshape_bom(path: str | Path) -> bool:
    return looks_like_onshape_bom_text(Path(path).read_text(encoding="utf-8-sig"))


def looks_like_onshape_bom_text(text: str) -> bool:
    return any(parse_dims(cell) for row in csv.reader(io.StringIO(text)) for cell in row)


def read_onshape_bom(path: str | Path) -> BomImport:
    return read_onshape_bom_text(Path(path).read_text(encoding="utf-8-sig"))


def read_onshape_bom_text(text: str) -> BomImport:
    text = text.removeprefix("﻿")
    rows = [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]

    # The header is the first row with a quantity column (exports may start with a title line).
    start = next(
        (i for i, row in enumerate(rows) if _find(row, "quantity", "qty", "count") is not None), None
    )
    if start is None:
        raise ValueError("no Quantity column found; is this an Onshape BOM CSV export?")
    header, body = rows[start], rows[start + 1 :]
    qty_col = _find(header, "quantity", "qty", "count")
    name_col = _find(header, "name", "part name", "description")
    item_col = _find(header, "item", "item number", "#")

    def cell(row: list[str], col: int | None) -> str:
        return row[col].strip() if col is not None and col < len(row) else ""

    items = [cell(r, item_col) for r in body]
    has_children = {it.rsplit(".", 1)[0] for it in items if "." in it}
    effective: dict[str, int] = {}
    merged: dict[tuple, int] = {}
    by_name: dict[str, Dims] = {}
    missing: list[tuple[str, int]] = []

    for row, item in zip(body, items):
        qty_text = cell(row, qty_col)
        try:
            qty = int(float(qty_text)) if qty_text else 1
        except ValueError:
            qty = 1
        if item and "." in item:
            qty *= effective.get(item.rsplit(".", 1)[0], 1)
        if item:
            effective[item] = qty

        name = cell(row, name_col) or f"Item {item}".strip()
        dims = next((d for c in row if (d := parse_dims(c))), None)
        if dims is None:
            if item not in has_children:  # subassemblies have no dims; that's expected
                missing.append((name, qty))
            continue
        by_name.setdefault(name, dims)
        merged[(name, dims)] = merged.get((name, dims), 0) + qty

    # Assembly-mirrored parts live in their own Part Studio; borrow the original's dims if needed.
    skipped: list[str] = []
    for name, qty in missing:
        original = by_name.get(_MIRRORED.sub("", name)) if _MIRRORED.search(name) else None
        if original is None:
            skipped.append(name)
        else:
            merged[(name, original)] = merged.get((name, original), 0) + qty

    if not merged:
        raise ValueError(
            "no cut list dims found; add the 'Cut list dims' feature to your Part Studios "
            "and a Title 1 column to the assembly BOM before exporting"
        )

    parts = [
        Part(name, d.length, d.width, d.thickness, qty=qty, grain=d.grain, tag=d.tag, kind=d.kind)
        for (name, d), qty in merged.items()
    ]
    return BomImport(parts, skipped)
