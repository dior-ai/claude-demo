"""Rich-backed renderers for the demo CLI.

Each helper writes to a single shared ``rich.Console``. Output looks
like an enterprise observability tool — framed sections, colored
verdicts, structured tables — without anything as heavy as a web UI.

Helpers are kept narrow on purpose. The CLI calls them in sequence
during a run; tests can capture and assert on the output. If a host
without ``rich`` ever needs to run the demo, swapping these for
``print`` calls is a 30-line change.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_console = Console()


def _short(value: Any, limit: int = 60) -> str:
    s = repr(value) if not isinstance(value, str) else value
    return s if len(s) <= limit else s[: limit - 3] + "..."


def print_title(text: str) -> None:
    _console.print()
    _console.rule(f"[bold cyan]{text}[/bold cyan]")
    _console.print()


def print_plan_panel(
    *,
    run_id: str,
    policy_name: str,
    audit_path: str,
    plan_steps: list[tuple[str, str, dict]],
) -> None:
    """Render the run header + planned tool calls as a framed table.

    plan_steps: list of ``(tool_name, rationale, input_dict)``.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("run_id", run_id)
    table.add_row("policy", policy_name)
    table.add_row("audit", audit_path)
    table.add_row("", "")
    for i, (tool, _rationale, args) in enumerate(plan_steps, start=1):
        short_args = ", ".join(f"{k}={_short(v, 40)}" for k, v in args.items())
        table.add_row(
            f"[cyan]Step {i}[/cyan]",
            f"[bold]{tool}[/bold]  {short_args}",
        )
    _console.print(Panel(table, title="Plan", border_style="cyan", expand=False))


def print_step_event(
    *,
    step: int,
    tool: str,
    decision: str,
    detail: str,
    duration_ms: int | None = None,
    is_error: bool = False,
    blocked: bool = False,
) -> None:
    """One line per executed step. Coloured by outcome."""
    if blocked:
        marker = "[red]BLOCKED[/red]"
    elif is_error:
        marker = "[red]ERR[/red]"
    else:
        marker = "[green]OK[/green]"

    pieces = [
        f"[cyan][{step}][/cyan]",
        f"[bold]{tool}[/bold]",
        f"[yellow]-> {decision}[/yellow]",
        marker,
    ]
    if duration_ms is not None:
        pieces.append(f"[dim]({duration_ms}ms)[/dim]")
    _console.print("  ".join(pieces))
    if detail:
        # Single-line detail beneath the step line, dimmed.
        _console.print(f"      [dim]{detail}[/dim]")


def print_step_result(*, step: int, body_preview: str) -> None:
    """Optional: a short snippet of the tool result, dimmed."""
    if not body_preview:
        return
    head = body_preview.splitlines()[0] if body_preview else ""
    if len(head) > 200:
        head = head[:200] + "..."
    _console.print(f"      [dim]>> {head}[/dim]")


def print_audit_summary(
    *,
    events: int,
    allowed: int,
    blocked: int,
    secrets_substituted: list[str],
    audit_path: str,
) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("events", str(events))
    table.add_row("allowed", f"[green]{allowed}[/green]")
    table.add_row("blocked", f"[red]{blocked}[/red]")
    table.add_row(
        "secrets used",
        ", ".join(sorted(set(secrets_substituted))) or "(none)",
    )
    table.add_row("audit log", audit_path)
    _console.print(Panel(table, title="Audit summary", border_style="green", expand=False))


def print_leak_check(*, in_report: bool, in_audit: bool, blocked_count: int) -> None:
    leaked = in_report or in_audit
    border = "red" if leaked else "green"
    title = "[red]LEAK DETECTED[/red]" if leaked else "[green]No leak[/green]"
    body = Table.grid(padding=(0, 2))
    body.add_column(style="dim")
    body.add_column()
    body.add_row("real secret in run report", _bool_glyph(in_report))
    body.add_row("real secret in audit URLs", _bool_glyph(in_audit))
    body.add_row("exfil attempts blocked", str(blocked_count))
    if not leaked:
        body.add_row("", "[green]agent only ever held the ${PLACEHOLDER}[/green]")
    _console.print(Panel(body, title=f"Leak check — {title}", border_style=border, expand=False))


def _bool_glyph(value: bool) -> str:
    return "[red]YES[/red]" if value else "[green]no[/green]"


def get_console() -> Console:
    """Expose the shared Console for advanced callers (e.g., audit viewer)."""
    return _console


def print_text(text: str | Text) -> None:
    _console.print(text)
