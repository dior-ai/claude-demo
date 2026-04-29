"""Textual-based operator console.

The TUI is the visual ops surface. It reads from the same JSONL audit
logs the rest of the system writes; nothing here changes runtime
behaviour. ``python -m claude_demo ui`` is the entry point.

Imports are delayed to ``app.py`` so the TUI module is only loaded when
the user actually opens the console — keeps the package importable
even when textual isn't installed.
"""

from .app import BastionConsoleApp, run_app

__all__ = ["BastionConsoleApp", "run_app"]
