"""Append-only JSONL audit log + hook adapters."""

from .log import AuditEvent, AuditLog

__all__ = ["AuditEvent", "AuditLog"]
