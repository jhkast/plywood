"""Tiny local HTTP server: serves the UI and exposes Api methods at POST /api/<method>."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from plywood.app.api import Api
from plywood.app.webbuild import python_zip

STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
METHODS = {
    "load_state", "save_state", "optimize", "choose", "import_parts", "import_stock", "convert_units", "report",
    "open_report", "phone_job", "import_boards",
}
TYPES[".webmanifest"] = "application/manifest+json"


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            name = self.path.split("?")[0].lstrip("/") or "index.html"
            if (STATIC / name).is_dir() and not name.endswith("/"):
                self.send_response(301)
                self.send_header("Location", "/" + name + "/")
                self.end_headers()
                return
            if name.endswith("/"):
                name += "index.html"
            if name == "shop/plywood.zip":  # the phone page's Python, built from this checkout
                return self._send(200, python_zip(), "application/zip")
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

        def do_POST(self):
            method = self.path.removeprefix("/api/")
            if method not in METHODS:
                return self._send(404, b"{}", "application/json")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                args = json.loads(self.rfile.read(length) or b"[]")
                result = getattr(api, method)(*args)
                self._send(200, json.dumps(result).encode(), "application/json")
            except Exception as e:  # report to the UI instead of dropping the connection
                traceback.print_exc()
                body = json.dumps({"ok": False, "message": f"{type(e).__name__}: {e}"}).encode()
                self._send(500, body, "application/json")

    return Handler


def start(api: Api, port: int = 0) -> ThreadingHTTPServer:
    """Start serving on 127.0.0.1 in a background thread; returns the server (see .server_port)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
