"""Read and write parts and stock CSV files.

Length columns accept unit-bearing text (`23-5/8`, `600mm`, `8'`); bare numbers use the
default unit. Headers are case-insensitive and a few aliases are accepted.
"""

from __future__ import annotations

import csv
from pathlib import Path

from plywood.core.models import Grain, Layout, Part, Stock, StockKind
from plywood.core.units import Formatter, parse_length

_ALIASES = {
    "name": ("name", "part", "label", "description"),
    "length": ("length", "l", "len"),
    "width": ("width", "w"),
    "thickness": ("thickness", "t", "thick"),
    "qty": ("qty", "quantity", "count", "pcs"),
    "grain": ("grain",),
    "tag": ("tag",),
    "cost": ("cost", "price"),
    "kind": ("kind", "type"),
}


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = {}
        for col in reader.fieldnames or []:
            key = col.strip().lower()
            for canon, names in _ALIASES.items():
                if key in names and canon not in header:
                    header[canon] = col
        rows = []
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            rows.append({canon: (row.get(col) or "").strip() for canon, col in header.items()})
        return rows


def _required(row: dict[str, str], key: str, line: int) -> str:
    value = row.get(key, "")
    if not value:
        raise ValueError(f"row {line}: missing {key}")
    return value


def read_parts(path: str | Path, default_unit: str = "in") -> list[Part]:
    parts = []
    for n, row in enumerate(_rows(Path(path)), start=2):
        parts.append(
            Part(
                name=row.get("name") or f"Part {n - 1}",
                length=parse_length(_required(row, "length", n), default_unit),
                width=parse_length(_required(row, "width", n), default_unit),
                thickness=parse_length(_required(row, "thickness", n), default_unit),
                qty=int(row.get("qty") or 1),
                grain=Grain.parse(row.get("grain")),
                tag=row.get("tag") or None,
                kind=StockKind.parse(row.get("kind")),
            )
        )
    return parts


def read_stock(path: str | Path, default_unit: str = "in") -> list[Stock]:
    """Blank qty means buy as many as needed; a number means pieces on hand."""
    stocks = []
    for n, row in enumerate(_rows(Path(path)), start=2):
        qty = row.get("qty", "")
        stocks.append(
            Stock(
                name=row.get("name") or f"Stock {n - 1}",
                length=parse_length(_required(row, "length", n), default_unit),
                width=parse_length(_required(row, "width", n), default_unit),
                thickness=parse_length(_required(row, "thickness", n), default_unit),
                qty=int(qty) if qty else None,
                cost=float(row.get("cost") or 0),
                tag=row.get("tag") or None,
                kind=StockKind.parse(row.get("kind")),
            )
        )
    return stocks


def write_cutlist(path: str | Path, layouts: list[Layout], fmt: Formatter) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sheet", "stock", "part", "length", "width", "thickness", "rotated", "x", "y"])
        for lay in layouts:
            for p in lay.placements:
                w.writerow(
                    [
                        lay.number,
                        lay.stock.name,
                        p.label,
                        fmt.bare(p.part.length),
                        fmt.bare(p.part.width),
                        fmt.thickness(p.part.thickness).removesuffix(" mm").removesuffix('"'),
                        "yes" if p.rotated else "no",
                        fmt.bare(p.rect.x),
                        fmt.bare(p.rect.y),
                    ]
                )
