"""Command line: `plywood optimize parts.csv --stock stock.csv`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from plywood.core.models import Settings
from plywood.core.optimize import optimize
from plywood.core.units import Formatter, parse_length
from plywood.core.validate import check
from plywood.io.onshape_bom import looks_like_onshape_bom, read_onshape_bom
from plywood.io.parts_csv import read_parts, read_stock, write_cutlist
from plywood.render.report import report_html
from plywood.render.svg import layout_svg


def _optimize(args: argparse.Namespace) -> int:
    unit = args.units
    length = lambda text: parse_length(text, unit)  # noqa: E731
    if looks_like_onshape_bom(args.parts):
        bom = read_onshape_bom(args.parts)
        parts = bom.parts
        print(f"Onshape BOM: {sum(p.qty for p in parts)} cut parts ({len(parts)} unique)")
        if bom.skipped:
            print(f"  skipped (no cut list dims): {', '.join(bom.skipped)}")
    else:
        parts = read_parts(args.parts, unit)
    stocks = read_stock(args.stock, unit) if args.stock else []
    settings = Settings(
        sheet_kerf=length(args.sheet_kerf or args.kerf),
        rip_kerf=length(args.rip_kerf or args.kerf),
        crosscut_kerf=length(args.crosscut_kerf or args.kerf),
        rough_crosscut_kerf=length(args.rough_kerf or args.kerf),
        edge_trim=length(args.trim),
        default_sheets=not args.no_default_sheets,
        tries=args.tries,
        seed=args.seed,
    )
    result = optimize(parts, stocks, settings)
    problems = check(result)
    if problems:  # should never happen; surface loudly if it does
        print("INTERNAL ERROR, invalid layout:", *problems, sep="\n  ", file=sys.stderr)
        return 2

    fmt = Formatter(unit, args.denominator)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(report_html(result, fmt, title=Path(args.parts).stem), encoding="utf-8")
    write_cutlist(out / "cutlist.csv", result.layouts, fmt)
    for lay in result.layouts:
        (out / f"sheet_{lay.number:02d}.svg").write_text(layout_svg(lay, fmt), encoding="utf-8")

    for lay in result.layouts:
        src = "on hand" if lay.stock.on_hand else "buy"
        print(
            f"#{lay.number:<3} {lay.stock.name:<20} {fmt.dims(lay.stock.length, lay.stock.width):<16} "
            f"{fmt.thickness(lay.stock.thickness):<8} {src:<8} {len(lay.placements):>3} parts  "
            f"waste {lay.waste_pct:5.1f}%"
        )
    print(
        f"\n{len(result.layouts)} pieces of stock ({len(result.purchased)} to buy), "
        f"waste {result.waste_pct:.1f}%, {result.iterations} layouts tried"
    )
    for u in result.unplaced:
        print(f"NOT PLACED: {u.part.name} x{u.count}: {u.reason}")
    print(f"Report: {(out / 'report.html').resolve()}")
    return 1 if result.unplaced else 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    ap =argparse.ArgumentParser(prog="plywood", description="Cut list optimizer")
    sub = ap.add_subparsers(dest="command", required=True)
    op = sub.add_parser("optimize", help="optimize a parts CSV")
    op.add_argument("parts", help="parts CSV (name,length,width,thickness,qty,grain,tag,kind) or an Onshape BOM CSV export")
    op.add_argument("--stock", help="stock CSV: name,length,width,thickness,qty,tag,kind,rough")
    op.add_argument("--units", choices=("in", "mm"), default="in", help="default input unit and display unit")
    op.add_argument("--denominator", type=int, default=16, help="inch fraction precision (default 16)")
    op.add_argument("--kerf", default="1/8in", help="kerf for every saw (the options below override it)")
    op.add_argument("--sheet-kerf", help="sheet cuts (track or table saw)")
    op.add_argument("--rip-kerf", help="board rips (table saw)")
    op.add_argument("--crosscut-kerf", help="board crosscuts (miter saw)")
    op.add_argument("--rough-kerf", help="cutting rough boards into segments (jig saw)")
    op.add_argument("--trim", default="0", help="edge trim on every sheet edge")
    op.add_argument("--tries", type=int, default=1000, help="random layouts to try after the fixed sweep")
    op.add_argument("--seed", type=int, default=0, help="random seed (a different seed gives a different layout)")
    op.add_argument("--no-default-sheets", action="store_true", help="don't assume 4x8 sheets for unmatched thicknesses")
    op.add_argument("--out", default="out", help="output folder")
    args = ap.parse_args(argv)
    if args.command == "optimize":
        return _optimize(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
