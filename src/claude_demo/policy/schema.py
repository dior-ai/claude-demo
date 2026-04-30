"""Policy data classes — what a loaded policy file looks like in memory."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.permissions import Decision


@dataclass(frozen=True)
class ForbiddenPattern:
    """A literal substring that must not appear in ``code_runner`` input.

    Substring match (case-sensitive). Demo-grade — production policy
    engines would use AST analysis or a sandbox at the syscall layer.
    """

    pattern: str
    reason: str


@dataclass(frozen=True)
class ForbiddenSelector:
    """A literal substring forbidden in ``browser_tool`` selectors.

    Used to keep the agent away from sensitive fields (credit-card
    inputs, password fields, etc.) regardless of which page declared
    them. Match is substring, case-sensitive — same shape as
    ``ForbiddenPattern`` so the YAML stays uniform.
    """

    pattern: str
    reason: str


@dataclass(frozen=True)
class Policy:
    """Loaded policy. Drives the PreToolUse hook and the proxy allowlist.

    Five responsibilities:

      - ``decide(tool_name)``  — verdict for that tool (or default)
      - ``http_allowlist``     — set of hostnames the proxy will egress to
      - ``forbidden_code_patterns`` — substrings to block in code_runner
      - ``browser_ops``        — per-op verdict for ``browser_tool``
      - ``browser_forbidden_selectors`` — substrings to block in
                                  ``browser_tool`` selectors

    The same Policy object is consumed by:
      - ``policy.evaluator.as_pre_hook()`` for tool gating, code patterns,
        and browser op/selector checks
      - ``proxy.credential.CredentialProxy`` for HTTP egress
      - ``proxy.browser.BrowserProxy`` for browser-channel egress
    """

    name: str
    description: str
    tool_rules: dict[str, Decision] = field(default_factory=dict)
    default_decision: Decision = Decision.ALLOW
    http_allowlist: frozenset[str] = field(default_factory=frozenset)
    forbidden_code_patterns: tuple[ForbiddenPattern, ...] = field(default_factory=tuple)
    browser_ops: dict[str, Decision] = field(default_factory=dict)
    browser_forbidden_selectors: tuple[ForbiddenSelector, ...] = field(
        default_factory=tuple
    )

    def decide(self, tool_name: str) -> Decision:
        return self.tool_rules.get(tool_name, self.default_decision)

    def decide_browser_op(self, op: str) -> Decision:
        """Verdict for one ``browser_tool`` op.

        Falls back to the tool-level decision (``decide('browser_tool')``)
        when the op isn't called out explicitly. That keeps profiles
        terse: ``gov-airgapped`` denying ``browser_tool`` at the tool
        level is enough; ops never run.
        """
        if op in self.browser_ops:
            return self.browser_ops[op]
        return self.decide("browser_tool")
