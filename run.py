"""Open this file in VS Code and press ▶ (Run Python File). Edit the settings below as needed."""

import webbrowser
from pathlib import Path

from plywood.cli import main

PARTS = "examples/cabinet_parts.csv"  # a parts CSV, or an Onshape BOM CSV export (see featurescript/)
STOCK = "examples/stock.csv"
UNITS = "in"  # "in" or "mm"
KERF = "1/8"
OUT = "out"
OPEN_REPORT = True  # open the report in your browser when done

here = Path(__file__).parent
code = main([
    "optimize", str(here / PARTS),
    "--stock", str(here / STOCK),
    "--units", UNITS,
    "--kerf", KERF,
    "--out", str(here / OUT),
])
if OPEN_REPORT:
    webbrowser.open((here / OUT / "report.html").resolve().as_uri())
