"""Append-only JSONL audit log.

Every meaningful runtime event lands here as a single JSON line. The
shape is stable enough to feed straight into a SIEM (Splunk, Datadog,
Elastic) and verbose enough to satisfy SOC2 / FedRAMP control language
that demands "all access events captured with actor, timestamp, and
decision".

Key fields (every record carries these):

  ts                ISO 8601 UTC, microsecond precision
  run_id            scoped to one agent run; usable as a correlation ID
  event             enum-like string (see EVENT_* constants)
  actor             which subsystem emitted it ("scripted", "policy", ...)

Per-event payload sits in ``payload`` so log shape never changes when we
add fields. Adapter classes (``as_pre_hook`` / ``as_post_hook`` /
``as_complete_hook``) wire the log into the existing HookEngine without
touching the runner code.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.hooks import (
    PostToolUseEvent,
    PreToolUseEvent,
    TaskCompleteEvent,
)

# Event-type constants. Strings (not enum) so the JSONL stays grep-friendly.
EVENT_RUN_START = "run_start"
EVENT_PRE_TOOL_USE = "pre_tool_use"
EVENT_POST_TOOL_USE = "post_tool_use"
EVENT_TOOL_BLOCKED = "tool_blocked"
EVENT_POLICY_DECISION = "policy_decision"
EVENT_PROXY_DECISION = "proxy_decision"
EVENT_TASK_COMPLETE = "task_complete"

PreHook = Callable[[PreToolUseEvent], None]
PostHook = Callable[[PostToolUseEvent], None]
CompleteHook = Callable[[TaskCompleteEvent], None]


def _new_correlation_id() -> str:
    return f"corr_{secrets.token_hex(6)}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class AuditEvent:
    """One record in the audit log. Stable shape for downstream consumers."""

    ts: str
    run_id: str
    event: str
    actor: str
    correlation_id: str = ""
    step_id: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), default=str)


class AuditLog:
    """Open a JSONL file for one run; emit structured events into it.

    Use as a context manager so the file always closes cleanly:

        with AuditLog.for_run(run_id, base_dir=Path("runs")) as audit:
            audit.emit_run_start(...)
            ...
    """

    def __init__(self, run_id: str, path: Path, *, actor: str = "runtime") -> None:
        self.run_id = run_id
        self.path = path
        self.default_actor = actor
        self._fh = path.open("a", encoding="utf-8")
        self._closed = False
        self._step_counter = 0
        self._pending: tuple[str, int] = ("", 0)

    @classmethod
    def for_run(
        cls, run_id: str, base_dir: Path | str = "runs", *, actor: str = "runtime"
    ) -> "AuditLog":
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        return cls(run_id=run_id, path=base / f"{run_id}.jsonl", actor=actor)

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._fh.flush()
        self._fh.close()
        self._closed = True

    # ------------------------------------------------------------------
    # Emission helpers
    # ------------------------------------------------------------------

    def emit(
        self,
        event: str,
        *,
        actor: str | None = None,
        step_id: int = 0,
        correlation_id: str = "",
        **payload: Any,
    ) -> AuditEvent:
        """Write one event to the log and return the record."""
        record = AuditEvent(
            ts=_utc_now_iso(),
            run_id=self.run_id,
            event=event,
            actor=actor or self.default_actor,
            correlation_id=correlation_id,
            step_id=step_id,
            payload=payload,
        )
        if not self._closed:
            self._fh.write(record.to_jsonl() + "\n")
            self._fh.flush()
        return record

    def emit_run_start(self, *, user_input: str, policy_name: str | None) -> AuditEvent:
        return self.emit(
            EVENT_RUN_START,
            actor=self.default_actor,
            user_input=user_input,
            policy=policy_name,
        )

    # ------------------------------------------------------------------
    # Hook adapters — plug straight into HookEngine
    #
    # Runs are sequential: pre fires, tool executes, post fires, repeat.
    # That means tracking the most-recent pre is enough to correlate the
    # post that follows it. The ``_pending`` slot holds (corr, step) until
    # the matching post-hook consumes it.
    # ------------------------------------------------------------------

    def as_pre_hook(self) -> PreHook:
        def pre(event: PreToolUseEvent) -> None:
            self._step_counter += 1
            corr = _new_correlation_id()
            self._pending = (corr, self._step_counter)
            self.emit(
                EVENT_PRE_TOOL_USE,
                actor="hook_engine",
                step_id=self._step_counter,
                correlation_id=corr,
                tool=event.tool_name,
                input_keys=sorted(event.tool_input.keys()),
            )

        return pre

    def as_post_hook(self) -> PostHook:
        def post(event: PostToolUseEvent) -> None:
            corr, step = getattr(self, "_pending", ("", 0))
            self.emit(
                EVENT_POST_TOOL_USE,
                actor="hook_engine",
                step_id=step,
                correlation_id=corr,
                tool=event.tool_name,
                is_error=event.is_error,
                result_size=len(event.tool_result),
            )

        return post

    def as_complete_hook(self) -> CompleteHook:
        def complete(event: TaskCompleteEvent) -> None:
            self.emit(
                EVENT_TASK_COMPLETE,
                actor="hook_engine",
                step_count=event.step_count,
                tool_call_count=event.tool_call_count,
                final_text_preview=event.final_text[:200],
            )

        return complete
