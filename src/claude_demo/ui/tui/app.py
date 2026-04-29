"""Bastion operator console — Textual TUI.

Three-pane layout:

  +--------------------+----------------------------------------+
  | Header (title)                                              |
  +--------------------+----------------------------------------+
  | Runs (list)        | Run summary  +  Events table           |
  |                    +----------------------------------------+
  |                    | Selected event detail (JSON)           |
  +--------------------+----------------------------------------+
  | Footer (key bindings)                                       |
  +--------------------+----------------------------------------+

The console reads from the same ``runs/*.jsonl`` audit logs the rest
of the system writes. It never modifies them — open as many sessions
as you want; this is a viewer, not a writer.

Key bindings:

  q / Q       Quit
  r / R       Refresh run list
  d / D       Run cred-safety demo (subprocess)
  t / T       Run red-team suite (subprocess)
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runs_dir(base: Path | None = None) -> Path:
    return base or Path("runs")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _short_ts(ts: str) -> str:
    # ISO 8601 -> HH:MM:SS.fff
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ts[11:23] if len(ts) > 23 else ts
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _event_color(event: str) -> str:
    if event == "tool_blocked" or event == "policy_decision":
        return "red"
    if event == "task_complete":
        return "green"
    if event == "run_start":
        return "cyan"
    if event == "pre_tool_use":
        return "yellow"
    if event == "post_tool_use":
        return "blue"
    return "white"


def _summarize_run(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up the audit log into a one-line summary."""
    summary = {
        "events": len(records),
        "tool_calls": 0,
        "errors": 0,
        "policy": "",
        "started": "",
        "ended": "",
        "task_complete": False,
    }
    for r in records:
        event = r.get("event", "")
        payload = r.get("payload") or {}
        if event == "run_start":
            summary["policy"] = payload.get("policy", "") or ""
            summary["started"] = r.get("ts", "")
        if event == "post_tool_use":
            summary["tool_calls"] += 1
            if payload.get("is_error"):
                summary["errors"] += 1
        if event == "task_complete":
            summary["task_complete"] = True
            summary["ended"] = r.get("ts", "")
    return summary


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


CSS = """
Screen {
    layout: vertical;
}

#main {
    layout: horizontal;
    height: 1fr;
}

#runs-list {
    width: 36;
    border: solid $primary;
    padding: 0 1;
}

#detail {
    width: 1fr;
    layout: vertical;
}

#meta {
    height: auto;
    border: solid $primary;
    padding: 0 1;
    margin: 0 0 0 0;
}

#events {
    height: 1fr;
    border: solid $primary;
}

#event-detail {
    height: 12;
    border: solid $primary;
    padding: 0 1;
}

ListItem {
    padding: 0 1;
}

ListView > ListItem.--highlight {
    background: $boost;
}
"""


class BastionConsoleApp(App):
    """Operator console for Bastion runs and audit logs."""

    CSS = CSS
    TITLE = "Bastion — Operator Console"
    SUB_TITLE = "hook-driven secure agent runtime"
    BINDINGS = [
        Binding("q,Q", "quit", "Quit"),
        Binding("r,R", "refresh", "Refresh"),
        Binding("d,D", "run_demo", "Run cred-safety"),
        Binding("t,T", "run_redteam", "Run redteam"),
    ]

    selected_run: reactive[str | None] = reactive(None)

    def __init__(self, runs_dir: Path | None = None) -> None:
        super().__init__()
        self._runs_dir = _runs_dir(runs_dir)
        self._records_by_run: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield ListView(id="runs-list")
            with Vertical(id="detail"):
                yield Static(self._meta_placeholder(), id="meta")
                events_table = DataTable(id="events")
                events_table.add_columns("ts", "step", "event", "actor", "tool", "summary")
                events_table.cursor_type = "row"
                events_table.zebra_stripes = True
                yield events_table
                yield Static("Select an event to inspect.", id="event-detail")
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self.action_refresh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        runs = self._discover_runs()
        runs_list = self.query_one("#runs-list", ListView)
        runs_list.clear()
        for run_id, path in runs:
            records = self._records_by_run.setdefault(run_id, _read_jsonl(path))
            summary = _summarize_run(records)
            label = self._format_run_label(run_id, summary)
            runs_list.append(ListItem(Label(label), name=run_id))

        if not runs:
            self._set_meta("[dim]No runs in ./runs/. Press D to run the cred-safety demo, or T to run the redteam suite.[/dim]")
        else:
            # Auto-select first (most recent).
            runs_list.index = 0
            first_id = runs[0][0]
            self._select_run(first_id)

    def action_run_demo(self) -> None:
        self._run_subprocess(["-m", "claude_demo", "run", "cred-safety"])

    def action_run_redteam(self) -> None:
        self._run_subprocess(["-m", "claude_demo", "redteam"])

    # ------------------------------------------------------------------
    # ListView events
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None or item.name is None:
            return
        self._select_run(item.name)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.selected_run is None:
            return
        row_index = event.cursor_row
        records = self._records_by_run.get(self.selected_run, [])
        if 0 <= row_index < len(records):
            record = records[row_index]
            self._set_event_detail(record)

    # ------------------------------------------------------------------
    # Internal: run loading
    # ------------------------------------------------------------------

    def _discover_runs(self) -> list[tuple[str, Path]]:
        if not self._runs_dir.is_dir():
            return []
        # Force a re-read of every file each refresh so adding a new run
        # via the D / T action shows up immediately.
        self._records_by_run.clear()
        files = sorted(
            self._runs_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [(p.stem, p) for p in files]

    def _select_run(self, run_id: str) -> None:
        self.selected_run = run_id
        records = self._records_by_run.get(run_id, [])
        if not records:
            path = self._runs_dir / f"{run_id}.jsonl"
            records = _read_jsonl(path)
            self._records_by_run[run_id] = records

        summary = _summarize_run(records)
        self._set_meta(self._format_meta(run_id, summary))

        table = self.query_one("#events", DataTable)
        table.clear()
        for record in records:
            payload = record.get("payload") or {}
            ts = _short_ts(record.get("ts", ""))
            step = str(record.get("step_id") or "")
            event = record.get("event", "")
            actor = record.get("actor", "")
            tool = payload.get("tool", "")
            summary_str = self._payload_summary(event, payload)
            color = _event_color(event)
            event_styled = f"[{color}]{event}[/{color}]"
            table.add_row(ts, step, event_styled, actor, tool, summary_str)

        self._set_event_detail({"hint": "select an event with arrow keys to inspect"})

    # ------------------------------------------------------------------
    # Internal: rendering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_placeholder() -> str:
        return (
            "[bold cyan]Bastion Operator Console[/bold cyan]\n"
            "[dim]Use ↑/↓ to browse runs · D to run cred-safety · "
            "T to run redteam · R to refresh · Q to quit[/dim]"
        )

    @staticmethod
    def _format_run_label(run_id: str, summary: dict[str, Any]) -> str:
        # Compact run label: id  policy  events  errors
        events = summary["events"]
        errors = summary["errors"]
        policy = summary["policy"] or "?"
        err_tag = (
            f" [red]✗ {errors}[/red]"
            if errors > 0
            else (" [green]✓[/green]" if summary["task_complete"] else "")
        )
        return f"[bold]{run_id}[/bold]\n  policy={policy}  events={events}{err_tag}"

    @staticmethod
    def _format_meta(run_id: str, summary: dict[str, Any]) -> str:
        started = _short_ts(summary["started"])
        ended = _short_ts(summary["ended"])
        status = "[green]complete[/green]" if summary["task_complete"] else "[yellow]running / incomplete[/yellow]"
        errors = summary["errors"]
        err_line = (
            f"[red]errors[/red]      {errors}"
            if errors > 0
            else "[green]errors[/green]      0"
        )
        return (
            f"[bold cyan]{run_id}[/bold cyan]   {status}\n"
            f"policy      {summary['policy'] or '(none)'}\n"
            f"started     {started}\n"
            f"ended       {ended}\n"
            f"events      {summary['events']}\n"
            f"tool calls  {summary['tool_calls']}\n"
            f"{err_line}"
        )

    def _set_meta(self, markup: str) -> None:
        widget = self.query_one("#meta", Static)
        widget.update(markup)

    def _set_event_detail(self, record: dict[str, Any]) -> None:
        widget = self.query_one("#event-detail", Static)
        # Pretty-print the record. Truncate large values so the panel stays readable.
        try:
            text = json.dumps(record, indent=2, sort_keys=False, default=str)
        except TypeError:
            text = repr(record)
        if len(text) > 1500:
            text = text[:1500] + "\n... [truncated]"
        widget.update(text)

    @staticmethod
    def _payload_summary(event: str, payload: dict[str, Any]) -> str:
        if event == "pre_tool_use":
            keys = payload.get("input_keys") or []
            return f"input_keys={keys}"
        if event == "post_tool_use":
            return f"is_error={payload.get('is_error')}, size={payload.get('result_size')}"
        if event == "task_complete":
            return f"steps={payload.get('step_count')} calls={payload.get('tool_call_count')}"
        if event == "run_start":
            return f"policy={payload.get('policy')}"
        # Generic: short repr of the payload.
        items = ", ".join(f"{k}={v!r}" for k, v in payload.items())
        return items if len(items) <= 80 else items[:77] + "..."

    # ------------------------------------------------------------------
    # Subprocess actions (D / T)
    # ------------------------------------------------------------------

    def _run_subprocess(self, args: list[str]) -> None:
        """Fork a python subprocess to run a demo, then refresh."""
        # We don't tee output into the TUI — keeps the implementation simple
        # and lets the operator see the rich CLI's framed output in their
        # original terminal if they spawned the TUI from one. After the run,
        # the new audit log appears in the run list on the next refresh.
        self.notify("Running demo… results will appear in the run list.", severity="information")
        try:
            subprocess.run(
                [sys.executable, *args],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Demo failed: {exc}", severity="error")
            return
        self.action_refresh()


def run_app(runs_dir: Path | None = None) -> int:
    """Entry point. Returns process exit code."""
    app = BastionConsoleApp(runs_dir=runs_dir)
    app.run()
    return 0
