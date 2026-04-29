"""Per-run state: the agent's working memory.

Tracks every step the agent has taken so the runner can produce a deterministic
report at the end and downstream features (replay, evals, audit logs) have a
single source of truth.

Three records:

  StepRecord     - one full LLM iteration (request + response)
  ToolCall       - one tool_use Claude requested, plus its outcome
  Outputs        - rolling text outputs the LLM has produced

State is intentionally append-only. Don't mutate previous entries; add new
ones. This keeps post-hoc inspection honest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool_use cycle."""

    step: int
    tool_name: str
    tool_input: dict[str, Any]
    result: str
    is_error: bool
    blocked: bool = False
    block_reason: str = ""
    duration_ms: int = 0


@dataclass
class StepRecord:
    """One Claude API turn."""

    step: int
    stop_reason: str  # "tool_use", "end_turn", etc.
    text: str  # any text content the model produced this turn
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class RunState:
    """Aggregate state across an entire agent run."""

    user_input: str
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    steps: list[StepRecord] = field(default_factory=list)
    final_text: str = ""

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def tool_call_count(self) -> int:
        return sum(len(s.tool_calls) for s in self.steps)

    @property
    def total_input_tokens(self) -> int:
        return sum(s.input_tokens for s in self.steps)

    @property
    def total_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.steps)

    def add_step(self, step: StepRecord) -> None:
        self.steps.append(step)

    def finish(self, final_text: str) -> None:
        self.final_text = final_text
        self.ended_at = time.time()

    def report(self) -> str:
        """Human-readable run summary in the format the spec asks for.

        Covers: step list, tool usage, outputs, final result.
        """
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("AGENT RUN REPORT")
        lines.append("=" * 70)
        lines.append(f"User input: {self.user_input}")
        duration = (self.ended_at or time.time()) - self.started_at
        lines.append(f"Duration: {duration:.2f}s")
        lines.append(
            f"Steps: {self.step_count} | Tool calls: {self.tool_call_count} "
            f"| Tokens in/out: {self.total_input_tokens}/{self.total_output_tokens}"
        )
        lines.append("")

        for step in self.steps:
            lines.append(f"--- Step {step.step} (stop_reason={step.stop_reason}) ---")
            if step.text:
                preview = step.text.strip()
                if len(preview) > 400:
                    preview = preview[:400] + "..."
                lines.append(f"  text: {preview}")
            for call in step.tool_calls:
                status = "BLOCKED" if call.blocked else ("ERROR" if call.is_error else "OK")
                lines.append(
                    f"  tool: {call.tool_name} [{status}] {call.duration_ms}ms"
                )
                if call.blocked:
                    lines.append(f"    reason: {call.block_reason}")
                preview = call.result.strip().splitlines()
                if preview:
                    head = preview[0]
                    if len(head) > 200:
                        head = head[:200] + "..."
                    lines.append(f"    result: {head}")
            lines.append("")

        lines.append("--- Final result ---")
        lines.append(self.final_text or "(no final text)")
        lines.append("=" * 70)
        return "\n".join(lines)
