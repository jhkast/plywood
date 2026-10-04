"""JSON-in, JSON-out functions behind the app UI.

The UI keeps every table cell as the text the user typed (e.g. "23-5/8", "600mm"); bare numbers
mean the current display unit. Everything is parsed here, so the UI never does unit math.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import webbrowser
from dataclasses import asdict
from pathlib import Path

from plywood.core.guillotine import PlanError
from plywood.core.models import Grain, Part, Result, Settings, Stock, StockKind
from plywood.core.optimize import browse, choose, optimize, restore
from plywood.core.units import Formatter, parse_length
from plywood.core.validate import check
from plywood.io.onshape_bom import looks_like_onshape_bom_text, read_onshape_bom_text
from plywood.io.parts_csv import cutlist_csv, read_parts_text, read_stock_text
from plywood.render.report import report_html, sheet_note, sheet_title, steps_html, summary_html
from plywood.render.svg import layout_svg, relative_width

PART_LENGTHS = ("length", "width", "thickness")
SETTING_LENGTHS = ("kerf", "edge_trim", "allowance")


def state_path() -> Path:
    base = os.environ.get("APPDATA") or Path.home() / ".config"
    return Path(base) / "plywood" / "state.json"


def default_state() -> dict:
    return {
        "version": 1,
        "job": "Untitled",
        "units": "in",
        "denominator": 16,
        "settings": {
            "kerf": "1/8",
            "allowance": "0",
            "edge_trim": "0",
            "default_sheets": True,
            "tries": 1000,
            "priority": "waste",
        },
        "parts": [],
        "stock": [],
        "tag_aliases": {},  # misspelling -> tag, applied to every import and optimize
        "tag_distinct": [],  # "a | b" pairs the user said are different materials
        "result": None,  # {"key": inputs_key(...), "sheets": Result.plan}: the cut list shown last
    }


class FieldError(Exception):
    def __init__(self, table: str, index: int, field: str, message: str):
        super().__init__(message)
        self.info = {"table": table, "index": index, "field": field, "message": message}


def _blank(row: dict) -> bool:
    return not any(str(row.get(k, "")).strip() for k in ("name", *PART_LENGTHS, "qty"))


def _length(
    row: dict, table: str, i: int, field: str, unit: str, required: bool = True, allow_zero: bool = False
) -> float | None:
    text = str(row.get(field, "")).strip()
    if not text:
        if required:
            raise FieldError(table, i, field, f"{field} is required")
        return None
    try:
        value = parse_length(text, unit)
    except ValueError as e:
        raise FieldError(table, i, field, str(e)) from None
    if value < 0 or (value == 0 and not allow_zero):
        raise FieldError(table, i, field, f"{field} must be positive")
    return value


def _int(row: dict, table: str, i: int, field: str, default: int | None) -> int | None:
    text = str(row.get(field, "")).strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        raise FieldError(table, i, field, "must be a whole number") from None
    if value < 0:
        raise FieldError(table, i, field, "can't be negative")
    return value


def _trim_edges(value) -> str | None:
    if value is None:
        return None
    return "".join(e for e in "lrbt" if e in str(value).lower())


def parse_state(state: dict) -> tuple[list[Part], list[Stock], Settings, Formatter, list[dict]]:
    unit = state.get("units", "in")
    aliases = state.get("tag_aliases") or {}

    def tag_of(row: dict) -> str | None:
        tag = str(row.get("tag") or "").strip().lower()
        for _ in range(10):
            if tag not in aliases:
                break
            tag = aliases[tag]
        return tag or None

    errors: list[dict] = []
    parts: list[Part] = []
    stocks: list[Stock] = []

    for i, row in enumerate(state.get("parts", [])):
        if _blank(row):
            continue
        try:
            parts.append(
                Part(
                    name=str(row.get("name") or f"Part {i + 1}").strip(),
                    length=_length(row, "parts", i, "length", unit),
                    width=_length(row, "parts", i, "width", unit),
                    thickness=_length(row, "parts", i, "thickness", unit),
                    qty=_int(row, "parts", i, "qty", 1),
                    grain=Grain.parse(row.get("grain")),
                    tag=tag_of(row),
                    kind=StockKind.parse(row.get("kind")),
                )
            )
        except FieldError as e:
            errors.append(e.info)

    for i, row in enumerate(state.get("stock", [])):
        if _blank(row):
            continue
        try:
            stocks.append(
                Stock(
                    name=str(row.get("name") or ("Board" if StockKind.parse(row.get("kind")) == StockKind.BOARD else "Sheet")).strip(),
                    length=_length(row, "stock", i, "length", unit),
                    width=_length(row, "stock", i, "width", unit),
                    thickness=_length(row, "stock", i, "thickness", unit),
                    qty=_int(row, "stock", i, "qty", None),
                    trim_edges=_trim_edges(row.get("trim_edges")),
                    tag=tag_of(row),
                    kind=StockKind.parse(row.get("kind")),
                )
            )
        except FieldError as e:
            errors.append(e.info)

    s = state.get("settings", {})
    priority = s.get("priority") if s.get("priority") in ("waste", "balanced", "cuts") else "waste"
    try:
        tries = max(0, int(s.get("tries", 1000)))
    except (TypeError, ValueError):
        tries = 1000
    settings = Settings(
        tries=tries,
        default_sheets=bool(s.get("default_sheets", True)),
        priority=priority,
    )
    for field in SETTING_LENGTHS:
        text = str(s.get(field, "")).strip() or "0"
        try:
            value = parse_length(text, unit)
            if field == "kerf":
                settings.rip_kerf = settings.crosscut_kerf = value
            else:
                setattr(settings, field, value)
        except ValueError as e:
            errors.append({"table": "settings", "index": 0, "field": field, "message": str(e)})
    fmt = Formatter(unit, int(state.get("denominator") or 16))
    return parts, stocks, settings, fmt, errors


def inputs_key(parts: list[Part], stocks: list[Stock], settings: Settings) -> str:
    """Fingerprint of everything that decides the layout (not display units or precision)."""

    def clean(v):
        if isinstance(v, float):
            return round(v, 4)
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v

    data = clean([[asdict(p) for p in parts], [asdict(s) for s in stocks], asdict(settings)])
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def part_row(p: Part, fmt: Formatter) -> dict:
    return {
        "name": p.name,
        "length": fmt.exact(p.length),
        "width": fmt.exact(p.width),
        "thickness": fmt.exact(p.thickness),
        "qty": str(p.qty),
        "grain": "" if p.grain == Grain.NONE else str(p.grain),
        "tag": p.tag or "",
        "kind": str(p.kind),
    }


def stock_row(s: Stock, fmt: Formatter) -> dict:
    return {
        "name": s.name,
        "length": fmt.exact(s.length),
        "width": fmt.exact(s.width),
        "thickness": fmt.exact(s.thickness),
        "qty": "" if s.qty is None else str(s.qty),
        "trim_edges": s.trim_edges,
        "tag": s.tag or "",
        "kind": str(s.kind),
    }


class Api:
    """Methods are called from JavaScript (via the local server or pywebview)."""

    def __init__(self, state_file: Path | None = None):
        self._state_file = state_file or state_path()
        self._window = None  # set by the desktop launcher for native file dialogs

    # ------------------------------------------------------------ state

    def load_state(self) -> dict:
        state = default_state()
        try:
            saved = json.loads(self._state_file.read_text(encoding="utf-8"))
            state.update({k: v for k, v in saved.items() if k in state})
            old = saved.get("settings", {})
            state["settings"] = {**default_state()["settings"], **old}
            if "kerf" not in old and "rip_kerf" in old:  # older saves had separate kerfs
                state["settings"]["kerf"] = old["rip_kerf"]
            state["settings"].pop("time_budget", None)  # replaced by tries
        except (OSError, ValueError):
            pass
        return state

    def save_state(self, state: dict) -> bool:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(self._state_file)
        return True

    # ------------------------------------------------------------ optimize

    def _solve(self, state: dict) -> tuple[Result, str]:
        """The saved cut list when the inputs haven't changed since it was made, else a new one."""
        parts, stocks, settings, _, _ = parse_state(state)
        key = inputs_key(parts, stocks, settings)
        saved = state.get("result") or {}
        if saved.get("key") == key:
            try:
                result = restore(parts, stocks, settings, saved["sheets"])
                if not check(result):
                    return result, key
            except (PlanError, KeyError, TypeError):
                pass
        return optimize(parts, stocks, settings), key

    def optimize(self, state: dict) -> dict:
        parts, stocks, settings, fmt, errors = parse_state(state)
        if errors:
            return {"ok": False, "errors": errors}
        if not parts:
            return {"ok": True, "errors": [], "empty": True}
        result, key = self._solve(state)
        return self._response(result, fmt, key, browse(parts, stocks, settings, result.plan))

    def choose(self, state: dict, number: int, position: int) -> dict:
        """Switch one sheet to another of its layouts, keeping every other sheet as it is."""
        parts, stocks, settings, fmt, errors = parse_state(state)
        if errors or not parts:
            return self.optimize(state)
        current, key = self._solve(state)
        try:
            result = choose(parts, stocks, settings, current.plan, int(number), int(position))
        except (PlanError, IndexError) as e:
            return {"ok": False, "errors": [], "message": f"couldn't switch layouts: {e}"}
        return self._response(result, fmt, key, browse(parts, stocks, settings, result.plan))

    def _response(self, result: Result, fmt: Formatter, key: str, options: list[tuple[int, int]]) -> dict:
        problems = check(result)
        if problems:
            return {"ok": False, "errors": [], "message": "internal error: " + "; ".join(problems)}
        return {
            "ok": True,
            "errors": [],
            "plan": {"key": key, "sheets": result.plan},
            "summary": summary_html(result, fmt, stats=False),
            "stats": {
                "stock": len(result.layouts),
                "buy": len(result.purchased),
                "waste": result.waste_pct,
                "parts": sum(len(lay.placements) for lay in result.layouts),
                "cuts": sum(lay.cuts for lay in result.layouts),
                "unplaced": sum(u.count for u in result.unplaced),
            },
            "sheets": [
                {
                    "number": lay.number,
                    "title": sheet_title(lay, fmt),
                    "note": sheet_note(lay, fmt),
                    "svg": layout_svg(lay, fmt, relative=relative_width(lay, result.layouts), pieces=True),
                    "steps": steps_html(lay, fmt, colored=True),
                    "option": pos,
                    "options": count,
                }
                for lay, (pos, count) in zip(result.layouts, options)
            ],
        }

    # ------------------------------------------------------------ import / convert

    def import_parts(self, text: str, units: str = "in", denominator: int = 16) -> dict:
        """Parse a parts CSV or an Onshape BOM export into table rows."""
        fmt = Formatter(units, denominator)
        try:
            if looks_like_onshape_bom_text(text):
                bom = read_onshape_bom_text(text)
                return {"ok": True, "source": "onshape", "rows": [part_row(p, fmt) for p in bom.parts], "skipped": bom.skipped}
            parts = read_parts_text(text, units)
            return {"ok": True, "source": "csv", "rows": [part_row(p, fmt) for p in parts], "skipped": []}
        except (ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}

    def import_stock(self, text: str, units: str = "in", denominator: int = 16) -> dict:
        fmt = Formatter(units, denominator)
        try:
            return {"ok": True, "rows": [stock_row(s, fmt) for s in read_stock_text(text, units)]}
        except (ValueError, KeyError) as e:
            return {"ok": False, "message": str(e)}

    def convert_units(self, state: dict, to_unit: str) -> dict:
        """Rewrite every length cell from the current display unit to `to_unit`."""
        src = state.get("units", "in")
        fmt = Formatter(to_unit, int(state.get("denominator") or 16))

        def conv(text: str) -> str:
            text = str(text).strip()
            if not text:
                return text
            try:
                return fmt.exact(parse_length(text, src))
            except ValueError:
                return text  # leave invalid input for the user to fix

        for table in ("parts", "stock"):
            for row in state.get(table, []):
                for field in PART_LENGTHS:
                    row[field] = conv(row.get(field, ""))
        for field in SETTING_LENGTHS:
            state["settings"][field] = conv(state["settings"].get(field, ""))
        state["units"] = to_unit
        return state

    # ------------------------------------------------------------ export

    def report(self, state: dict) -> dict:
        parts, stocks, settings, fmt, errors = parse_state(state)
        if errors or not parts:
            return {"ok": False, "message": "fix the highlighted cells first" if errors else "no parts"}
        result, _ = self._solve(state)
        return {
            "ok": True,
            "html": report_html(result, fmt, title=state.get("job") or "Cut list"),
            "csv": cutlist_csv(result.layouts, fmt),
        }

    def open_report(self, state: dict) -> dict:
        """Write the printable report to a temp file and open it in the default browser."""
        rep = self.report(state)
        if not rep["ok"]:
            return rep
        path = Path(tempfile.gettempdir()) / "plywood-report.html"
        path.write_text(rep["html"], encoding="utf-8")
        webbrowser.open(path.as_uri())
        return {"ok": True, "path": str(path)}

    def save_text(self, filename: str, text: str) -> dict:
        """Native save dialog (desktop window only)."""
        if self._window is None:
            return {"ok": False, "message": "no native window"}
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result if isinstance(result, str) else result[0]
        Path(path).write_text(text, encoding="utf-8", newline="")
        return {"ok": True, "path": path}
