"""Browser-egress allowlist gate.

The same ``policy.http_allowlist`` set that gates ``http_tool`` egress
is applied to a different egress channel: a browser. The browser tool
calls ``allow_url()`` before navigation, sub-resource loads, and form
submits; ``${SECRET}`` placeholders inside form values pass through
``substitute()`` so the agent never holds the real value.

This is a separate module from ``proxy.credential`` on purpose: each
egress channel has its own audit trail. They SHARE the source of
truth (the policy's allowlist) but are otherwise independent — if a
future channel is added (e.g., a gRPC tool), it gets its own proxy
in the same shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

# Same placeholder grammar as the credential proxy: ${SCREAMING_NAME}.
PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

BrowserRequestKind = Literal["navigate", "subresource", "submit"]


@dataclass
class BrowserAuditEntry:
    """One audit-log record for a browser-driven egress attempt."""

    kind: BrowserRequestKind
    url: str
    host: str
    blocked: bool
    block_reason: str = ""


@dataclass
class BrowserProxyResult:
    """Outcome of an ``allow_url`` check."""

    allowed: bool
    block_reason: str = ""


@dataclass
class BrowserProxy:
    """Allowlist gate + secret substitution for the browser channel.

    Construct with the set of allowed hostnames and the secrets table.
    Every navigation, sub-resource fetch, and form submit the browser
    is about to perform calls ``allow_url`` first; off-allowlist hosts
    are refused before the browser issues a single byte.
    """

    allowed_hosts: set[str]
    secrets: dict[str, str] = field(default_factory=dict)
    audit_log: list[BrowserAuditEntry] = field(default_factory=list)

    def allow_url(self, url: str, kind: BrowserRequestKind) -> BrowserProxyResult:
        host = urlparse(url).hostname or ""
        if host not in self.allowed_hosts:
            reason = f"browser proxy denied egress to '{host}' (not on allowlist)"
            self.audit_log.append(
                BrowserAuditEntry(
                    kind=kind, url=url, host=host, blocked=True, block_reason=reason
                )
            )
            return BrowserProxyResult(allowed=False, block_reason=reason)
        self.audit_log.append(
            BrowserAuditEntry(kind=kind, url=url, host=host, blocked=False)
        )
        return BrowserProxyResult(allowed=True)

    def substitute(self, value: str) -> tuple[str, list[str]]:
        """Replace ``${NAME}`` placeholders with real secrets.

        Returns the substituted string and the list of placeholder
        names that were resolved. Unknown names are left as-is so a
        misconfigured run fails loudly rather than silently dropping
        an empty string into a form field.
        """
        used: list[str] = []

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self.secrets:
                used.append(name)
                return self.secrets[name]
            return match.group(0)

        return PLACEHOLDER_RE.sub(replace, value), used
