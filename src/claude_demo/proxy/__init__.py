"""Egress proxy layer — credential injection and host allowlisting."""

from .credential import CredentialProxy, ProxyAuditEntry, ProxyResult

__all__ = ["CredentialProxy", "ProxyAuditEntry", "ProxyResult"]
