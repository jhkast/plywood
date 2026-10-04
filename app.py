"""Open this file in VS Code and press ▶ (Run Python File) to start the Plywood app."""

import subprocess
import sys
from pathlib import Path

VENV_PYTHON = Path(__file__).parent / ".venv" / "Scripts" / "python.exe"

# VS Code may run this with a different Python; hand off to the project's own environment.
if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_PYTHON.parents[1].resolve():
    print(f"Switching to the project's Python: {VENV_PYTHON}")
    raise SystemExit(subprocess.call([str(VENV_PYTHON), __file__, *sys.argv[1:]]))

from plywood.app.desktop import main  # noqa: E402

print("Starting Plywood…")
main(sys.argv[1:])  # add --browser to use your web browser instead of an app window
