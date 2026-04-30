"""Red-team runner — fires every AttackSpec at the substrate.

The runner builds a fully-instantiated runtime (policy + proxy +
sandbox + audit + all three concrete tools), runs each attack as a
single ScriptedPlan step, and verifies the outcome against the
attack's ``expected_layer``.

Two assertions decide pass/fail:

  1. **Outcome match.** A policy/proxy/tool/sandbox attack must end
     with ``call.is_error`` or ``call.blocked`` set; an "allowed"
     attack must run cleanly. Mismatch = failure.
  2. **Leak check.** The real ``WIDGETS_TOKEN`` value must not appear
     anywhere in the runtime's outputs (tool results, run report, or
     proxy audit URLs). A single occurrence = LEAK regardless of
     individual attack outcomes.

The output of ``run_redteam()`` is a ``RedTeamReport`` that can be
rendered as a rich table or asserted on in tests.
"""

from __future__ import annotations

import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..agents.scripted import ScriptedPlan, ScriptedRunner
from ..audit.log import AuditLog
from ..core.hooks import HookEngine
from ..core.state import RunState, ToolCall
from ..policy import as_pre_hook as policy_pre_hook
from ..policy import load_policy
from ..policy.schema import Policy
from ..proxy.browser import BrowserProxy
from ..proxy.credential import CredentialProxy
from ..sandbox import Sandbox
from ..tools._fake_browser import FakeBrowser, FakePage
from ..tools.browser import make_browser_tool
from ..tools.code import make_code_runner
from ..tools.file import make_file_tool
from ..tools.http import make_http_tool
from .scenarios import ATTACKS, AttackSpec

# Real secret value — exists only in the proxy's secrets table.
# Generated fresh each call so the leak check has a fingerprint that
# can't possibly come from any other source.
def _fresh_secret() -> str:
    return f"sk-redteam-{secrets.token_hex(8)}"


AttackOutcome = Literal[
    "blocked-by-policy",
    "blocked-by-proxy",
    "blocked-by-tool",
    "killed-by-sandbox",
    "allowed",
    "unexpected",
]


@dataclass
class AttackResult:
    """One scenario's verdict."""

    spec: AttackSpec
    passed: bool
    actual_outcome: AttackOutcome
    detail: str

    @property
    def name(self) -> str:
        return self.spec.name


@dataclass
class RedTeamReport:
    """Aggregate result for one redteam run."""

    run_id: str
    audit_path: Path
    results: list[AttackResult] = field(default_factory=list)
    leak_detected: bool = False
    leak_evidence: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and not self.leak_detected


# ---------------------------------------------------------------------------
# Outcome categorization
# ---------------------------------------------------------------------------


def _categorize(call: ToolCall) -> AttackOutcome:
    """Inspect a tool call and decide which defense layer caught it."""
    if call.blocked:
        reason = (call.block_reason or "").lower()
        if "polic" in reason:
            return "blocked-by-policy"
        return "blocked-by-policy"  # any pre-hook block is policy-grade

    if call.is_error:
        result = call.result.lower()
        if "proxy denied egress" in result:
            # Matches both ``proxy denied egress`` (CredentialProxy) and
            # ``browser proxy denied egress`` (BrowserProxy) — same
            # defence layer, same categorization.
            return "blocked-by-proxy"
        if "timed_out" in result or "timed out" in result.lower():
            return "killed-by-sandbox"
        # Tool-level errors: path traversal, unknown ops, malformed input,
        # unknown tool. All of these surface as "ERROR: ..." from the tool
        # surface itself.
        return "blocked-by-tool"

    return "allowed"


def _verify(spec: AttackSpec, call: ToolCall, secret_value: str) -> tuple[bool, AttackOutcome, str]:
    """Decide whether the substrate handled the attack correctly."""
    outcome = _categorize(call)

    expected = spec.expected_layer

    # Layer-level expectations.
    layer_pass = (
        (expected == "policy" and outcome == "blocked-by-policy")
        or (expected == "proxy" and outcome == "blocked-by-proxy")
        or (expected == "tool" and outcome == "blocked-by-tool")
        or (expected == "sandbox" and outcome in ("killed-by-sandbox", "blocked-by-policy"))
        or (expected == "allowed" and outcome == "allowed")
    )

    # Leak check on this individual call's surfaced result.
    leaked_here = secret_value in (call.result or "")

    if leaked_here:
        return (False, outcome, f"LEAK in tool result: real secret value found")

    if not layer_pass:
        return (False, outcome, f"expected {expected}, got {outcome}")

    return (True, outcome, "blocked at expected layer" if expected != "allowed" else "ran cleanly, no leak")


# ---------------------------------------------------------------------------
# Runtime assembly
# ---------------------------------------------------------------------------


def _build_runtime(
    policy: Policy,
    secret_value: str,
    input_dir: Path,
    screenshot_dir: Path,
    audit: AuditLog,
) -> tuple[ScriptedRunner, CredentialProxy, BrowserProxy]:
    proxy = CredentialProxy(
        allowed_hosts=set(policy.http_allowlist),
        secrets={"WIDGETS_TOKEN": secret_value},
    )
    browser_proxy = BrowserProxy(
        allowed_hosts=set(policy.http_allowlist),
        secrets={"WIDGETS_TOKEN": secret_value},
    )

    # A trivial fixture: one page on an allowlisted host with the
    # selectors the browser-layer attacks reference. The point is to
    # let well-formed ops reach the gates we're trying to verify; any
    # off-allowlist navigation fails before the page lookup matters.
    page = FakePage(
        title="redteam fixture",
        text={".price": "$0.00"},
        inputs={"#email", "#card-number", "#cvv", "#submit"},
    )
    browser = FakeBrowser(
        proxy=browser_proxy,
        pages={"http://api.local/page": page},
        screenshot_dir=screenshot_dir,
    )
    # Pre-load the page so fill/click ops in scenarios don't trip on the
    # "no page loaded" guard before reaching the policy / proxy gates.
    browser.goto("http://api.local/page")

    sandbox = Sandbox(timeout_seconds=2.0)

    tools = [
        make_http_tool(proxy),
        make_code_runner(sandbox),
        make_file_tool(input_dir),
        make_browser_tool(browser),
    ]

    hooks = (
        HookEngine()
        .add_pre(policy_pre_hook(policy))
        .add_pre(audit.as_pre_hook())
        .add_post(audit.as_post_hook())
        .add_complete(audit.as_complete_hook())
    )

    runner = ScriptedRunner(tools=tools, hooks=hooks)
    return runner, proxy, browser_proxy


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_redteam(
    policy_path: Path,
    *,
    audit_base: Path = Path("runs"),
) -> RedTeamReport:
    """Fire every scenario in ``ATTACKS`` at the substrate, return a report."""
    policy = load_policy(policy_path)
    secret_value = _fresh_secret()
    run_id = f"redteam_{secrets.token_hex(4)}"

    with tempfile.TemporaryDirectory(prefix="redteam_") as tmp:
        input_dir = Path(tmp)
        screenshot_dir = input_dir / "shots"
        screenshot_dir.mkdir()
        # Stage one benign file so legitimate file_tool ops have something
        # to look at if a future attack tries one.
        (input_dir / "data.txt").write_text("public content\n", encoding="utf-8")

        with AuditLog.for_run(run_id, base_dir=audit_base) as audit:
            audit.emit_run_start(user_input="redteam", policy_name=policy.name)

            runner, proxy, browser_proxy = _build_runtime(
                policy, secret_value, input_dir, screenshot_dir, audit
            )

            # One plan, one runner — every scenario as a single step.
            plan = ScriptedPlan()
            for attack in ATTACKS:
                plan.add(
                    tool_name=attack.tool,
                    tool_input=dict(attack.tool_input),
                    rationale=f"[{attack.name}] {attack.description}",
                )

            state = runner.run(user_prompt="redteam suite", plan=plan)

            results: list[AttackResult] = []
            for attack, step in zip(ATTACKS, state.steps):
                if not step.tool_calls:
                    results.append(
                        AttackResult(
                            spec=attack,
                            passed=False,
                            actual_outcome="unexpected",
                            detail="no tool call recorded",
                        )
                    )
                    continue
                call = step.tool_calls[0]
                ok, outcome, detail = _verify(attack, call, secret_value)
                results.append(
                    AttackResult(
                        spec=attack, passed=ok, actual_outcome=outcome, detail=detail
                    )
                )

            # Run-wide leak check across every surface that could contain it.
            full_state_dump = _state_dump(state)
            audit_dump = "\n".join(
                f"{e.url} {','.join(e.secrets_used)}" for e in proxy.audit_log
            )
            browser_audit_dump = "\n".join(e.url for e in browser_proxy.audit_log)
            leak_detected = (
                secret_value in full_state_dump
                or secret_value in audit_dump
                or secret_value in browser_audit_dump
            )
            leak_evidence = ""
            if leak_detected:
                if secret_value in full_state_dump:
                    leak_evidence = "real secret value found in run state"
                elif secret_value in audit_dump:
                    leak_evidence = "real secret value found in proxy audit URLs"
                elif secret_value in browser_audit_dump:
                    leak_evidence = "real secret value found in browser proxy audit URLs"

            return RedTeamReport(
                run_id=run_id,
                audit_path=Path(audit_base) / f"{run_id}.jsonl",
                results=results,
                leak_detected=leak_detected,
                leak_evidence=leak_evidence,
            )


def _state_dump(state: RunState) -> str:
    """Render every visible byte of run state as a single string for grepping."""
    parts = [state.report(), state.final_text]
    for step in state.steps:
        for call in step.tool_calls:
            parts.append(call.result)
    return "\n".join(p for p in parts if p)
