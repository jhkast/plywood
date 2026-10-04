"""Length parsing and formatting. Everything is stored internally in millimetres."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import gcd

MM_PER_INCH = 25.4

_UNIT_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": MM_PER_INCH,
    '"': MM_PER_INCH,
    "ft": 12 * MM_PER_INCH,
    "'": 12 * MM_PER_INCH,
}

_NUMBER = re.compile(r"\d+(?:\.\d*)?|\.\d+")
_FRACTION = re.compile(r"(?:(\d+)[\s-]+)?(\d+)/(\d+)")
_FEET_INCHES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:'|ft)\s*-?\s*(.*)")
_SUFFIX = re.compile(r"(.*?)\s*(mm|cm|m|in|\"|ft|')")


def _parse_number(text: str) -> float:
    text = text.strip()
    if _NUMBER.fullmatch(text):
        return float(text)
    m = _FRACTION.fullmatch(text)
    if m:
        whole = int(m[1] or 0)
        denominator = int(m[3])
        if denominator == 0:
            raise ValueError(f"zero denominator in {text!r}")
        return whole + int(m[2]) / denominator
    raise ValueError(f"can't parse number {text!r}")


def parse_length(text: str | float | int, default_unit: str = "in") -> float:
    """Parse a length like `23-5/8`, `23 5/8"`, `600mm`, `8'`, or `8' 6-1/2"` into mm.

    Bare numbers use `default_unit` ("in" or "mm").
    """
    if isinstance(text, (int, float)):
        return float(text) * _UNIT_MM[default_unit]
    s = text.strip().lower().replace("″", '"').replace("′", "'").replace("”", '"').replace("’", "'")
    if not s:
        raise ValueError("empty length")

    m = _FEET_INCHES.fullmatch(s)
    if m and m[2].strip():
        rest = m[2].strip().removesuffix('"').removesuffix("in").strip()
        return float(m[1]) * _UNIT_MM["ft"] + _parse_number(rest) * MM_PER_INCH

    m = _SUFFIX.fullmatch(s)
    if m:
        return _parse_number(m[1]) * _UNIT_MM[m[2]]
    return _parse_number(s) * _UNIT_MM[default_unit]


@dataclass(frozen=True)
class Formatter:
    """Formats mm values for display, as inches with fractions or as mm."""

    unit: str = "in"  # "in" or "mm"
    denominator: int = 16  # fraction precision for inches

    def __call__(self, mm: float) -> str:
        return self.length(mm)

    def length(self, mm: float) -> str:
        if self.unit == "mm":
            text = f"{mm:.1f}".rstrip("0").rstrip(".")
            return f"{text} mm"
        return format_inches(mm / MM_PER_INCH, self.denominator)

    def dims(self, *values: float) -> str:
        if self.unit == "mm":
            body = " × ".join(f"{v:.1f}".rstrip("0").rstrip(".") for v in values)
            return f"{body} mm"
        return " × ".join(format_inches(v / MM_PER_INCH, self.denominator) for v in values)

    def thickness(self, mm: float) -> str:
        """Thickness needs finer precision: 23/32" ply must not display as 3/4"."""
        if self.unit == "mm":
            return f"{mm:.2f}".rstrip("0").rstrip(".") + " mm"
        return format_inches(mm / MM_PER_INCH, max(self.denominator, 64))

    def bare(self, mm: float) -> str:
        """Length without a unit suffix."""
        return self.length(mm).removesuffix(" mm").removesuffix('"')


def format_inches(inches: float, denominator: int = 16) -> str:
    sign = "-" if inches < 0 else ""
    ticks = round(abs(inches) * denominator)
    whole, num = divmod(ticks, denominator)
    if num == 0:
        return f'{sign}{whole}"'
    g = gcd(num, denominator)
    frac = f"{num // g}/{denominator // g}"
    if whole == 0:
        return f'{sign}{frac}"'
    return f'{sign}{whole}-{frac}"'
