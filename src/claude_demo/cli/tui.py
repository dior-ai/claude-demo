"""``python -m claude_demo ui`` — open the Textual operator console.

The TUI is an optional install. If ``textual`` isn't on the path, we
print a helpful error pointing at the right pip extra rather than
crashing with an obscure ImportError.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def ui_command(args: argparse.Namespace) -> int:
    try:
        from ..ui.tui import run_app
    except ImportError as exc:
        print(
            "ERROR: the operator console requires the 'textual' package.\n"
            "  pip install -e \".[ui]\"\n"
            f"  (original error: {exc})"
        )
        return 2

    runs_dir = Path(args.runs_dir).resolve()
    return run_app(runs_dir=runs_dir)


def register_ui_subparser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "ui",
        help="Open the Textual operator console (browse runs, audit events).",
        description=(
            "Open the operator console: a TUI that lists every run under "
            "./runs/, lets you select one, and renders its audit log as a "
            "table. Press D inside the TUI to fire the cred-safety demo, "
            "T to run the red-team suite."
        ),
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Directory containing audit JSONL files (default: ./runs).",
    )
    parser.set_defaults(func=ui_command)
