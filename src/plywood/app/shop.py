"""Shopping list for the lumberyard, on a phone.

The desktop app packs the job into a link (`job_code`). The phone page shows what to buy and
which boards to find; as boards go in the cart it re-plans (`replan`, run in the browser by
Pyodide) and says what's still needed. The boards bought come back to the desktop the same way
(`boards_code` on the phone, `cart_rows` on the desktop).

Codes are compact JSON, deflated and base64url-encoded, so they fit in a link and a QR code.
"""

from __future__ import annotations

import base64
import json
import zlib
from collections import Counter
from dataclasses import replace

from plywood.core.models import Layout, Result, Stock, StockKind
from plywood.core.optimize import optimize
from plywood.core.units import Formatter, parse_length
from plywood.render.report import buy_lines, find_lines, stock_label

VERSION = 3  # 3: hardwood pieces combine parts end to end
PART_FIELDS = ("name", "length", "width", "thickness", "qty", "grain", "tag")
STOCK_FIELDS = ("length", "width", "thickness", "qty", "trim_edges", "rough", "tag")
SETTINGS = (
    "table_saw_kerf", "miter_saw_kerf", "jig_saw_kerf", "edge_trim", "max_planing",
    "rough_cleanup", "edge_joint", "min_planer_length", "snipe", "spare_pct", "waste_pct", "combine_width",
    "combine_length", "piece_extra_width", "min_piece_width", "tries", "priority",
)


def encode(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    c = zlib.compressobj(9, zlib.DEFLATED, -15)  # raw deflate: the browser's "deflate-raw"
    return base64.urlsafe_b64encode(c.compress(raw) + c.flush()).decode().rstrip("=")


def decode(code: str) -> dict:
    code = code.strip()
    for sep in ("#j=", "#b=", "j=", "b="):  # a whole link pasted in works too
        if sep in code:
            code = code.split(sep, 1)[1]
            break
    code = code.split("&")[0].strip()
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)), -15)
        data = json.loads(raw)
    except (ValueError, zlib.error) as e:
        raise ValueError("not a Plywood code") from e
    if not isinstance(data, dict) or data.get("v") != VERSION:
        raise ValueError("not a Plywood code")
    return data


# ---------------------------------------------------------------- what to get


def _part_lines(lays: list[Layout], fmt: Formatter) -> list[str]:
    counts = Counter(
        (p.part.name, p.part.length, p.part.width, p.part.thickness) for lay in lays for p in lay.placements
    )
    return [
        f"{n} × {name} · {fmt.dims(ln, w)} × {fmt.thickness(t)}"
        for (name, ln, w, t), n in sorted(counts.items(), key=lambda kv: (-kv[0][1], kv[0][0]))
    ]


def _unplaced(result: Result) -> list[str]:
    return [f"{u.count} × {u.part.name}: {u.reason}" for u in result.unplaced]


def job_payload(state: dict, result: Result, fmt: Formatter) -> dict:
    """What the phone needs: the shopping list, plus the hardwood parts and stock to re-plan with."""
    s = state.get("settings", {})

    def boards(rows):
        return [r for r in rows if StockKind.parse(r.get("kind")) == StockKind.HARDWOOD
                and any(str(r.get(k, "")).strip() for k in ("length", "width", "thickness"))]

    return {
        "v": VERSION,
        "job": state.get("job") or "Cut list",
        "units": state.get("units", "in"),
        "den": int(state.get("denominator") or 16),
        "settings": {k: s[k] for k in SETTINGS if k in s},
        "aliases": state.get("tag_aliases") or {},
        "parts": [[r.get(f, "") for f in PART_FIELDS] for r in boards(state.get("parts", []))],
        "stock": [[r.get(f, "") for f in STOCK_FIELDS] for r in boards(state.get("stock", []))],
        "buy": buy_lines(result, fmt),
        "find": find_lines(result, fmt),
        "unplaced": _unplaced(result),
    }


# ---------------------------------------------------------------- on the phone


def _state(job: dict) -> dict:
    return {
        "units": job.get("units", "in"),
        "denominator": job.get("den", 16),
        "settings": job.get("settings", {}),
        "tag_aliases": job.get("aliases", {}),
        "parts": [dict(zip(PART_FIELDS, r), kind="hardwood") for r in job.get("parts", [])],
        "stock": [dict(zip(STOCK_FIELDS, r), kind="hardwood") for r in job.get("stock", [])],
    }


def _cart_stock(cart: list[dict], unit: str) -> list[Stock]:
    out = []
    for i, b in enumerate(cart):
        try:
            length, width, thick = (parse_length(str(b[k]), unit) for k in ("length", "width", "thickness"))
        except (KeyError, ValueError) as e:
            raise ValueError(f"board {i + 1}: {e}") from None
        if min(length, width, thick) <= 0:
            raise ValueError(f"board {i + 1}: sizes must be more than 0")
        if width > length:
            length, width = width, length
        tag = str(b.get("tag") or "").strip().lower() or None
        out.append(Stock(f"cart {i}", length, width, thick, qty=1, tag=tag, kind=StockKind.HARDWOOD, rough=bool(b.get("rough", True))))
    return out


def replan(job: dict, cart: list[dict], tries: int | None = None) -> dict:
    """Plan the board parts on the boards in the cart (plus any listed on hand).

    Returns what's still to find, what each cart board makes, and whether that's everything.
    `tries`: at most this many random layouts (a quick first answer on a slow phone).
    """
    from plywood.app.api import parse_state  # the desktop api; imported late to keep this light

    parts, stocks, settings, fmt, errors = parse_state(_state(job))
    if errors:
        return {"ok": False, "message": "; ".join(f"{e['field']}: {e['message']}" for e in errors)}
    if tries is not None:
        settings.tries = min(settings.tries, int(tries))
    try:
        cart_stock = _cart_stock(cart, fmt.unit)
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    # The spares on the original list have to be found too before it's "enough".
    by_name = {p.name: p for p in parts}
    wanted: Counter = Counter()
    for g in job.get("find", []):
        spare_of = g.get("spare_of") or {}
        for b in g.get("blanks", []):
            for label, n in (b.get("spares") or {}).items():
                wanted[(label, spare_of.get(label, label))] += n
    spares = [replace(by_name[part], name=f"{label} (spare)", qty=n, spare=True)
              for (label, part), n in wanted.items() if part in by_name]
    result = optimize(parts + spares, stocks + cart_stock, settings)
    uses: dict[int, list[Layout]] = {}
    for lay in result.layouts:
        if lay.stock.name.startswith("cart "):
            uses.setdefault(int(lay.stock.name.split()[1]), []).append(lay)
    find = find_lines(result, fmt, spares=False)
    initial = {g["key"]: g.get("spare_of", {}) for g in job.get("find", [])}
    for g in find:
        g["spare_of"] = initial.get(g["key"], {})
    unplaced = _unplaced(result)
    return {
        "ok": True,
        "find": find,
        "unplaced": unplaced,
        "cart": [{"label": stock_label(st, fmt), "parts": _part_lines(uses.get(i, []), fmt)} for i, st in enumerate(cart_stock)],
        "enough": not find and not unplaced,
        "complete": settings.tries >= int(job.get("settings", {}).get("tries", 1000)),
    }


def boards_payload(job: dict, cart: list[dict]) -> dict:
    return {"v": VERSION, "job": job.get("job", ""), "units": job.get("units", "in"), "boards": cart}


def cart_rows(code: str, units: str, denominator: int = 16) -> dict:
    """Boards bought, as on-hand Stock rows in the desktop's units (same boards merged)."""
    data = decode(code)
    if "boards" not in data:
        raise ValueError("this is a job link, not boards from the phone")
    src = data.get("units", "in")
    fmt = Formatter(units, denominator)
    rows: dict[tuple, dict] = {}
    for b in data["boards"]:
        dims = sorted((parse_length(str(b[k]), src) for k in ("length", "width")), reverse=True)
        thick = parse_length(str(b["thickness"]), src)
        tag = str(b.get("tag") or "").strip()
        rough = bool(b.get("rough", True))
        key = (round(dims[0], 2), round(dims[1], 2), round(thick, 2), tag.lower(), rough)
        if key in rows:
            rows[key]["qty"] = str(int(rows[key]["qty"]) + 1)
            continue
        rows[key] = {
            "length": fmt.exact(dims[0]), "width": fmt.exact(dims[1]), "thickness": fmt.exact(thick),
            "qty": "1", "trim_edges": None, "kind": "hardwood", "rough": rough, "tag": tag,
        }
    return {"job": data.get("job", ""), "rows": list(rows.values())}


def call(method: str, args_json: str) -> str:
    """Entry point for the phone page's Pyodide worker: JSON in, JSON out."""
    args = json.loads(args_json)
    try:
        if method == "replan":
            out = replan(*args)
        else:
            out = {"ok": False, "message": f"unknown method {method}"}
    except ValueError as e:
        out = {"ok": False, "message": str(e)}
    return json.dumps(out)
