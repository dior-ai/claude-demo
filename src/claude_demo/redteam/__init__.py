"""Adversarial scenarios that verify the substrate survives attack.

Run via ``python -m claude_demo redteam``. Each scenario is fired at a
fully-instantiated runtime (policy + proxy + sandbox + audit) and the
result is checked against an expected outcome. The leak check at the
end is the load-bearing assertion: across every attack, the real
secret value must never appear in any tool result, audit URL, or
final state report.
"""

from .runner import AttackOutcome, AttackResult, RedTeamReport, run_redteam
from .scenarios import ATTACKS, AttackSpec

__all__ = [
    "ATTACKS",
    "AttackOutcome",
    "AttackResult",
    "AttackSpec",
    "RedTeamReport",
    "run_redteam",
]
