"""Policy-as-code — YAML rules evaluated as PreToolUse hooks."""

from .evaluator import as_pre_hook
from .loader import load_policy
from .schema import ForbiddenPattern, Policy

__all__ = ["ForbiddenPattern", "Policy", "as_pre_hook", "load_policy"]
