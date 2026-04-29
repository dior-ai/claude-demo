"""``python -m claude_demo audit view <path>``.

Pretty-prints a JSONL audit log as a table. Filters by event type,
tool name, or correlation ID — the same fields a SIEM exposes.

This is the ops surface. A SIEM is for production; ``audit view`` is
for the demo and for one-off investigations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.table import Table

from ..ui.console import get_console


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"audit log not found: {path}")
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{i}: malformed JSON: {exc}") from exc
    return out


def _matches(record: dict, args: argparse.Namespace) -> bool:
    if args.filter_event and record.get("event") != args.filter_event:
        return False
    if args.filter_tool and (record.get("payload") or {}).get("tool") != args.filter_tool:
        return False
    if args.filter_correlation and record.get("correlation_id") != args.filter_correlation:
        return False
    return True


def view_command(args: argparse.Namespace) -> int:
    console = get_console()
    records = _read_records(Path(args.path))
    matched = [r for r in records if _matches(r, args)]

    table = Table(title=f"audit log: {args.path}", show_lines=False, expand=False)
    table.add_column("ts", style="dim")
    table.add_column("step", justify="right")
    table.add_column("event", style="cyan")
    table.add_column("actor", style="dim")
    table.add_column("correlation", style="dim")
    table.add_column("payload")

    for rec in matched:
        ts = (rec.get("ts") or "")[11:23]  # HH:MM:SS.fff
        payload = rec.get("payload") or {}
        # Render payload compactly. Bold the most informative field per event.
        body = ", ".join(f"{k}={_short(v)}" for k, v in payload.items())
        table.add_row(
            ts,
            str(rec.get("step_id") or ""),
            rec.get("event", ""),
            rec.get("actor", ""),
            (rec.get("correlation_id") or "")[:14],
            body,
        )

    console.print(table)
    # highlight=False so Rich doesn't auto-style the integers and identifiers
    # in this summary line. Auto-highlight breaks the message into separate
    # ANSI chunks ("\x1b[..m3\x1b[0m record(s) shown..."), which makes the
    # text fragile to grep against in tests and downstream tooling.
    console.print(
        f"[dim]{len(matched)} record(s) shown of {len(records)} total"
        + (f" — filtered by event='{args.filter_event}'" if args.filter_event else "")
        + (f" tool='{args.filter_tool}'" if args.filter_tool else "")
        + (f" corr='{args.filter_correlation}'" if args.filter_correlation else "")
        + "[/dim]",
        highlight=False,
    )
    return 0


def _short(value: object, limit: int = 80) -> str:
    s = repr(value) if not isinstance(value, str) else value
    return s if len(s) <= limit else s[: limit - 3] + "..."


def register_audit_subparser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "audit",
        help="Audit log operations.",
        description="Audit log operations.",
    )
    audit_sub = parser.add_subparsers(dest="audit_command", required=True)

    view = audit_sub.add_parser("view", help="Pretty-print an audit log file.")
    view.add_argument("path", help="Path to a JSONL audit log (e.g., runs/run_xxx.jsonl).")
    view.add_argument("--filter-event", default="", help="Only show events with this event type.")
    view.add_argument("--filter-tool", default="", help="Only show events for this tool name.")
    view.add_argument(
        "--filter-correlation",
        default="",
        help="Only show events with this correlation_id.",
    )
    view.set_defaults(func=view_command)
