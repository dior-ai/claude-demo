"""``python -m claude_demo run <example>``.

Runs one of the bundled examples through the runtime. T1 ships
``cred-safety``; the other slots are reserved for the T2 demos
(``browser-research``, ``multi-agent-mcp``).

The CLI surface is intentionally small: ``run <name> [--policy ...]``.
Discovery is by importable example module — adding a new example is a
matter of adding a sibling under ``examples/`` with a ``main()`` that
accepts an args namespace and returns an exit code.
"""

from __future__ import annotations

import argparse
import importlib
from typing import Callable

# Friendly slug -> dotted module path. Keeps the CLI vocabulary stable
# even as folders are renamed.
EXAMPLES: dict[str, str] = {
    "cred-safety": "examples.cred_safety.run",
    "browser-research": "examples.browser_research.run",
    # Real Claude + real Playwright + real internet. Costs API tokens.
    "research-assistant": "examples.research_assistant.run",
    # T2:
    # "multi-agent-mcp":   "examples.multi_agent_mcp.run",
}


def _resolve(name: str) -> Callable[[argparse.Namespace], int]:
    if name not in EXAMPLES:
        raise SystemExit(
            f"unknown example '{name}'. Available: {', '.join(sorted(EXAMPLES))}"
        )
    module = importlib.import_module(EXAMPLES[name])
    if not hasattr(module, "main"):
        raise SystemExit(f"example module '{EXAMPLES[name]}' has no main(args) function")
    return module.main  # type: ignore[no-any-return]


def run_command(args: argparse.Namespace) -> int:
    fn = _resolve(args.example)
    return fn(args)


def register_run_subparser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "run",
        help="Run a bundled example end-to-end.",
        description="Run a bundled example end-to-end.",
    )
    parser.add_argument(
        "example",
        choices=sorted(EXAMPLES.keys()),
        help="Example to run.",
    )
    parser.add_argument(
        "--policy",
        default="default",
        help="Policy profile name (file under ./policies/<name>.yaml).",
    )
    # Currently only ``browser-research`` consumes ``--engine``; the
    # other examples ignore it. Keeping the flag at the run-command
    # level (vs. an opaque pass-through) means the CLI surface stays
    # discoverable via ``--help``.
    parser.add_argument(
        "--engine",
        choices=["fake", "playwright"],
        default="fake",
        help=(
            "Browser engine for the browser-research example. "
            "'fake' (default) is in-process; 'playwright' drives real "
            "headless Chromium and requires `pip install -e \".[browser]\"`."
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help=(
            "Show the browser window while it runs (browser-research + "
            "--engine playwright only). Slows each op so the action is "
            "watchable; useful for screencasts and live demos."
        ),
    )
    # research-assistant flags. The other examples ignore these.
    parser.add_argument(
        "--topic",
        default=None,
        help="Topic for the research-assistant example (required for it).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output path for the research-assistant example. "
            "Default: output/research.md."
        ),
    )
    parser.set_defaults(func=run_command)
