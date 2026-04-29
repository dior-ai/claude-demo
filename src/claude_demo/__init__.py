"""Hook-Driven Secure Agent Runtime — substrate for enterprise AI automation.

Public re-exports for the most-used types. The full surface lives in the
sub-packages (``core``, ``policy``, ``audit``, ``proxy``, ``tools``,
``mcp``, ``agents``, ``ui``, ``cli``).
"""

__version__ = "0.2.0"

from .core.hooks import HookEngine, PostToolUseEvent, PreToolUseEvent, TaskCompleteEvent, ToolBlocked
from .core.permissions import Decision, PermissionPolicy
from .core.state import RunState, StepRecord, ToolCall
from .core.workflow import Workflow

__all__ = [
    "Decision",
    "HookEngine",
    "PermissionPolicy",
    "PostToolUseEvent",
    "PreToolUseEvent",
    "RunState",
    "StepRecord",
    "TaskCompleteEvent",
    "ToolBlocked",
    "ToolCall",
    "Workflow",
]
