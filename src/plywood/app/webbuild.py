"""Build the phone shopping list as static files, for any static host (e.g. GitHub Pages).

    uv run python -m plywood.app.webbuild [out_dir]     (default: dist/web)

The page is app/static/shop/; its Python (this package, minus the desktop parts) goes in
plywood.zip next to it and runs in the browser with Pyodide.
"""

from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]  # src/plywood
SHOP = PACKAGE / "app" / "static" / "shop"
SKIP = {"app/desktop.py", "app/server.py", "app/webbuild.py", "cli.py"}


def python_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(PACKAGE.rglob("*.py")):
            rel = path.relative_to(PACKAGE).as_posix()
            if rel not in SKIP and "__pycache__" not in rel:
                z.write(path, f"plywood/{rel}")
    return buf.getvalue()


def build(out: Path) -> Path:
    shop = out / "shop"
    if shop.exists():
        shutil.rmtree(shop)
    shutil.copytree(SHOP, shop)
    (shop / "plywood.zip").write_bytes(python_zip())
    # The site root forwards to the phone page.
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta http-equiv='refresh' content='0; url=shop/'>"
        "<a href='shop/'>Plywood shopping list</a>\n",
        encoding="utf-8",
    )
    return shop


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/web")
    print(f"built {build(out)}")
