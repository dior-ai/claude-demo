"""CLI entry point: ``python -m claude_demo``.

Four subcommands today:

  run      runs an example end-to-end (the headline operator surface)
  audit    audit-log operations (currently: ``view``)
  redteam  fires 20+ adversarial scenarios at the runtime; pass/fail
  ui       opens the Textual operator console (requires ``textual``)

Designed to feel like an ops tool. Every command emits structured
output through ``ui.console`` so screenshots and screen-recordings
look like an enterprise observability product, not a script.
"""

from __future__ import annotations

import argparse
import sys

from .audit import register_audit_subparser
from .redteam import register_redteam_subparser
from .run import register_run_subparser
from .tui import register_ui_subparser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m claude_demo",
        description="Hook-driven secure agent runtime.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_run_subparser(sub)
    register_audit_subparser(sub)
    register_redteam_subparser(sub)
    register_ui_subparser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
