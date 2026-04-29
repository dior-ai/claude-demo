"""``python -m claude_demo redteam`` — fire every adversarial scenario.

Builds a fresh runtime, runs every ``AttackSpec``, prints a colour-coded
table of results plus a leak-check panel. Returns 0 if all attacks were
caught at their expected layer and no leaks were detected, 1 otherwise.

This is the artifact that converts "looks correct" to "verifiably
survives attack." Run it before every release.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from ..redteam import run_redteam
from ..ui.console import get_console, print_title

POLICIES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "policies"


_OUTCOME_STYLE = {
    "blocked-by-policy": ("[green]POLICY[/green]", "blocked at the PreToolUse hook"),
    "blocked-by-proxy": ("[green]PROXY[/green]", "refused at the host allowlist"),
    "blocked-by-tool": ("[green]TOOL[/green]", "rejected by the tool itself"),
    "killed-by-sandbox": ("[green]SANDBOX[/green]", "terminated by sandbox"),
    "allowed": ("[yellow]ALLOWED[/yellow]", "ran cleanly (no leak)"),
    "unexpected": ("[red]UNEXPECTED[/red]", "did not fire the expected layer"),
}


def _layer_badge(outcome: str) -> str:
    return _OUTCOME_STYLE.get(outcome, (outcome, ""))[0]


def redteam_command(args: argparse.Namespace) -> int:
    console = get_console()
    policy_path = POLICIES_DIR / f"{args.policy}.yaml"
    if not policy_path.is_file():
        console.print(
            f"[red]ERROR[/red]: policy '{args.policy}' not found at {policy_path}"
        )
        return 2

    print_title(f"RED-TEAM SUITE — policy={args.policy}")
    console.print(
        "[dim]Each scenario fires one tool call at the runtime. The runtime "
        "passes if and only if the call is caught at the expected defense "
        "layer AND no leak is detected.[/dim]\n"
    )

    report = run_redteam(policy_path)

    table = Table(show_lines=False, expand=False, header_style="bold cyan")
    table.add_column("#", justify="right", style="dim")
    table.add_column("scenario", style="bold")
    table.add_column("expected")
    table.add_column("caught at")
    table.add_column("status")

    for i, result in enumerate(report.results, start=1):
        expected = result.spec.expected_layer.upper()
        caught = _layer_badge(result.actual_outcome)
        status = (
            "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        )
        table.add_row(str(i), result.spec.name, expected, caught, status)

    console.print(table)

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("scenarios", str(report.total))
    summary.add_row("passed", f"[green]{report.passed}[/green]")
    summary.add_row(
        "failed",
        f"[red]{report.failed}[/red]" if report.failed else f"[green]{report.failed}[/green]",
    )
    summary.add_row("leak detected", "[red]YES[/red]" if report.leak_detected else "[green]no[/green]")
    summary.add_row("audit log", str(report.audit_path))
    console.print(
        Panel(
            summary,
            title="Red-team summary",
            border_style="green" if report.all_passed else "red",
            expand=False,
        )
    )

    if report.leak_detected:
        console.print(
            f"\n[red]LEAK DETECTED[/red]: {report.leak_evidence}",
            highlight=False,
        )
        return 1

    if report.failed:
        console.print(
            "\n[red]Some scenarios were not handled by the expected layer.[/red] "
            "Check the table above and the audit log for details.",
            highlight=False,
        )
        return 1

    console.print(
        f"\n[green]{report.passed}/{report.total} attacks blocked. "
        "0 secrets leaked.[/green]",
        highlight=False,
    )
    return 0


def register_redteam_subparser(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "redteam",
        help="Fire 20+ adversarial scenarios at the runtime; verify each is blocked.",
        description=(
            "Run the adversarial suite. Pass/fail criterion: every scenario "
            "is caught at its expected defense layer AND no real secret value "
            "appears in any output or audit URL."
        ),
    )
    parser.add_argument(
        "--policy",
        default="default",
        help="Policy profile to test under (file under ./policies/<name>.yaml).",
    )
    parser.set_defaults(func=redteam_command)
