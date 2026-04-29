# Architecture

A short tour of the runtime, suitable for engineers reading this in a
30-minute review window. For threat modelling, see
[threat-model.md](threat-model.md).

## One-paragraph summary

The substrate is a **hook-driven agent runtime** with three trust
boundaries: **policy** (what tools can run), **proxy** (what egress
is allowed and which secrets are substituted), and **sandbox** (where
arbitrary code executes). Every tool call passes through every
boundary, every event is logged, and every tool decision is auditable.
The runner that drives the trajectory is interchangeable: a scripted
plan for deterministic / keyless operation, an LLM (Claude) for open
agentic flows. They share the engine.

## Diagram

```
                 ┌───────────────────────────────┐
 user input  ──► │  agent runner                 │
                 │  (scripted | claude)          │
                 └────────────┬──────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────┐
                │  HookEngine                       │
                │  ─ PreToolUse  ─ PostToolUse      │
                │  ─ TaskComplete                   │
                └────────────┬──────────────────────┘
                             │  fires per tool call
                             ▼
   ┌───────────────────────────────────────────────────┐
   │  Hook chain (registered in order)                 │
   │                                                   │
   │  1. policy.evaluator.as_pre_hook(Policy)          │   <-- T1
   │     verdict: allow | confirm | deny               │       PreToolUse
   │     code-pattern check on code_runner             │
   │                                                   │
   │  2. audit.AuditLog.as_pre_hook                    │   <-- T1
   │     emits pre_tool_use record (corr_id + step)    │
   │                                                   │
   │  3. (other site-specific PreHooks)                │
   │                                                   │
   │  ── tool dispatch ──                              │
   │                                                   │
   │  4. truncate_post_hook                            │
   │  5. audit.AuditLog.as_post_hook                   │   <-- T1
   │  6. (other site-specific PostHooks)               │
   └───────────────┬───────────────────────────────────┘
                   │
                   ▼
        ┌─────────────────────────┐
        │  Tool dispatch          │
        │  (one of)               │
        ├─────────────────────────┤
        │  http_request ──► CredentialProxy (allowlist + ${SECRET})
        │  code_runner  ──► Sandbox (subprocess; T2 adds bubblewrap)
        │  file_tool    ──► fixed input dir, path-traversal guarded
        │  mcp__*       ──► MCPClient ──► StubMCPServer (T2: real MCP)
        └─────────────────────────┘
                   │
                   ▼
        runs/<run_id>.jsonl  ─► audit-viewer / SIEM
```

## Modules

| Module                       | Role                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| `claude_demo.core.hooks`     | `HookEngine` + `PreToolUseEvent` / `PostToolUseEvent` / `TaskComplete`. |
| `claude_demo.core.state`     | Append-only `RunState`: step list, tool calls, durations, tokens.    |
| `claude_demo.core.permissions` | `Decision` enum + low-level `PermissionPolicy` (used by policy hook). |
| `claude_demo.core.workflow`  | Ordered named pipeline. Threads a shared context dict between steps. |
| `claude_demo.policy.*`       | YAML loader + evaluator. Produces a `PreToolUse` hook from `Policy`. |
| `claude_demo.audit.log`      | Append-only JSONL audit log + hook adapters.                         |
| `claude_demo.proxy.credential` | Egress allowlist + `${SECRET}` substitution + per-request audit.    |
| `claude_demo.sandbox`        | Subprocess executor with stripped env + timeout.                     |
| `claude_demo.tools.{base,code,file,http}` | Concrete tool implementations.                          |
| `claude_demo.mcp.*`          | JSON-RPC client + in-process stub server + `Tool` adapter.           |
| `claude_demo.agents.scripted` | Key-free runner. Consumes a hand-written `ScriptedPlan`.            |
| `claude_demo.agents.claude`  | Optional LLM-driven runner (manual Claude tool-use loop).            |
| `claude_demo.ui.console`     | `rich` renderers for the CLI surface (plan, trace, summary, leak).   |
| `claude_demo.cli.*`          | `python -m claude_demo run <example>` and `audit view <path>`.       |

## Data flow for one tool call

This is the path through the runtime for a single `http_request` call
in the headline demo:

1. **Runner** picks the next call from the `ScriptedPlan` (or, in the
   LLM path, parses the next `tool_use` block from a Claude response).
2. **HookEngine.fire_pre()** runs the registered pre-hooks in order:
   - policy hook checks tool-level decision; raises `ToolBlocked` on
     deny / declined-confirm. If the tool is `code_runner`, scans the
     `code` arg for forbidden patterns.
   - audit hook emits a `pre_tool_use` JSONL record with a fresh
     `correlation_id` and the next `step_id`.
3. If not blocked, **the tool runs**. For `http_request`, the tool
   delegates to `CredentialProxy.request()`, which:
   - rejects the call if the host is not on `Policy.http_allowlist`
     (records a `ProxyAuditEntry` with `blocked=True`);
   - otherwise substitutes `${SECRET_NAME}` placeholders in headers
     and body using the host-side secrets table;
   - applies `host_overrides` to point logical hostnames at a local
     listener (so `api.local` → `127.0.0.1:9101` for the demo);
   - forwards via `urllib`, captures the response, returns a
     `ProxyResult`.
4. **HookEngine.fire_post()** runs post-hooks:
   - truncate clamps long results;
   - audit hook emits `post_tool_use` with the same `correlation_id`,
     so a SIEM can pair pre/post records.
5. The runner records the outcome in `RunState`. Loop until the plan
   is exhausted (scripted) or Claude returns `end_turn` (LLM).
6. **TaskComplete** fires once at the end. Audit emits a final
   `task_complete` record.

## Trust boundaries

Three layers, each independently bypass-resistant. Defence in depth:

| Layer    | Defends against                                            |
| -------- | ---------------------------------------------------------- |
| Policy   | Wrong tool getting picked at all; dangerous code patterns. |
| Proxy    | Egress to off-allowlist hosts; secret leakage to anywhere. |
| Sandbox  | Local code executing dangerous syscalls (subset; T2 adds OS-native isolation). |

A bug in any single layer doesn't escape the runtime — an attacker has
to defeat all three. The audit log makes the bypass attempts visible
even when they don't succeed.

## Extending

- **A new tool** — implement a factory returning a `Tool`, register it
  with the runner. The hook engine, audit, and policy automatically
  apply.
- **A new policy profile** — add a YAML file under `policies/`, load it
  with `--policy NAME`. No code changes.
- **A new agent runner** — implement the same interface as
  `ScriptedRunner` (consume a plan / LLM, dispatch tools through
  `HookEngine`). The scripted and Claude runners are existence proofs.
- **A new sandbox backend (T2)** — implement the `Sandbox` protocol
  (`run_python(code) -> SandboxResult`). The current subprocess
  backend is one example; a `bubblewrap.py` backend slots in
  identically on Linux.
- **A real MCP server (T2/T3)** — replace the in-process transport in
  `MCPClient` with a stdio or HTTP transport. The client API stays the
  same.
