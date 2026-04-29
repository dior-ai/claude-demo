"""End-to-end demo runner.

Wires every component together:

  prompt
    │
    ├─ Sandbox(timeout, input files)         <- secure execution
    ├─ Tools = [code_runner, file_tool]      <- real, not mocked
    ├─ HookEngine                            <- lifecycle interception
    │     pre:  permission policy + safety + logging
    │     post: truncate + logging
    │     done: logging
    ├─ AgentRunner(Claude)                   <- LLM-controlled loop
    └─ Workflow                              <- chained, named steps

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python -m examples.demo "Analyze examples/data/sample.csv and report the top 3 categories by total spend"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_demo.agents.claude import AgentRunner
from claude_demo.core.hooks import (
    HookEngine,
    logging_complete_hook,
    logging_post_hook,
    logging_pre_hook,
    safety_pre_hook,
    truncate_post_hook,
)
from claude_demo.core.permissions import Decision, PermissionPolicy
from claude_demo.sandbox import Sandbox
from claude_demo.tools import make_code_runner, make_file_tool
from claude_demo.core.workflow import Workflow

DEFAULT_PROMPT = (
    "Analyze the dataset in sample.csv (it has columns: category, amount, date). "
    "Report the top 3 categories by total spend, and the day with the highest "
    "total spend across all categories. Use the file_tool to inspect first, "
    "then code_runner to compute. Return a short summary."
)


def build_runner(input_dir: Path) -> AgentRunner:
    # Sandbox sees every file in input_dir under its cwd.
    input_files = {
        p.name: p for p in input_dir.iterdir() if p.is_file()
    }
    sandbox = Sandbox(timeout_seconds=10.0, input_files=input_files)

    tools = [
        make_code_runner(sandbox),
        make_file_tool(input_dir),
    ]

    # Hook engine: permissions first (so deny short-circuits), then safety,
    # then logging.
    permissions = PermissionPolicy(
        default=Decision.ALLOW,
        # Demo is fully autonomous — set code_runner to CONFIRM if you want
        # interactive y/N gating.
        overrides={},
    )
    hooks = (
        HookEngine()
        .add_pre(permissions.as_pre_hook())
        .add_pre(safety_pre_hook)
        .add_pre(logging_pre_hook)
        .add_post(truncate_post_hook(max_chars=8000))
        .add_post(logging_post_hook)
        .add_complete(logging_complete_hook)
    )

    return AgentRunner(tools=tools, hooks=hooks)


def main(argv: list[str]) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY before running the demo.", file=sys.stderr)
        return 2

    user_prompt = " ".join(argv[1:]).strip() or DEFAULT_PROMPT

    here = Path(__file__).resolve().parent
    input_dir = here / "data"
    if not input_dir.exists():
        print(f"ERROR: input directory not found: {input_dir}", file=sys.stderr)
        return 1

    runner = build_runner(input_dir)

    # The workflow has one real step today: run the agent. The shape is
    # there so future steps (retrieval, post-processing, eval) drop in.
    workflow = (
        Workflow(name="analyze_dataset")
        .step(
            name="agent_analysis",
            action=lambda ctx: runner.run(ctx["prompt"]),
            output_key="run_state",
        )
    )

    run = workflow.run({"prompt": user_prompt})

    print()
    print("Workflow steps:")
    for name, status in run.step_log:
        print(f"  - {name}: {status}")

    state = run.context.get("run_state")
    if state is not None:
        print()
        print(state.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
