"""Launch the app in a native window (pywebview), or in your browser with --browser."""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from pathlib import Path

from plywood.app import server
from plywood.app.api import Api, state_path

MIN_SIZE = (900, 600)


def _geometry_file() -> Path:
    return state_path().with_name("window.json")


def _load_geometry() -> dict | None:
    try:
        g = json.loads(_geometry_file().read_text(encoding="utf-8"))
        return {k: int(g[k]) for k in ("x", "y", "width", "height")} | {"maximized": bool(g.get("maximized"))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _window_geometry(webview) -> dict:
    """Last saved normal size/position (and maximized flag) if it still overlaps a screen;
    otherwise 90% of the screen, centered (keeping a saved maximized flag)."""
    screens = webview.screens
    g = _load_geometry()
    # Older saves stored the maximized frame (just off the screen edge) as the normal size.
    if g is not None and g["maximized"] and any(g["x"] < sc.x or g["y"] < sc.y for sc in screens[:1]):
        g = {"x": -10**6, "y": -10**6, "width": 0, "height": 0, "maximized": True}
    if g is not None:
        overlaps = any(
            g["x"] < sc.x + sc.width - 100 and g["x"] + g["width"] > sc.x + 100
            and g["y"] < sc.y + sc.height - 100 and g["y"] + g["height"] > sc.y + 100
            for sc in screens
        )
        if overlaps:
            return g
    sc = screens[0]
    width = max(MIN_SIZE[0], min(1600, int(sc.width * 0.9)))
    height = max(MIN_SIZE[1], min(1000, int(sc.height * 0.9)))
    return {
        "x": sc.x + max(0, (sc.width - width) // 2),
        "y": sc.y + max(0, (sc.height - height) // 2),
        "width": width,
        "height": height,
        "maximized": bool(g and g["maximized"]),
    }


def _remember_geometry(window, maximized: list[bool], normal: dict) -> None:
    """Save the normal (un-maximized) size and position, plus whether the window was maximized."""
    try:
        g = dict(normal)
        if not maximized[0]:
            g.update(x=window.x, y=window.y, width=window.width, height=window.height)
        g["maximized"] = maximized[0]
        _geometry_file().parent.mkdir(parents=True, exist_ok=True)
        _geometry_file().write_text(json.dumps(g), encoding="utf-8")
    except Exception:  # never block closing the window
        pass


def _save_question(window, job: str) -> str:
    """'save', 'discard' or 'cancel'. Runs on the window's own thread while it's closing."""
    text = f"Save changes to {job}?"
    try:  # Windows: a real Save / Don't Save / Cancel box
        import System.Windows.Forms as WF
        from webview.platforms.winforms import BrowserView

        r = WF.MessageBox.Show(BrowserView.instances.get(window.uid), text, "Plywood",
                               WF.MessageBoxButtons.YesNoCancel, WF.MessageBoxIcon.Warning)
        return "save" if r == WF.DialogResult.Yes else "discard" if r == WF.DialogResult.No else "cancel"
    except Exception:
        return "discard" if window.create_confirmation_dialog("Plywood", f"Close without saving {job}?") else "cancel"


def _ask_to_save(window, api: Api) -> bool:
    """Closing handler: False keeps the window open."""
    if api.unsaved is None or api.quitting:
        return True
    choice = _save_question(window, api.unsaved)
    if choice == "save":
        # The save dialog can't open while the window is mid-close: keep it open, save from the
        # page, and the page closes the window (Api.quit) once the file is written.
        threading.Thread(target=lambda: window.evaluate_js("window.plywoodApp.saveAndClose()"), daemon=True).start()
        return False
    return choice == "discard"


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
            g = _window_geometry(webview)
            window = webview.create_window(
                "Plywood", url, js_api=api, width=g["width"], height=g["height"], x=g["x"], y=g["y"],
                maximized=g["maximized"], min_size=MIN_SIZE, text_select=True,  # pywebview blocks selecting text by default
            )
            api._window = window
            maximized = [g["maximized"]]
            normal = {k: g[k] for k in ("x", "y", "width", "height")}

            def on_restored(*_):
                maximized[0] = False

            def on_resized_or_moved(*_):
                # Windows reports the maximized frame (just past the screen edge) before the maximized event.
                looks_maximized = window.x < 0 or window.y < 0
                if not maximized[0] and not looks_maximized:
                    normal.update(x=window.x, y=window.y, width=window.width, height=window.height)

            window.events.maximized += lambda *a: maximized.__setitem__(0, True)
            window.events.restored += on_restored
            window.events.resized += on_resized_or_moved
            window.events.moved += on_resized_or_moved
            window.events.closing += lambda *a: _remember_geometry(window, maximized, normal)
            window.events.closing += lambda *a: _ask_to_save(window, api)
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
