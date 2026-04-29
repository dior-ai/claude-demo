"""Credential injection proxy.

The pattern (borrowed from zerobox): secrets never enter the agent's
context or the sandbox. The agent uses placeholder strings like
${WIDGETS_TOKEN} in headers and bodies; the proxy substitutes the real
value at request time, but ONLY if the target host is on the allowlist.
Off-allowlist hosts are blocked BEFORE substitution — so a leaked or
compromised agent cannot exfiltrate a secret it never had.

This implementation is in-process: tools call ``proxy.request(...)``
directly. A production version would expose this on a localhost HTTP
port and route sandbox traffic through HTTP_PROXY / HTTPS_PROXY env
vars; the gating logic stays the same.

Three properties matter for the demo:

  1. Host allowlist is checked FIRST. If denied, the secrets dict is
     never read. The audit log records the attempt.
  2. ${NAME} placeholders are recognised in header values and bodies.
     Unknown names are left as-is — never silently dropped.
  3. Every request, allowed or blocked, lands in audit_log. That log
     is the deliverable evidence: "this is what the agent tried to do
     and what we let through."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


@dataclass
class ProxyResult:
    """Outcome of a proxied request."""

    status: int
    body: str
    blocked: bool = False
    block_reason: str = ""
    # Names of secrets that were actually substituted. The values never
    # appear here — only the names — so this is safe to surface in logs.
    substitutions: list[str] = field(default_factory=list)


@dataclass
class ProxyAuditEntry:
    """One audit-log record. Stable shape for assertions in tests + reports."""

    method: str
    url: str
    host: str
    blocked: bool
    block_reason: str = ""
    status: int = 0
    secrets_used: list[str] = field(default_factory=list)


@dataclass
class CredentialProxy:
    """In-process credential-injection proxy with host allowlist.

    Construct with the set of allowed hostnames, the secrets table, and
    optionally a host-override map for local-development DNS (so you can
    serve ``api.local`` from 127.0.0.1 without touching /etc/hosts).
    """

    allowed_hosts: set[str]
    secrets: dict[str, str]
    # Logical hostname -> (ip, port). Lets the demo use real-feeling host
    # names while pointing at a localhost mock backend.
    host_overrides: dict[str, tuple[str, int]] = field(default_factory=dict)
    timeout_seconds: float = 5.0
    audit_log: list[ProxyAuditEntry] = field(default_factory=list)

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> ProxyResult:
        method = method.upper()
        parsed = urlparse(url)
        host = parsed.hostname or ""

        # 1. Allowlist check FIRST. We must not read a single byte of the
        #    secrets table for an off-allowlist target.
        if host not in self.allowed_hosts:
            reason = f"proxy denied egress to '{host}' (not on allowlist)"
            self.audit_log.append(
                ProxyAuditEntry(
                    method=method, url=url, host=host, blocked=True, block_reason=reason
                )
            )
            return ProxyResult(status=0, body="", blocked=True, block_reason=reason)

        # 2. Substitute placeholders in headers and body.
        used: list[str] = []
        out_headers = {k: self._substitute(v, used) for k, v in (headers or {}).items()}
        out_body = self._substitute(body, used) if body is not None else None

        # 3. Apply host override so logical names point to a real listener.
        forward_url = url
        if host in self.host_overrides:
            ip, port = self.host_overrides[host]
            forward_url = urlunparse(parsed._replace(netloc=f"{ip}:{port}"))
            # Preserve the logical Host so server-side dispatch can still
            # see what the agent asked for.
            out_headers.setdefault("Host", host)

        # 4. Forward via urllib.
        req_body = out_body.encode("utf-8") if out_body else None
        req = Request(forward_url, method=method, headers=out_headers, data=req_body)
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                status = resp.status
                response_body = resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            status = exc.code
            response_body = (
                exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            )
        except URLError as exc:
            status = 0
            response_body = f"(network error: {exc.reason})"

        self.audit_log.append(
            ProxyAuditEntry(
                method=method,
                url=url,
                host=host,
                blocked=False,
                status=status,
                secrets_used=list(used),
            )
        )
        return ProxyResult(status=status, body=response_body, substitutions=used)

    def _substitute(self, text: str | None, used: list[str]) -> str:
        if text is None:
            return ""

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self.secrets:
                used.append(name)
                return self.secrets[name]
            # Unknown placeholder: leave the literal in place. The remote
            # server will reject it, which is the right outcome — a missing
            # secret should not silently become an empty string.
            return match.group(0)

        return PLACEHOLDER_RE.sub(replace, text)

    def report(self) -> str:
        """Human-readable audit report — drop straight into demo output."""
        lines = ["--- credential proxy audit ---"]
        if not self.audit_log:
            lines.append("(no requests)")
            return "\n".join(lines)
        for i, entry in enumerate(self.audit_log, start=1):
            if entry.blocked:
                lines.append(
                    f"  {i}. [BLOCKED] {entry.method} {entry.url} -> {entry.block_reason}"
                )
            else:
                secrets = ",".join(entry.secrets_used) or "none"
                lines.append(
                    f"  {i}. [{entry.status}] {entry.method} {entry.url}  secrets_used=[{secrets}]"
                )
        blocked = sum(1 for e in self.audit_log if e.blocked)
        lines.append(f"summary: {len(self.audit_log)} requests, {blocked} blocked")
        return "\n".join(lines)
