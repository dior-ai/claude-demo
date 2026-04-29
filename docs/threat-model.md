# Threat model

A short STRIDE-shaped pass over the runtime. Use this to decide what to
trust, what to gate, and what to audit. For the architecture overview,
see [architecture.md](architecture.md).

## Assumptions

- The agent (LLM or scripted plan) is **untrusted**. Its outputs may be
  attacker-controlled (prompt injection, malicious training data,
  compromised plan source).
- The runtime host is **trusted**. Operating-system-level compromise
  of the host is out of scope for this demo.
- The mock backend in `examples/cred_safety/mock_backend.py` is a stand
  in for any internal API. Real internal APIs would have their own
  authn/authz on top of the proxy substitution.
- Production deployments run a hardened sandbox backend (bubblewrap +
  seccomp on Linux, Seatbelt on macOS — landing in T2). The current
  subprocess sandbox is *demo-grade*: it strips env, isolates cwd, and
  applies a timeout, but does not block syscalls.

## Assets

| Asset                          | Why it matters                                          |
| ------------------------------ | ------------------------------------------------------- |
| Secrets table (proxy)          | Loss = total credential compromise                      |
| Audit log                      | Compliance evidence; tamper changes attestation story   |
| Internal APIs (egress targets) | Direct compromise vector for the org                    |
| Host filesystem                | Host-side data; sandbox should not be able to read it   |

## STRIDE per component

### Agent runner (Scripted / Claude)

| Threat                | Vector                                                      | Mitigation                                                                                      |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **S**poofing          | Prompt-injected agent claims it's a different actor.        | Audit log records `actor` from the runtime, not the agent. `actor` is set by the engine.        |
| **T**ampering         | Agent rewrites its own conversation history.                | Conversation lives in process memory; agent has no API to mutate it. (LLM cannot edit prior turns.) |
| **R**epudiation       | Agent denies issuing a tool call.                           | Every PreToolUse fires an audit record before the tool runs. Append-only.                       |
| **I**nformation disc. | Agent prompts a secret out of the proxy.                    | Proxy gates on host first, then substitutes — agent cannot induce substitution to off-allowlist hosts. |
| **D**oS              | Agent loops forever calling tools.                          | `AgentRunner` enforces `max_steps`. Sandbox enforces per-call timeout.                          |
| **E**oP              | Agent escapes runtime to host.                              | Sandbox + tool surface boundary. T2 hardens the sandbox; today's subprocess sandbox is demo-grade. |

### Policy engine

| Threat | Vector                                              | Mitigation                                                               |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------ |
| T      | Operator edits the policy file mid-run.             | Policy is loaded once at run start; mutating the file mid-run has no effect on the running run. |
| I      | Malformed policy silently allows everything.        | `PolicyError` raised on unknown decision strings, missing keys, unsupported version. Fails closed. |
| E      | Forbidden code patterns bypassed by encoding.       | Acknowledged limitation: substring match is demo-grade. Production wants AST analysis or syscall filtering (sandbox layer). |

### Credential proxy

| Threat | Vector                                              | Mitigation                                                               |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------ |
| S      | Agent claims a `${SECRET}` reference for a host it's not authorized to reach. | Allowlist check runs **before** secrets table is read. No secret leaks via off-allowlist hosts. |
| T      | Agent injects a `${...}` into a URL path or query.  | Substitution only occurs in headers / body, not in URL components. URL host comes straight from the agent. |
| R      | Forwarded request denies it ever happened.          | Every request, allowed or blocked, is added to `proxy.audit_log` AND `AuditLog` JSONL. |
| I      | Header values containing real secrets logged verbatim. | Audit records secret *names* (`secrets_used: [WIDGETS_TOKEN]`), never values. |
| D      | Slow remote stalls the runtime.                     | `timeout_seconds` on every `urlopen` call.                               |
| E      | Substituted secret leaks back into the response.    | Response body returned to the agent; production would scrub responses. (Open issue, not addressed in T1.) |

### Sandbox (subprocess)

| Threat | Vector                                              | Mitigation                                                               |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------ |
| T      | Sandboxed code mutates input files visible to host. | Inputs staged with `shutil.copy2`, not symlinks. Writes affect only the temp dir. |
| I      | Sandboxed code reads host env vars or files.        | `_safe_env()` strips PATH/HOME/etc. Cwd is a fresh temp dir.            |
| D      | Infinite loop / fork bomb.                          | `timeout_seconds` kills the subprocess.                                 |
| E      | Sandboxed code calls `socket`, `subprocess`, `os.system`. | Defence in depth: policy `forbidden_code_patterns` rejects them at hook time; T2 adds bubblewrap network/PID isolation. |

### Audit log

| Threat | Vector                                              | Mitigation                                                               |
| ------ | --------------------------------------------------- | ------------------------------------------------------------------------ |
| T      | Agent rewrites past audit entries.                  | File is opened in append mode; agent has no API to write to it.          |
| R      | Operator claims an action wasn't taken.             | Every entry has `ts`, `run_id`, `correlation_id`, `actor`. Pre/post are paired. |
| I      | Sensitive payloads logged verbatim.                 | Audit records key *names* (`input_keys: ["url", "headers"]`), not values. Tool-level results are size-only. |

## Known limitations (out of scope for T1)

- **No syscall sandbox.** The subprocess sandbox stops at process
  isolation. T2 lands bubblewrap (Linux) and Seatbelt (macOS) backends
  with seccomp/Seatbelt profiles. Until then, the safety story relies
  on policy + proxy.
- **No response body scrubbing.** If a backend echoes a substituted
  secret back, the agent sees it. Production needs an outbound scrubber.
- **No mTLS to MCP servers.** The stub is in-process; T2 swaps in
  stdio/HTTP transports with mTLS.
- **Audit log is per-process file.** A dedicated log shipper (Fluent
  Bit, Vector) is expected to tail and forward to a SIEM. The
  on-disk format is the contract; the shipper is operational.
- **No rate-limiting at the proxy.** A burst of allowed requests is
  not throttled. Adding a token bucket per (run, host) is a one-file
  change in `cred_proxy.py`; deferred to T2.
- **Audit shape may evolve.** New event types are append-only; if
  payload fields are renamed, downstream consumers see the old name in
  historical records and the new name going forward.
