"""Launch the app in a native window (pywebview), or in your browser with --browser."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from plywood.app import server
from plywood.app.api import Api


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="plywood-app")
    ap.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    ap.add_argument("--port", type=int, default=0, help="local port (default: any free port)")
    ap.add_argument("--state", help="state file (default: %%APPDATA%%/plywood/state.json)")
    ap.add_argument("--no-open", action="store_true", help="with --browser: just serve, don't open a browser")
    args = ap.parse_args(argv)

    api = Api(Path(args.state) if args.state else None)
    srv = server.start(api, args.port)
    url = f"http://127.0.0.1:{srv.server_port}/"

    if not args.browser:
        try:
            import webview
        except ImportError:
            print("pywebview isn't installed; opening in the browser instead")
        else:
            window = webview.create_window("Plywood", url, js_api=api, width=1480, height=920, min_size=(900, 600))
            api._window = window
            shown = threading.Event()
            window.events.shown += shown.set
            webview.start()
            if shown.is_set():
                return  # the user closed the window
            print("The app window couldn't open; using your browser instead.")

    print(f"Plywood running at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
