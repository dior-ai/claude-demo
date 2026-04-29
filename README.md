# Bastion

> **Hook-driven secure agent runtime — the substrate for enterprise AI automation.**

A platform-shaped substrate for enterprise AI automation: lifecycle
hooks gate every tool call, a credential-injection proxy keeps secrets
out of the agent context, every event lands in a JSON Lines audit log,
and policy-as-code controls what tools can run. The agent driving the
trajectory is interchangeable — a deterministic scripted plan today, a
Claude-driven loop when an `ANTHROPIC_API_KEY` is available.

**Status:** T1 (substrate) shipped — see *Roadmap* below for T2 / T3.

## TL;DR — two steps to a visual test

No API key, no external network. The launcher script auto-finds Python,
auto-installs the package on first run, and prints the framed demo:

```bash
# Step 1 — open Git Bash in this directory.
# Step 2:
./demo.sh
```

Other modes:

```bash
./demo.sh prod-restricted   # same plan, prod-restricted policy
./demo.sh gov-airgapped     # same plan, air-gapped policy (every step blocked)
./demo.sh openai            # OpenAI-driven demo with a prompt-injection test
./demo.sh audit             # pretty-print the most recent audit log
./demo.sh tests             # run the unit test suite (83 cases)
```

The `openai` mode needs `OPENAI_API_KEY`. GPT picks the trajectory
itself, with an embedded prompt-injection telling it to exfiltrate the
secret to a hostile host — the substrate must hold under non-deterministic,
adversarial agent behaviour. See *Provider-agnostic agent driver* below.

If you'd rather run the underlying commands by hand:

```bash
pip install -e .
python -m claude_demo run cred-safety
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

## Provider-agnostic agent driver

The substrate (hooks + policy + proxy + audit + sandbox) is unchanged
regardless of which LLM is in the seat. Three drivers ship today:

- `ScriptedRunner` — deterministic plan, no API key. Headline demo.
- `OpenAIAgentRunner` — `chat.completions` tool-use loop, needs `OPENAI_API_KEY`.
- `AgentRunner` (Claude) — `messages.create` tool-use loop, needs `ANTHROPIC_API_KEY`.

All three call into the same `HookEngine`. Proof of agnosticism is in
the test suite: 83 tests pass without either LLM key, including the
runner tests which use fake clients to assert each driver's loop fires
the same `PreToolUse` / `PostToolUse` / `TaskComplete` hooks in the
same order. Swapping the model is one constructor call; the safety
story does not change.

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

## Roadmap

**T1 — substrate (shipped, this commit)**

Policy-as-code, JSONL audit, credential proxy, MCP integration shape,
scripted runtime, rich CLI, audit viewer, three policy profiles
(`default` / `prod-restricted` / `gov-airgapped`), architecture +
threat-model docs. 76 unit tests, ~1 second end-to-end demo, no API
key required.

**T2 — integrations + adversarial proof (~1 week)**

Bubblewrap + Seatbelt sandbox backends, Playwright-based browser tool
gated through the same hooks, sub-agent / multi-agent primitive, real
MCP transport (stdio / HTTP). Plus a **red-team suite** — 20+
adversarial scenarios run against the substrate, with the audit log
proving every one is blocked. That output shifts the demo from "looks
correct" to "verifiably survives attack".

**T3 — production posture (~1 month)**

OpenTelemetry traces, mTLS to MCP servers, multi-tenancy + RBAC,
SIEM-shipper recipe (Fluent Bit / Vector → Splunk / Elastic),
Dockerfile + compose stack, 24-hour soak test, external pen-test
report.

## How T1 maps to enterprise platform pillars

| Pillar                       | T1 status | Where it lives                                                           |
| ---------------------------- | --------- | ------------------------------------------------------------------------ |
| MCP / A2A integrations       | ✅ shape  | `src/claude_demo/mcp/` (real JSON-RPC, transport-swappable)              |
| LLM tool orchestration       | ✅        | `agents/scripted.py` + `agents/claude.py` share the hook engine          |
| Browser automation           | ❌ T2     | `examples/browser_research/` placeholder                                 |
| Code-execution sandboxes     | ✅ + T2   | `sandbox.py` (subprocess); bubblewrap / Seatbelt land in T2              |
| Multi-agent workflows        | △         | `core/workflow.py` ships; sub-agent primitive in T2                      |
| Security & compliance        | ✅        | `policy/`, `audit/`, `proxy/`, `docs/threat-model.md`                    |
| Government-scale (air-gap)   | ✅        | `policies/gov-airgapped.yaml` — default-deny, no egress, no code exec    |
| Fundable enterprise feel     | ✅        | `pyproject.toml`, `docs/architecture.md`, three deployment profiles, CLI |
