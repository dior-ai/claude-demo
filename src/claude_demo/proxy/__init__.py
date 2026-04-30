"""Egress proxy layer — credential injection and host allowlisting."""

from .browser import BrowserAuditEntry, BrowserProxy, BrowserProxyResult
from .credential import CredentialProxy, ProxyAuditEntry, ProxyResult

__all__ = [
    "BrowserAuditEntry",
    "BrowserProxy",
    "BrowserProxyResult",
    "CredentialProxy",
    "ProxyAuditEntry",
    "ProxyResult",
]
