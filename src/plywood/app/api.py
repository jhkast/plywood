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

from plywood.app import shop
from plywood.core.guillotine import PlanError
from plywood.core.models import Grain, Part, Result, Settings, Stock, StockKind
from plywood.core.optimize import browse, choose, optimize, restore
from plywood.core.units import Formatter, parse_length
from plywood.core.validate import check
from plywood.io.onshape_bom import looks_like_onshape_bom_text, read_onshape_bom_text
from plywood.io.parts_csv import cutlist_csv, read_parts_text, read_stock_text
from plywood.render.report import (
    report_html, sheet_note, sheet_title, shopping_count, shopping_html, steps_html, summary_html,
)
from plywood.render.svg import layout_svg, relative_width

PART_LENGTHS = ("length", "width", "thickness")
# Lumber settings, with their defaults in inches.
LUMBER_LENGTHS = {
    "max_planing": "1/4",
    "rough_cleanup": "1/8",
    "edge_joint": "1/16",
    "min_planer_length": "18",
    "snipe": "4",
}
# Settings from older versions with nothing to map to.
DROPPED = ("time_budget", "board_width", "board_length", "default_boards")
# Extras: shopping list only, so they never change (or re-run) the layout.
EXTRAS = {"spare_sheets": 0, "spare_sticks": 1, "spare_pct": 10, "waste_pct": 25}
DIMENSIONAL_LENGTHS = "8', 10', 12'"
# Kerf per saw, with defaults in inches. Sheets go on the track saw or the table saw
# (`sheet_saw`); board rips on the table saw, crosscuts on the miter saw, and rough boards are
# cut into segments with the jig saw.
SAW_KERFS = {"track_saw_kerf": "1/16", "table_saw_kerf": "1/8", "miter_saw_kerf": "1/8", "jig_saw_kerf": "1/16"}
KERFS = tuple(SAW_KERFS)
# Older saves: a kerf per kind of cut (and before that one kerf for everything).
OLD_KERFS = {"track_saw_kerf": "sheet_kerf", "table_saw_kerf": "rip_kerf", "miter_saw_kerf": "crosscut_kerf",
             "jig_saw_kerf": "rough_crosscut_kerf"}
SETTING_LENGTHS = (*KERFS, "edge_trim", "allowance", *LUMBER_LENGTHS)


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
            **SAW_KERFS,
            "sheet_saw": "track",
            "allowance": "0",
            "edge_trim": "0",
            "default_sheets": True,
            "default_dimensional": True,
            "dimensional_lengths": DIMENSIONAL_LENGTHS,
            **LUMBER_LENGTHS,
            **EXTRAS,
            "tries": 1000,
            "priority": "waste",
        },
        "parts": [],
        "stock": [],
        "tag_aliases": {},  # misspelling -> tag, applied to every import and optimize
        "tag_distinct": [],  # "a | b" pairs the user said are different materials
        "result": None,  # {"key": inputs_key(...), "sheets": Result.plan}: the cut list shown last
        "phone_url": "",  # where the phone shopping list page is hosted
    }


class FieldError(Exception):
    def __init__(self, table: str, index: int, field: str, message: str):
        super().__init__(message)
        self.info = {"table": table, "index": index, "field": field, "message": message}


def _blank(row: dict) -> bool:
    return not any(str(row.get(k, "")).strip() for k in ("name", *PART_LENGTHS, "qty"))


def _stock_blank(row: dict) -> bool:
    """Stock rows have no name column; a leftover name from an older save doesn't count."""
    return not any(str(row.get(k, "")).strip() for k in PART_LENGTHS)


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


def _lengths_list(text, unit: str) -> tuple[float, ...]:
    """'8', 10', 12'' -> lengths in mm, shortest first."""
    values = [parse_length(t, unit) for t in str(text).replace(";", ",").split(",") if t.strip()]
    if not values or min(values) <= 0:
        raise ValueError("list one or more lengths, e.g. 8', 10', 12'")
    return tuple(sorted(set(values)))


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

    s = state.get("settings", {})
    try:
        dimensional_lengths = _lengths_list(s.get("dimensional_lengths") or DIMENSIONAL_LENGTHS, unit)
    except ValueError as e:
        dimensional_lengths = Settings().dimensional_lengths
        errors.append({"table": "settings", "index": 0, "field": "dimensional_lengths", "message": str(e)})

    for i, row in enumerate(state.get("stock", [])):
        if _stock_blank(row):
            continue
        try:
            kind = StockKind.parse(row.get("kind"))
            qty = _int(row, "stock", i, "qty", None) or None  # 0 = have none, buy as needed
            # Dimensional lumber to buy with no length: whichever standard length wastes least.
            any_length = kind == StockKind.DIMENSIONAL and qty is None and not str(row.get("length") or "").strip()
            lengths = dimensional_lengths if any_length else (_length(row, "stock", i, "length", unit),)
            for length in lengths:
                stocks.append(
                    Stock(
                        name="",  # labeled from its size and material
                        length=length,
                        width=_length(row, "stock", i, "width", unit),
                        thickness=_length(row, "stock", i, "thickness", unit),
                        qty=qty,
                        trim_edges=_trim_edges(row.get("trim_edges")),
                        tag=tag_of(row),
                        kind=kind,
                        rough=bool(row.get("rough")) and kind == StockKind.HARDWOOD,
                    )
                )
        except FieldError as e:
            errors.append(e.info)
        except ValueError as e:
            errors.append({"table": "stock", "index": i, "field": "kind", "message": str(e)})

    priority = s.get("priority") if s.get("priority") in ("waste", "balanced", "cuts") else "waste"
    try:
        tries = max(0, int(s.get("tries", 1000)))
    except (TypeError, ValueError):
        tries = 1000
    settings = Settings(
        tries=tries,
        default_sheets=bool(s.get("default_sheets", True)),
        default_dimensional=bool(s.get("default_dimensional", True)),
        dimensional_lengths=dimensional_lengths,
        priority=priority,
    )
    for field, default in EXTRAS.items():
        try:
            value = max(0, float(str(s.get(field, default)).strip().rstrip("%") or 0))
            setattr(settings, field, int(value) if isinstance(default, int) and field.startswith("spare_s") else value)
        except ValueError:
            errors.append({"table": "settings", "index": 0, "field": field, "message": "must be a number"})
    lengths = {}
    for field in SETTING_LENGTHS:
        text = str(s.get(field, "")).strip() or "0"
        try:
            lengths[field] = parse_length(text, unit)
        except ValueError as e:
            errors.append({"table": "settings", "index": 0, "field": field, "message": str(e)})
    for field, value in lengths.items():
        if field not in SAW_KERFS:
            setattr(settings, field, value)
    saw = "table_saw_kerf" if s.get("sheet_saw") == "table" else "track_saw_kerf"
    settings.sheet_kerf = lengths.get(saw, 0.0)
    settings.rip_kerf = lengths.get("table_saw_kerf", 0.0)
    settings.crosscut_kerf = lengths.get("miter_saw_kerf", 0.0)
    settings.rough_crosscut_kerf = lengths.get("jig_saw_kerf", 0.0)
    fmt = Formatter(unit, int(state.get("denominator") or 16))
    return parts, stocks, settings, fmt, errors


def migrate_settings(settings: dict, old: dict) -> dict:
    """Saw kerfs from older saves, keeping their values so saved cut lists stay the same."""
    settings = dict(settings)
    if not any(k in old for k in SAW_KERFS):
        if any(k in old for k in OLD_KERFS.values()):
            settings.update({new: old.get(was, settings[new]) for new, was in OLD_KERFS.items()})
            settings["sheet_saw"] = "track"
        elif old.get("kerf"):
            settings.update({k: old["kerf"] for k in SAW_KERFS})
    for k in ("kerf", *OLD_KERFS.values()):
        settings.pop(k, None)
    return settings


def inputs_key(parts: list[Part], stocks: list[Stock], settings: Settings) -> str:
    """Fingerprint of everything that decides the layout (not display units or precision)."""

    def clean(v):
        if isinstance(v, float):
            return round(v, 2)  # 1/100 mm: survives in/mm conversion of the cells
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        return v

    layout = {k: v for k, v in asdict(settings).items() if k not in EXTRAS}
    data = clean([[asdict(p) for p in parts], [asdict(s) for s in stocks], layout])
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
        "length": fmt.exact(s.length),
        "width": fmt.exact(s.width),
        "thickness": fmt.exact(s.thickness),
        "qty": "" if s.qty is None else str(s.qty),
        "trim_edges": s.trim_edges,
        "tag": s.tag or "",
        "kind": str(s.kind),
        "rough": s.rough,
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
            state["settings"] = migrate_settings(state["settings"], old)
            for k in DROPPED:
                state["settings"].pop(k, None)
            for row in [*state["parts"], *state["stock"]]:  # "board" became hardwood
                if str(row.get("kind", "")).lower() == "board":
                    row["kind"] = "hardwood"
            if state.get("units") == "mm":  # new settings arrive with inch defaults
                mm = Formatter("mm")
                for field, default in {**LUMBER_LENGTHS, **SAW_KERFS}.items():
                    migrated = field in SAW_KERFS and (old.get("kerf") or OLD_KERFS[field] in old)
                    if field not in old and not migrated:
                        state["settings"][field] = mm.exact(parse_length(default, "in"))
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

    def _solve(self, state: dict, keep: bool = False) -> tuple[Result | None, str]:
        """The saved cut list when the inputs haven't changed since it was made, else a new one.

        `keep` (opening a file or the app): always rebuild the saved cut list, even if the
        fingerprint changed (say, an update added a setting). None if it no longer fits.
        """
        parts, stocks, settings, _, _ = parse_state(state)
        key = inputs_key(parts, stocks, settings)
        saved = state.get("result") or {}
        if saved.get("sheets") is not None and (keep or saved.get("key") == key):
            try:
                result = restore(parts, stocks, settings, saved["sheets"])
                if not check(result):
                    return result, key
            except (PlanError, KeyError, TypeError):
                pass
            if keep:
                return None, key
        return optimize(parts, stocks, settings), key

    def optimize(self, state: dict, keep: bool = False) -> dict:
        parts, stocks, settings, fmt, errors = parse_state(state)
        if errors:
            return {"ok": False, "errors": errors}
        if not parts:
            return {"ok": True, "errors": [], "empty": True}
        result, key = self._solve(state, keep)
        if result is None:
            return {"ok": False, "errors": [], "stale": True,
                    "message": "The saved cut list no longer fits these parts and stock."}
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
            "summary": summary_html(result, fmt, stats=False, shopping=False),
            "shopping": shopping_html(result, fmt),
            "stats": {
                "stock": len(result.layouts),
                "buy": len(result.purchased),
                "find": sum(u.count for u in result.to_find),
                "shop": shopping_count(result, fmt),
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
        try:
            values = _lengths_list(state["settings"].get("dimensional_lengths") or DIMENSIONAL_LENGTHS, src)
            feet = to_unit == "in" and all(abs(v / 304.8 - round(v / 304.8)) < 1e-6 for v in values)
            state["settings"]["dimensional_lengths"] = ", ".join(
                f"{round(v / 304.8)}'" if feet else fmt.exact(v) for v in values
            )
        except ValueError:
            pass
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

    def phone_job(self, state: dict) -> dict:
        """The job's shopping list as a code for the phone page's link."""
        parts, stocks, settings, fmt, errors = parse_state(state)
        if errors or not parts:
            return {"ok": False, "message": "fix the highlighted cells first" if errors else "no parts"}
        result, _ = self._solve(state)
        return {"ok": True, "code": shop.encode(shop.job_payload(state, result, fmt))}

    def import_boards(self, code: str, units: str = "in", denominator: int = 16) -> dict:
        """Boards bought at the yard (a code or link from the phone) as on-hand Stock rows."""
        try:
            return {"ok": True, **shop.cart_rows(code, units, denominator)}
        except (ValueError, KeyError, TypeError) as e:
            return {"ok": False, "message": str(e)}

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
