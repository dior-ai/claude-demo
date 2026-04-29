"""Meridian-inspired workflow chaining.

A Workflow is an ordered list of steps. Each step takes a `context` dict,
runs an action, and writes its output back into the context under a chosen
key. The next step sees everything previous steps produced.

This is the workflow primitive the spec describes — *not* a full Meridian
clone. It has three properties that matter:

  - tracks steps in order
  - chains actions
  - passes outputs between steps

For the demo we wrap a single AgentRunner.run() as one step. A real system
would compose retrieval, planning, multiple sub-agents, and post-processing
as separate steps reading from the same context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

StepFn = Callable[[dict[str, Any]], Any]


@dataclass
class WorkflowStep:
    name: str
    action: StepFn
    output_key: str  # where to store the action's return value in the context

    def __call__(self, context: dict[str, Any]) -> Any:
        result = self.action(context)
        context[self.output_key] = result
        return result


@dataclass
class WorkflowRun:
    """Snapshot of one workflow execution."""

    name: str
    context: dict[str, Any] = field(default_factory=dict)
    step_log: list[tuple[str, str]] = field(default_factory=list)
    # (step_name, "ok" | "error: ...")


class Workflow:
    """Ordered, named pipeline that threads a context dict between steps."""

    def __init__(self, name: str = "workflow") -> None:
        self.name = name
        self._steps: list[WorkflowStep] = []

    def step(self, name: str, action: StepFn, output_key: str | None = None) -> "Workflow":
        self._steps.append(
            WorkflowStep(name=name, action=action, output_key=output_key or name)
        )
        return self

    def run(self, initial: dict[str, Any] | None = None) -> WorkflowRun:
        run = WorkflowRun(name=self.name, context=dict(initial or {}))
        for step in self._steps:
            try:
                step(run.context)
                run.step_log.append((step.name, "ok"))
            except Exception as exc:  # noqa: BLE001 - workflow boundary
                run.step_log.append((step.name, f"error: {type(exc).__name__}: {exc}"))
                # Stop on first failure — caller can inspect run.context for
                # what made it through.
                break
        return run
