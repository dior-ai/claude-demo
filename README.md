# Hook-Driven Secure Agent Runner

A minimal but real proof-of-concept of an enterprise AI automation runtime.
An agent (scripted or LLM-driven) plans tasks, hooks intercept and gate
every tool call, and tools execute through a credential-injection proxy
or inside an isolated Python sandbox.

The demo is grounded in ideas from these open-source projects:

- **anthropic-experimental/sandbox-runtime, zerobox** — OS-style isolation, deny-by-default, credential proxy
- **claude-code-hooks** — lifecycle interception (PreToolUse, PostToolUse, TaskComplete)
- **claudeclaw / openclaw** — LLM-controlled agent loop
- **meridian** — workflow chaining and state tracking

## TL;DR — run the no-key demo in 30 seconds

```bash
python -m examples.cred_safety_demo
```

No API key, no external network, ~70 ms end-to-end. The script:

1. fetches widgets from `api.local` with `Authorization: Bearer ${WIDGETS_TOKEN}` — proxy substitutes the real secret because the host is on the allowlist
2. POSTs a report (with the placeholder in BOTH headers and body) — proxy substitutes again
3. attempts to exfil to `evil.local/leak` — proxy **blocks before substitution**, so the secret is never read

It ends with an explicit leakage check that asserts the real token value never appears in the run report or audit URLs.

```
--- secret leakage check ---
  real secret appears in run report:   False
  real secret appears in audit URLs:   False
  exfil attempts blocked:              1
  RESULT: no leak. Agent only ever held ${WIDGETS_TOKEN} placeholder.
```

## Architecture

```
User input
    │
    ▼
┌───────────────────────────────────────────────────────────────┐
│ Agent loop                                                    │
│   ScriptedRunner (no key)  OR  AgentRunner (Claude)           │
│                                                               │
│   plan ─► tool selection ─► hook gate ─► tool ─► result       │
│     ▲                          │           │                  │
│     └──────────── state ◄──────┴───────────┘                  │
└───────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
              ┌────────────────────────────────┐
              │ Tool execution surface          │
              │  http_request → credential proxy│
              │  code_runner  → subprocess      │
              │  file_tool    → input dir       │
              └────────────────────────────────┘
```

Every component is a separate module:

| Module             | Role                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------- |
| `scripted.py`      | **Key-free** runner. Drives a hand-written `ScriptedPlan` through the same hook engine. |
| `agent.py`         | Claude-driven runner (manual tool-use loop). Same hooks, same state. Optional.          |
| `hooks.py`         | `PreToolUse`, `PostToolUse`, `TaskComplete`. Hooks can block / rewrite / log.           |
| `permissions.py`   | Per-tool permission policy (`allow`, `deny`, `confirm`).                                |
| `cred_proxy.py`    | Credential injection proxy: host allowlist + `${SECRET}` substitution + audit log.      |
| `http_tool.py`     | `http_request` tool — only network-egress path; always routes through the proxy.        |
| `sandbox.py`       | Ephemeral subprocess executor. Isolated cwd, scrubbed env, timeout.                     |
| `tools.py`         | `code_runner` and `file_tool` definitions.                                              |
| `state.py`         | Append-only step / tool-call / token-usage record.                                      |
| `workflow.py`      | Ordered named steps that thread a shared context — chains stages.                       |

## Two runners, same engine

| Runner            | When to use                                                                  | API key? |
| ----------------- | ---------------------------------------------------------------------------- | -------- |
| `ScriptedRunner`  | Demos, tests, CI, library use where your own code drives the trajectory.     | No       |
| `AgentRunner`     | Production-style LLM-driven loop (Claude `claude-opus-4-7`).                 | Yes      |

Both share the hook engine, tools, permissions, sandbox, and state. The hook-driven safety story does not depend on whether an LLM is in the loop.

## What this demo proves

- **The agent never holds the real secret** — only `${SECRET_NAME}` placeholders. The substitution is conditional on the proxy's allowlist check, which runs BEFORE any secret is read.
- **Hooks actively modify flow** — `PreToolUse` can block (or rewrite) tool inputs; `PostToolUse` can transform results; `TaskComplete` finalizes.
- **Sandbox is real** — Python runs in a subprocess in an ephemeral cwd, with a kill timeout, scrubbed env, and copy-not-link input file staging.
- **Tools are real** — `http_request` makes actual HTTP calls; `code_runner` actually executes Python; `file_tool` actually reads files (with path-traversal rejection).
- **State is auditable** — every request, allowed or blocked, lands in `proxy.audit_log` and `RunState`. The "secret leakage check" stanza is a one-line proof that the agent context never touched the real value.

## Run

```bash
# Key-free credential-safety demo (recommended)
python -m examples.cred_safety_demo

# Tests (41 cases, ~2.5s on Python 3.13)
python -m unittest discover tests

# Optional: Claude-driven dataset analysis (needs ANTHROPIC_API_KEY)
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m examples.demo
```

The cred-safety demo and the test suite have **zero external dependencies** (stdlib only). `requirements.txt` is needed only for the optional Claude-driven path.

## Layout

```
src/
  agent.py          AgentRunner (Claude-driven)
  scripted.py       ScriptedRunner (key-free)
  hooks.py          HookEngine + PreToolUse / PostToolUse / TaskComplete
  permissions.py    PermissionPolicy (allow / confirm / deny)
  cred_proxy.py     CredentialProxy (allowlist + ${SECRET} substitution + audit)
  http_tool.py      http_request tool (only egress path)
  sandbox.py        Ephemeral subprocess executor
  tools.py          code_runner + file_tool
  state.py          Append-only RunState
  workflow.py       Ordered named pipeline steps
examples/
  cred_safety_demo.py    Headline demo (no key)
  mock_backend.py        Tiny in-process HTTP server for the demo
  demo.py                Optional Claude-driven analysis demo
  data/sample.csv
tests/
  test_cred_proxy.py
  test_scripted.py
  test_hooks.py
  test_permissions.py
  test_sandbox.py
  test_tools.py
  test_workflow.py
```
