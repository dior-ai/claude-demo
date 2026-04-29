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
class Policy:
    """Loaded policy. Drives the PreToolUse hook and the proxy allowlist.

    Three responsibilities:

      - ``decide(tool_name)``  — verdict for that tool (or default)
      - ``http_allowlist``     — set of hostnames the proxy will egress to
      - ``forbidden_code_patterns`` — substrings to block in code_runner

    The same Policy object is consumed by:
      - ``policy.evaluator.as_pre_hook()`` for tool gating + code patterns
      - ``proxy.credential.CredentialProxy`` for HTTP egress
    """

    name: str
    description: str
    tool_rules: dict[str, Decision] = field(default_factory=dict)
    default_decision: Decision = Decision.ALLOW
    http_allowlist: frozenset[str] = field(default_factory=frozenset)
    forbidden_code_patterns: tuple[ForbiddenPattern, ...] = field(default_factory=tuple)

    def decide(self, tool_name: str) -> Decision:
        return self.tool_rules.get(tool_name, self.default_decision)
