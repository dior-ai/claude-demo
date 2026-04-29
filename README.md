# Hook-Driven Secure Agent Runtime

A platform-shaped substrate for enterprise AI automation: lifecycle
hooks gate every tool call, a credential-injection proxy keeps secrets
out of the agent context, every event lands in a JSON Lines audit log,
and policy-as-code controls what tools can run. The agent driving the
trajectory is interchangeable — a deterministic scripted plan today, a
Claude-driven loop when an `ANTHROPIC_API_KEY` is available.

The shape is grounded in the open-source projects this is meant to
slot underneath: hooks from `claude-code-hooks`, OS-style isolation
from `sandbox-runtime`, deny-by-default + credential proxy from
`zerobox`, workflow shape from `meridian`, agent-loop ergonomics from
`claudeclaw / openclaw`.

## TL;DR — run the headline demo in 30 seconds

No API key, no external network. Stdlib + `rich` + `PyYAML` only.

```bash
# Install (editable; pulls in rich + PyYAML)
pip install -e .

# Run
python -m claude_demo run cred-safety

# Inspect the JSONL audit it wrote
python -m claude_demo audit view runs/<run_id>.jsonl
```

The run prints a framed plan, a per-step trace (with policy verdict +
duration + status), an audit-summary panel, and a leak-check panel
asserting the real secret value never appeared in the run report or
audit URLs:

```
─────────── CREDENTIAL-SAFETY DEMO ───────────

┌──────────── Plan ────────────┐
│ run_id  run_b4f970ef         │
│ policy  default              │
│ audit   runs/run_b4f970ef.jsonl
│ Step 1  http_request  api.local/widgets
│ Step 2  http_request  api.local/report
│ Step 3  http_request  evil.local/leak
└──────────────────────────────┘

[1]  http_request  -> policy.allow  OK   (13ms)
[2]  http_request  -> policy.allow  OK   (1ms)
[3]  http_request  -> policy.allow  ERR  (0ms)
      ERROR: proxy denied egress to 'evil.local' (not on allowlist)

┌── Audit summary ──┐    ┌── Leak check — No leak ──────────────┐
│ events       3    │    │ real secret in run report  no        │
│ allowed      2    │    │ real secret in audit URLs  no        │
│ blocked      1    │    │ exfil attempts blocked     1         │
│ secrets used WIDGETS_TOKEN
│ audit log    runs/run_b4f970ef.jsonl
└───────────────────┘    └──────────────────────────────────────┘
```

Swap policy at the CLI: `python -m claude_demo run cred-safety
--policy gov-airgapped` (the gov-airgapped profile denies all egress
and refuses tools by default; the same demo plan now blocks every
step).

## Architecture in one diagram

```
                  ┌──────────────────────────────┐
 user input  ──►  │  agent runner                │
                  │  scripted (no key) | claude  │
                  └────────────┬─────────────────┘
                               │
                               ▼
                  ┌──────────────────────────────┐
                  │  HookEngine                  │
                  │  PreToolUse / PostToolUse /  │
                  │  TaskComplete                │
                  └────────────┬─────────────────┘
                               │
   ┌──────────────────────────────────────────────────┐
   │  Hook chain (registered in order)                │
   │   1. policy.evaluator        allow/deny/confirm  │
   │   2. audit.AuditLog          JSONL pre / post    │
   │   3. (other PreHooks)                            │
   │   ─ tool dispatch ─                              │
   │   4. truncate / log / etc.                       │
   │   5. audit.AuditLog          paired post record  │
   └──────────────┬───────────────────────────────────┘
                  ▼
       ┌──────────────────────────────┐
       │  http_request → CredentialProxy
       │  code_runner  → Sandbox
       │  file_tool    → input dir
       │  mcp__*       → MCPClient → StubMCPServer
       └──────────────────────────────┘
                  │
                  ▼
       runs/<run_id>.jsonl  →  audit-viewer / SIEM
```

Long form, with module table + data flow + threat model:
- [docs/architecture.md](docs/architecture.md)
- [docs/threat-model.md](docs/threat-model.md)

## Three policy profiles, one demo, three behaviours

| Profile             | Default       | code_runner | bash    | egress allowlist           |
| ------------------- | ------------- | ----------- | ------- | -------------------------- |
| `default.yaml`      | allow         | allow       | allow   | `api.local`                |
| `prod-restricted.yaml` | allow      | confirm     | deny    | `internal-api.corp`        |
| `gov-airgapped.yaml`   | **deny**   | deny        | deny    | (empty)                    |

A YAML swap is a behaviour swap. The `CredentialProxy` reads its
allowlist directly from `policy.http_allowlist`, so the same demo plan
gets blocked under `gov-airgapped` — proving the policy plumbing
actually changes runtime decisions, not just text on screen.

## Modules

| Path                              | Role                                                   |
| --------------------------------- | ------------------------------------------------------ |
| `claude_demo.core.hooks`          | `HookEngine` + lifecycle events.                       |
| `claude_demo.core.state`          | Append-only `RunState`.                                |
| `claude_demo.core.workflow`       | Ordered named pipeline that threads a context dict.    |
| `claude_demo.core.permissions`    | `Decision` enum + low-level `PermissionPolicy`.        |
| `claude_demo.policy.*`            | YAML loader + evaluator → PreToolUse hook.             |
| `claude_demo.audit.log`           | Append-only JSONL audit log + hook adapters.           |
| `claude_demo.proxy.credential`    | Egress allowlist + `${SECRET}` substitution + audit.   |
| `claude_demo.sandbox`             | Subprocess sandbox (T2 adds bubblewrap / Seatbelt).    |
| `claude_demo.tools.{base,code,file,http}` | Concrete tool implementations.                |
| `claude_demo.mcp.*`               | JSON-RPC client + in-process stub server + adapter.    |
| `claude_demo.agents.scripted`     | Key-free runner. `ScriptedPlan` + `ScriptedRunner`.    |
| `claude_demo.agents.claude`       | Optional Claude-driven runner (manual tool-use loop).  |
| `claude_demo.ui.console`          | Rich renderers for the CLI.                            |
| `claude_demo.cli.*`               | `python -m claude_demo` entry points.                  |

## Demos

| Slug                | Status   | What it proves                                                              |
| ------------------- | -------- | --------------------------------------------------------------------------- |
| `cred-safety`       | T1 ✅    | Hooks + policy + cred proxy + audit + leak check.                           |
| `browser-research`  | T2 (planned) | Browser automation tool gated by hooks + policy + audit.                |
| `multi-agent-mcp`   | T2 (planned) | MCP integration + sub-agent handoff + workflow chaining.                |

## Run

```bash
# Headline keyless demo
python -m claude_demo run cred-safety
python -m claude_demo run cred-safety --policy prod-restricted
python -m claude_demo run cred-safety --policy gov-airgapped

# Inspect an audit log
python -m claude_demo audit view runs/<run_id>.jsonl
python -m claude_demo audit view runs/<run_id>.jsonl --filter-event policy_decision

# Tests (76 cases, ~1.5s on Python 3.13)
python -m unittest discover tests

# Optional: Claude-driven dataset analyzer (needs ANTHROPIC_API_KEY)
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=sk-ant-...
python -m examples.optional_claude_demo
```

## Layout

```
docs/
  architecture.md
  threat-model.md
policies/
  default.yaml
  prod-restricted.yaml
  gov-airgapped.yaml
src/claude_demo/
  core/        hooks, state, permissions, workflow
  sandbox.py   ephemeral subprocess executor
  policy/      schema + YAML loader + evaluator
  audit/       JSONL log + hook adapters
  proxy/       credential injection proxy
  tools/       base / code / file / http
  mcp/         client + stub_server + adapter
  agents/      scripted, claude
  ui/          rich renderers
  cli/         python -m claude_demo entry points
examples/
  cred_safety/         headline T1 demo
  browser_research/    T2 placeholder
  multi_agent_mcp/     T2 placeholder
  optional_claude_demo.py
runs/                  gitignored; per-run JSONL audit logs
tests/                 76 cases, stdlib unittest
```

## What ships in T1 vs. what's deferred

T1 (this commit) ships the substrate: policy, audit, MCP shape,
credential proxy, scripted runtime, rich CLI, audit viewer, three
policy profiles, architecture + threat-model docs.

T2 plans (next round, when the demo lands well): bubblewrap +
Seatbelt sandbox backends, browser automation via Playwright,
sub-agent / multi-agent primitive, real MCP server transport.

T3 (hardening): OpenTelemetry hook stubs, Dockerfile + compose,
audit-viewer TUI on `textual`, SIEM-shipper recipe.
